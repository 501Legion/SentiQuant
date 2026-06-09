"""Persistent DecisionLog 단위 테스트
(community-opinion-agent — decision_log.py).

핵심: 모든 후보(BUY/SKIP/HOLD/...)의 판단 원본을 jsonl로 영속 저장,
LLM reasoning/tool_interpretation 보존, decision_id로 reflection join,
optional 필드 누락에도 append 실패 없음.

실행:
  pytest tests/test_decision_log_persistence.py
  python tests/test_decision_log_persistence.py
"""
from __future__ import annotations

import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
from decision_log import (
    build_decision_record, append_decision_log, load_decision_logs,
    make_decision_id, decision_log_path,
)
from decision_router import DecisionResult
from universe_filter import UniverseDecision
from cost_aware_trade_filter import CostAwareTradeDecision
from wsb_signal_engine import build_daily_snapshot


def _snap(symbol="NVDA"):
    return build_daily_snapshot(
        symbol, {"bullish": 5, "bearish": 1, "neutral": 1, "score": 80,
                 "mentions": 7, "neutral_ratio": 0.14, "velocity_state": "NORMAL"},
        history=[], date_str="2026-03-01")


def _univ():
    return UniverseDecision("NVDA", True, "CORE", ["INDEX_CORE"], 0.9, 0.9, 1.0)


def _cost():
    return CostAwareTradeDecision(True, ["EDGE_SUFFICIENT"], 0.7, 5.0, 7.1, 1.0, "ENTER")


def _record(action="BUY", router_mode="rule_based", **over):
    d = DecisionResult(
        action=action, confidence=0.8, size_factor=1.0, reason_codes=["buy_approved"],
        reasoning="rule reasoning", router_mode=router_mode, rule_action="BUY",
        tool_interpretation={"opinion_signal": "score 80"},
    )
    for k, v in over.items():
        setattr(d, k, v)
    return build_decision_record(
        decision=d, snapshot=_snap(), universe_decision=_univ(), cost_decision=_cost(),
        date="2026-03-01", symbol="NVDA", source="reddit", model="finbert-wsb",
        ranking="sentiment", sizing="opinion_trend", universe_mode="community_liquid",
        run_id="testrun", current_signal="BUY", llm_enabled=False, llm_model="",
    )


# --- T1: rule_based DecisionResult가 jsonl에 저장됨 ---
def test_t1_rule_based_persisted():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "decisions.jsonl")
        append_decision_log(_record(), path=p)
        rows = load_decision_logs(path=p)
        assert len(rows) == 1
        r = rows[0]
        assert r["final_action"] == "BUY" and r["router_mode"] == "rule_based"
        assert r["symbol"] == "NVDA" and r["decision_id"]
        assert r["opinion_score"] == 80 and r["universe_tier"] == "CORE"


# --- T2: llm_assisted reasoning / tool_interpretation 저장 ---
def test_t2_llm_reasoning_persisted():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "decisions.jsonl")
        rec = _record(action="HOLD", router_mode="llm_assisted",
                      reasoning="rule | LLM: persist 1일이라 보류",
                      llm_action="HOLD",
                      tool_interpretation={"risk_signal": "persistence weak"})
        append_decision_log(rec, path=p)
        r = load_decision_logs(path=p)[0]
        assert r["router_mode"] == "llm_assisted"
        assert "LLM:" in r["reasoning"]
        assert r["llm_action"] == "HOLD"
        assert r["tool_interpretation"]["risk_signal"] == "persistence weak"


# --- T3: SKIP / HOLD 도 저장됨 ---
def test_t3_skip_hold_persisted():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "decisions.jsonl")
        append_decision_log(_record(action="SKIP", reason_codes=["high_noise"]), path=p)
        append_decision_log(_record(action="HOLD"), path=p)
        acts = [r["final_action"] for r in load_decision_logs(path=p)]
        assert "SKIP" in acts and "HOLD" in acts


# --- T4: closed trade 없어도 decision log는 남음 (독립 저장) ---
def test_t4_independent_of_trades():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "decisions.jsonl")
        # 거래(체결) 없이 SKIP 판단만 저장
        append_decision_log(_record(action="SKIP"), path=p)
        assert len(load_decision_logs(path=p)) == 1


# --- T5: load_decision_logs(symbol=) / 날짜 필터 ---
def test_t5_filters():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "decisions.jsonl")
        for sym, dt in [("NVDA", "2026-03-01"), ("TSLA", "2026-03-02"), ("NVDA", "2026-03-03")]:
            rec = _record(); rec["symbol"] = sym; rec["date"] = dt
            append_decision_log(rec, path=p)
        assert len(load_decision_logs(symbol="NVDA", path=p)) == 2
        assert len(load_decision_logs(start_date="2026-03-02", path=p)) == 2
        assert len(load_decision_logs(end_date="2026-03-01", path=p)) == 1


# --- T6: optional 필드 누락에도 append 실패하지 않음 ---
def test_t6_missing_optional_fields():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "decisions.jsonl")
        # snapshot/universe/cost None, 최소 DecisionResult
        rec = build_decision_record(
            decision=DecisionResult(action="SKIP"),
            snapshot=None, universe_decision=None, cost_decision=None,
            date="2026-03-01", symbol="X", source="reddit", model="m",
            ranking="r", sizing="s", universe_mode="u",
        )
        append_decision_log(rec, path=p)   # 예외 없이 저장
        r = load_decision_logs(path=p)[0]
        assert r["final_action"] == "SKIP"
        assert r["opinion_score"] is None and r["universe_tier"] == ""


# --- T7: make_decision_id 결정성 + reflection join 키 일치 ---
def test_t7_decision_id_deterministic():
    a = make_decision_id("2026-03-01", "NVDA", "reddit", "finbert-wsb",
                         "sentiment", "opinion_trend", "community_liquid")
    b = make_decision_id("2026-03-01", "NVDA", "reddit", "finbert-wsb",
                         "sentiment", "opinion_trend", "community_liquid")
    c = make_decision_id("2026-03-02", "NVDA", "reddit", "finbert-wsb",
                         "sentiment", "opinion_trend", "community_liquid")
    assert a == b and a != c
    # Low/HighLevelReflection이 같은 키로 join 가능
    from opinion_reflection import build_low_level, build_high_level
    low = build_low_level(_snap(), {1: 110.0}, 100.0, decision_id=a)
    assert low.decision_id == a
    trade = {"symbol": "NVDA", "entry_price": 100, "price": 110, "shares": 1,
             "gross_pnl": 10, "net_pnl": 8, "commission": 1, "reason": "trailing_stop"}
    high = build_high_level({"opinion_score": 80}, {}, trade, decision_id=a)
    assert high.decision_id == a


# --- T8: 저장 경로 결정 (run_id / live / 기본) ---
def test_t8_path_resolution():
    assert "backtests" in decision_log_path(run_id="abc") and "abc" in decision_log_path(run_id="abc")
    assert decision_log_path(live=True) == config.COMMUNITY_LIVE_DECISIONS_FILE
    assert decision_log_path() == config.COMMUNITY_DECISIONS_FILE


# --- T9: flag OFF면 append no-op ---
def test_t9_flag_off_noop():
    orig = config.COMMUNITY_DECISION_LOG_ENABLED
    try:
        config.COMMUNITY_DECISION_LOG_ENABLED = False
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "decisions.jsonl")
            append_decision_log(_record(), path=p)
            assert not os.path.exists(p)
    finally:
        config.COMMUNITY_DECISION_LOG_ENABLED = orig


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\nDecisionLog 영속성 단위 테스트 - {len(tests)}건\n" + "-" * 50)
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
