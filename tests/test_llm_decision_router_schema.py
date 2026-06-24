"""LLM Decision Router 스키마·안전장치 단위 테스트
(community-opinion-agent §3.5.3~4 / Plan FR-3.3~3.4).

핵심: 기본 OFF면 LLM 호출 0회 / invalid JSON이면 rule-based fallback /
BUY 금지 조건에서 LLM BUY를 SKIP으로 보정 / strict schema 검증.

실행:
  pytest tests/test_llm_decision_router_schema.py
  python tests/test_llm_decision_router_schema.py
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
from decision_router import (
    DecisionRouter, DecisionResult, LLMRouter, LLMDecisionResult, parse_llm_decision,
)
from wsb_signal_engine import DailyOpinionSnapshot
from universe_filter import UniverseDecision
from cost_aware_trade_filter import CostAwareTradeDecision


def _snap(**over):
    base = dict(date="2026-03-01", symbol="X", opinion_score=78.0, opinion_trend="UP",
                persistence_days=3, consensus_ratio=2.0, neutral_ratio=0.25,
                velocity_state="NORMAL", universe_tier="CORE")
    base.update(over)
    return DailyOpinionSnapshot(**base)


def _univ(allowed=True, tier="CORE", mult=1.0, reasons=None):
    return UniverseDecision("X", allowed, tier, reasons or [], 0.8, 0.8, mult)


def _cost(allowed=True, factor=1.0):
    return CostAwareTradeDecision(allowed, [], 0.7, 5.0, 7.0, factor, "ENTER")


class _Counter:
    def __init__(self, payload): self.calls = 0; self.payload = payload
    def __call__(self, prompt): self.calls += 1; return self.payload


def _decide(router, *, signal="BUY", snap=None, univ=None, cost=None,
            position=None, cash=100000.0):
    return router.decide(
        symbol="X", current_signal=signal, daily_opinion_snapshot=snap or _snap(),
        retrieved_similar_opinions=[], retrieved_low_level_reflections=[],
        retrieved_high_level_reflections=[], rsi=45.0, atr=2.0,
        market_filter_status="NORMAL", universe_decision=univ or _univ(),
        cost_filter_decision=cost or _cost(), current_position=position,
        cash=cash, equity=100000.0, risk_settings={},
    )


def _toggle(flag):
    config.COMMUNITY_LLM_ROUTER_ENABLED = flag


# --- T1: 기본(플래그 없음 + config OFF) → LLM 호출 0회 ---
def test_t1_default_no_llm_call():
    orig = config.COMMUNITY_LLM_ROUTER_ENABLED
    try:
        _toggle(False)
        fake = _Counter('{"action":"BUY","confidence":0.9}')
        router = DecisionRouter(llm_router=False, llm=LLMRouter(complete_fn=fake))
        assert router.llm_router is False        # 둘 다 OFF → 비활성
        d = _decide(router)
        assert fake.calls == 0                   # 호출 0회
        assert d.router_mode == "rule_based"
    finally:
        _toggle(orig)


# --- T2: --llm-router 플래그 단독으로 활성 (config OFF여도) ---
def test_t2_flag_alone_enables():
    orig = config.COMMUNITY_LLM_ROUTER_ENABLED
    try:
        _toggle(False)                           # config는 OFF
        fake = _Counter('{"action":"BUY","confidence":0.9,"size_factor_modifier":1.0}')
        router = DecisionRouter(llm_router=True, llm=LLMRouter(complete_fn=fake))
        assert router.llm_router is True         # 플래그만으로 활성
        d = _decide(router)
        assert fake.calls >= 1                   # LLM 실제 호출됨
        assert d.router_mode == "llm_assisted"
    finally:
        _toggle(orig)


# --- T2b: config flag 단독으로도 활성 (플래그 없이) ---
def test_t2b_config_alone_enables():
    orig = config.COMMUNITY_LLM_ROUTER_ENABLED
    try:
        _toggle(True)                            # config ON
        fake = _Counter('{"action":"HOLD","confidence":0.9}')
        router = DecisionRouter(llm_router=False, llm=LLMRouter(complete_fn=fake))
        assert router.llm_router is True
        _decide(router)
        assert fake.calls >= 1
    finally:
        _toggle(orig)


# --- T3: invalid JSON → rule-based fallback ---
def test_t3_invalid_json_fallback():
    orig = config.COMMUNITY_LLM_ROUTER_ENABLED
    try:
        _toggle(True)
        fake = _Counter("이건 JSON이 아님")
        router = DecisionRouter(llm_router=True, llm=LLMRouter(complete_fn=fake))
        d = _decide(router)
        assert fake.calls == 1                   # 호출은 됨
        assert d.router_mode == "rule_based"     # 파싱 실패 → rule 유지
        assert "llm_fallback_to_rule_based" in d.warnings
    finally:
        _toggle(orig)


# --- T4: BUY 금지 조건(neutral 상한 초과)에서 LLM BUY → SKIP 보정 ---
def test_t4_llm_buy_cannot_override_skip():
    orig = config.COMMUNITY_LLM_ROUTER_ENABLED
    try:
        _toggle(True)
        fake = _Counter('{"action":"BUY","confidence":0.95,"size_factor_modifier":1.0}')
        router = DecisionRouter(llm_router=True, llm=LLMRouter(complete_fn=fake))
        d = _decide(router, snap=_snap(neutral_ratio=config.COMMUNITY_NEUTRAL_RATIO_MAX + 0.01))   # rule SKIP
        assert d.action == "SKIP"
        assert "llm_buy_overridden_by_rule_skip" in d.warnings
    finally:
        _toggle(orig)


# --- T5: 유효 LLM DOWNSIZE → size 축소 + llm_assisted ---
def test_t5_llm_downsize_applied():
    orig = config.COMMUNITY_LLM_ROUTER_ENABLED
    try:
        _toggle(True)
        base = _decide(DecisionRouter())          # rule-based BUY
        fake = _Counter('{"action":"BUY","confidence":0.9,"size_factor_modifier":0.5,'
                        '"reasoning":"노이즈 우려로 축소"}')
        router = DecisionRouter(llm_router=True, llm=LLMRouter(complete_fn=fake))
        d = _decide(router)
        assert d.action == "BUY"
        assert d.router_mode == "llm_assisted"
        assert d.size_factor < base.size_factor
        assert "llm_assisted" in d.reason_codes
    finally:
        _toggle(orig)


# --- T6: LLM confidence 낮음 → rule-based 우선 ---
def test_t6_low_confidence_keeps_rule():
    orig = config.COMMUNITY_LLM_ROUTER_ENABLED
    try:
        _toggle(True)
        low = config.COMMUNITY_LLM_ROUTER_MIN_CONFIDENCE - 0.1
        fake = _Counter(json.dumps({"action": "SKIP", "confidence": low}))
        router = DecisionRouter(llm_router=True, llm=LLMRouter(complete_fn=fake))
        d = _decide(router)
        assert d.router_mode == "rule_based"
        assert "llm_low_confidence_kept_rule" in d.warnings
    finally:
        _toggle(orig)


# --- T7: parse_llm_decision strict schema ---
def test_t7_parse_schema():
    ok = parse_llm_decision('{"action":"HOLD","confidence":0.7}')
    assert isinstance(ok, LLMDecisionResult)
    assert ok.action == "HOLD" and ok.confidence == 0.7
    # 코드펜스 허용
    fenced = parse_llm_decision('```json\n{"action":"SELL","confidence":0.8}\n```')
    assert fenced.action == "SELL"
    # 잘못된 action → None
    assert parse_llm_decision('{"action":"LOL","confidence":0.9}') is None
    # confidence 누락(strict) → None
    orig = config.COMMUNITY_LLM_ROUTER_REQUIRE_STRICT_JSON
    try:
        config.COMMUNITY_LLM_ROUTER_REQUIRE_STRICT_JSON = True
        assert parse_llm_decision('{"action":"BUY"}') is None
    finally:
        config.COMMUNITY_LLM_ROUTER_REQUIRE_STRICT_JSON = orig
    # 완전 비JSON → None
    assert parse_llm_decision("hello") is None


# --- T8: LLM이 보유 없는데 SELL → 최종 안전장치 SKIP ---
def test_t8_llm_sell_no_position_blocked():
    orig = config.COMMUNITY_LLM_ROUTER_ENABLED
    try:
        _toggle(True)
        fake = _Counter('{"action":"SELL","confidence":0.9}')
        router = DecisionRouter(llm_router=True, llm=LLMRouter(complete_fn=fake))
        d = _decide(router, position=None)        # 보유 없음
        assert d.action == "SKIP"
        assert "safety_no_position" in d.reason_codes
    finally:
        _toggle(orig)


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\nLLM Decision Router 스키마·안전장치 테스트 - {len(tests)}건\n" + "-" * 50)
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            failed += 1
    print("-" * 50)
    print(f"{passed} passed, {failed} failed (of {len(tests)})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_standalone())
