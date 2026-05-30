"""CommunityMemoryStore + Reflection 단위 테스트
(community-opinion-agent §3.3·§3.4 / Plan FR-2.*).

실행:
  pytest tests/test_community_memory_reflection.py
  python tests/test_community_memory_reflection.py
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
from community_memory import (
    CommunityMemoryStore, JsonlMemoryStore, KIND_OPINION,
)
from opinion_reflection import (
    LowLevelReflection, HighLevelReflection, build_low_level, build_high_level,
)
from wsb_signal_engine import build_daily_snapshot


def _store(tmp) -> CommunityMemoryStore:
    return CommunityMemoryStore(backend=JsonlMemoryStore(base_dir=tmp), top_k=5)


def _snap(symbol, **over):
    base = {"bullish": 5, "bearish": 1, "neutral": 1, "score": 78,
            "mentions": 7, "neutral_ratio": 0.14, "velocity_state": "NORMAL"}
    base.update(over)
    return build_daily_snapshot(symbol, base, history=[], date_str="2026-03-01")


# --- T1: opinion snapshot 저장/로드 ---
def test_t1_snapshot_store_load():
    with tempfile.TemporaryDirectory() as d:
        store = _store(d)
        store.add_opinion_snapshot(_snap("NVDA"))
        store.add_opinion_snapshot(_snap("TSLA"))
        rows = store.backend.read_all(KIND_OPINION)
        assert len(rows) == 2
        assert {r["symbol"] for r in rows} == {"NVDA", "TSLA"}


# --- T2: retrieval query field 생성 ---
def test_t2_query_fields():
    snap = _snap("NVDA")
    assert snap.query_consensus and snap.query_risk and snap.query_opinion_trend


# --- T3: 유사 opinion snapshot 검색 (같은 symbol 우선) ---
def test_t3_retrieve_similar():
    with tempfile.TemporaryDirectory() as d:
        store = _store(d)
        for sym in ("NVDA", "TSLA", "AMD", "NVDA"):
            store.add_opinion_snapshot(_snap(sym))
        query = {"symbol": "NVDA", "opinion_score": 78, "consensus_ratio": 5.0,
                 "universe_tier": "CORE"}
        hits = store.retrieve_similar_opinions("NVDA", query, top_k=3)
        assert len(hits) == 3
        assert hits[0]["symbol"] == "NVDA"   # 같은 symbol 최상위


# --- T4: low-level reflection — next_1d/3d/7d/14d 계산 + result_label ---
def test_t4_low_level_returns():
    snap = _snap("NVDA")
    fwd = {1: 110.0, 3: 112.0, 7: 115.0, 14: 120.0}   # entry 100 → +10/+12/+15/+20%
    ref = build_low_level(snap, fwd, entry_price=100.0)
    assert ref.next_1d_return == 10.0
    assert ref.next_7d_return == 15.0
    assert ref.result_label == "success_1d"   # 1d +10% ≥ 2%
    # 실패 케이스
    fwd2 = {1: 99.0, 3: 98.0, 7: 97.0, 14: 99.5}
    ref2 = build_low_level(snap, fwd2, entry_price=100.0)
    assert ref2.result_label == "failed"


# --- T5: high-level reflection — pnl / score_change / consensus_change / cost_drag ---
def test_t5_high_level():
    entry = {"opinion_score": 80, "consensus_ratio": 3.0, "neutral_ratio": 0.2,
             "velocity_state": "NORMAL", "universe_tier": "CORE"}
    exit_ = {"opinion_score": 60, "consensus_ratio": 1.0, "neutral_ratio": 0.5,
             "velocity_state": "DECLINING"}
    trade = {"symbol": "NVDA", "entry_date": "2026-03-01", "date": "2026-03-05",
             "entry_price": 100.0, "price": 110.0, "shares": 10,
             "gross_pnl": 100.0, "net_pnl": 94.0, "commission": 3.0,
             "pnl_pct": 9.4, "reason": "trailing_stop"}
    ref = build_high_level(entry, exit_, trade)
    assert ref.net_pnl_after_cost == 94.0
    assert ref.opinion_score_change == -20.0
    assert ref.consensus_change == -2.0
    assert abs(ref.neutral_ratio_change - 0.3) < 1e-9
    assert ref.cost_drag_pct > 0            # gross>net → 비용 발생
    assert ref.decision_quality == "risk_management_success"   # trailing_stop


# --- T6: bad_entry / stop_loss 분류 ---
def test_t6_decision_quality_variants():
    entry_weak = {"opinion_score": 50}     # < LOW(60) → bad_entry
    trade = {"symbol": "X", "entry_price": 100, "price": 105, "shares": 1,
             "gross_pnl": 5, "net_pnl": 3, "commission": 1, "reason": ""}
    assert build_high_level(entry_weak, {}, trade).decision_quality == "bad_entry"

    trade_sl = dict(trade, price=92, gross_pnl=-8, net_pnl=-10, reason="stop_loss")
    assert build_high_level({"opinion_score": 75}, {}, trade_sl).decision_quality \
        == "risk_management_failure"


# --- T7: closed trade 기반 reflection 저장/검색 ---
def test_t7_reflection_store_retrieve():
    with tempfile.TemporaryDirectory() as d:
        store = _store(d)
        entry = {"opinion_score": 80, "consensus_ratio": 3.0, "neutral_ratio": 0.2,
                 "velocity_state": "NORMAL", "universe_tier": "CORE", "symbol": "NVDA"}
        trade = {"symbol": "NVDA", "entry_price": 100, "price": 110, "shares": 10,
                 "gross_pnl": 100, "net_pnl": 94, "commission": 3, "reason": "trailing_stop"}
        store.add_high_level_reflection(build_high_level(entry, {}, trade))
        hits = store.retrieve_high_level_reflections(
            "NVDA", {"symbol": "NVDA", "opinion_score": 80})
        assert len(hits) == 1
        assert hits[0]["decision_quality"] == "risk_management_success"


# --- T8: jsonl append/read 정상 ---
def test_t8_jsonl_append_read():
    with tempfile.TemporaryDirectory() as d:
        be = JsonlMemoryStore(base_dir=d)
        be.append(KIND_OPINION, {"symbol": "A", "opinion_score": 70})
        be.append(KIND_OPINION, {"symbol": "B", "opinion_score": 80})
        rows = be.read_all(KIND_OPINION)
        assert [r["symbol"] for r in rows] == ["A", "B"]


# --- T9: memory OFF → add/retrieve no-op (회귀 0) ---
def test_t9_memory_disabled_noop():
    orig = config.COMMUNITY_MEMORY_ENABLED
    try:
        config.COMMUNITY_MEMORY_ENABLED = False
        with tempfile.TemporaryDirectory() as d:
            store = _store(d)
            store.add_opinion_snapshot(_snap("NVDA"))
            assert store.retrieve_similar_opinions("NVDA", {}) == []
            assert store.backend.read_all(KIND_OPINION) == []   # 저장 안 됨
    finally:
        config.COMMUNITY_MEMORY_ENABLED = orig


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\nCommunityMemory + Reflection 단위 테스트 - {len(tests)}건\n" + "-" * 50)
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
