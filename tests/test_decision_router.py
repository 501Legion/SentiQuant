"""DecisionRouter (rule-based) 단위 테스트
(community-opinion-agent §3.5 / Plan FR-3.1~3.4·테스트).

실행:
  pytest tests/test_decision_router.py
  python tests/test_decision_router.py
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
from decision_router import DecisionRouter, DecisionResult
from wsb_signal_engine import DailyOpinionSnapshot
from universe_filter import UniverseDecision
from cost_aware_trade_filter import CostAwareTradeDecision


def _snap(**over) -> DailyOpinionSnapshot:
    base = dict(
        date="2026-03-01", symbol="X", opinion_score=78.0, opinion_trend="UP",
        persistence_days=3, consensus_ratio=2.0, neutral_ratio=0.25,
        velocity_state="NORMAL", universe_tier="CORE",
    )
    base.update(over)
    return DailyOpinionSnapshot(**base)


def _univ(allowed=True, tier="CORE", mult=1.0, reasons=None):
    return UniverseDecision("X", allowed, tier, reasons or [], 0.8, 0.8, mult)


def _cost(allowed=True, factor=1.0, action="ENTER"):
    return CostAwareTradeDecision(allowed, ["EDGE_SUFFICIENT"], 0.7, 5.0, 7.0, factor, action)


def _decide(router=None, *, signal="BUY", snap=None, univ=None, cost=None,
            position=None, cash=100000.0, low=None, high=None):
    r = router or DecisionRouter()
    return r.decide(
        symbol="X", current_signal=signal,
        daily_opinion_snapshot=snap or _snap(),
        retrieved_similar_opinions=[], retrieved_low_level_reflections=low or [],
        retrieved_high_level_reflections=high or [], rsi=45.0, atr=2.0,
        market_filter_status="NORMAL", universe_decision=univ or _univ(),
        cost_filter_decision=cost or _cost(), current_position=position,
        cash=cash, equity=100000.0, risk_settings={},
    )


# --- T1: strong consensus + low neutral + persistence → BUY ---
def test_t1_buy_approved():
    d = _decide()
    assert d.action == "BUY"
    assert d.size_factor > 0
    assert d.router_mode == "rule_based"
    assert "buy_approved" in d.reason_codes


# --- T2: NEW_SPIKE 단독(persistence 부족) → size 축소 ---
def test_t2_new_spike_downsize():
    full = _decide()
    spike = _decide(snap=_snap(velocity_state="NEW_SPIKE", persistence_days=1))
    assert spike.action in ("BUY", "SKIP")
    if spike.action == "BUY":
        assert spike.size_factor < full.size_factor
        assert "new_spike_downsize" in spike.reason_codes


# --- T3: neutral_ratio가 현재 상한을 넘으면 SKIP ---
def test_t3_high_neutral_skip():
    d = _decide(snap=_snap(neutral_ratio=config.COMMUNITY_NEUTRAL_RATIO_MAX + 0.01))
    assert d.action == "SKIP"
    assert "high_noise" in d.reason_codes


# --- T4: consensus 붕괴 + 보유 → SELL/REDUCE/EXIT ---
def test_t4_consensus_break_sell():
    pos = {"symbol": "X", "shares": 10}
    d = _decide(snap=_snap(consensus_ratio=0.8, neutral_ratio=0.25), position=pos)
    assert d.action in ("SELL", "REDUCE", "EXIT")


# --- T5: 과거 유사 실패 多 → size 축소 ---
def test_t5_history_downsize():
    fails = [{"result_label": "failed"}, {"result_label": "failed"},
             {"decision_quality": "bad_entry"}]
    base = _decide()
    d = _decide(low=fails)
    assert d.action == "BUY"
    assert d.size_factor < base.size_factor
    assert "history_downsize" in d.reason_codes


# --- T6: universe blocked → BUY 금지(SKIP) ---
def test_t6_universe_blocked():
    d = _decide(univ=_univ(allowed=False, tier="BLOCKED", reasons=["LOW_DOLLAR_VOLUME"]))
    assert d.action == "SKIP"
    assert "universe_blocked" in d.reason_codes


# --- T7: cost blocked → BUY 금지 ---
def test_t7_cost_blocked():
    d = _decide(cost=_cost(allowed=False, factor=0.0, action="SKIP"))
    assert d.action == "SKIP"
    assert "cost_blocked" in d.reason_codes


# --- T8: final_size_factor ≤ 1.3 ---
def test_t8_size_clamped():
    d = _decide(snap=_snap(opinion_score=99, consensus_ratio=9.0, opinion_trend="UP",
                           persistence_days=5),
                univ=_univ(mult=1.0), cost=_cost(factor=1.0))
    assert d.size_factor <= config.COMMUNITY_SIZE_FACTOR_MAX


# --- T9: universe COMMUNITY_LIQUID size_multiplier 반영 ---
def test_t9_community_liquid_multiplier():
    core = _decide(univ=_univ(tier="CORE", mult=1.0))
    cl = _decide(univ=_univ(tier="COMMUNITY_LIQUID", mult=0.5))
    assert cl.size_factor < core.size_factor


# --- T10: DecisionResult strict schema ---
def test_t10_schema():
    d = _decide()
    for fld in ("action", "confidence", "size_factor", "risk_modifier",
                "stop_loss_pct", "trailing_stop_pct", "reason_codes", "reasoning",
                "tool_interpretation", "memory_hits_used", "warnings", "router_mode"):
        assert hasattr(d, fld), fld
    assert d.action in {"BUY", "HOLD", "SELL", "REDUCE", "SKIP", "EXIT"}
    assert set(d.tool_interpretation) == {
        "opinion_signal", "consensus_signal", "noise_signal", "memory_signal",
        "reflection_signal", "technical_signal", "universe_signal",
        "cost_signal", "risk_signal"}


# --- T11: 보유 없으면 SELL 불가 (안전장치) ---
def test_t11_no_position_no_sell():
    # consensus 붕괴여도 포지션 없으면 SELL 금지 → SKIP
    d = _decide(snap=_snap(consensus_ratio=0.8), position=None)
    assert d.action != "SELL"


# --- T12: rule SKIP 조건에서 cash 부족 → SKIP ---
def test_t12_insufficient_cash_skip():
    d = _decide(cash=0.0)
    assert d.action == "SKIP"
    assert "insufficient_cash" in d.reason_codes


# --- T13: rule 신호 없음(NEUTRAL) → SKIP ---
def test_t13_no_signal_skip():
    d = _decide(signal="NEUTRAL")
    assert d.action == "SKIP"
    assert "no_rule_signal" in d.reason_codes


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\nDecisionRouter (rule-based) 단위 테스트 - {len(tests)}건\n" + "-" * 50)
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
