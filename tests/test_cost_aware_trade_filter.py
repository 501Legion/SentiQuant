"""CostAwareTradeFilter 단위 테스트 (community-opinion-agent §3.2 / Plan FR-00.4~5).

round_trip_cost 계산, edge<cost×2 SKIP, ATR pct 부족 SKIP, 경계 DOWNSIZE,
필터 OFF → allowed. 단위: 퍼센트(5.0 = 5%).

실행:
  pytest tests/test_cost_aware_trade_filter.py
  python tests/test_cost_aware_trade_filter.py
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
from cost_aware_trade_filter import CostAwareTradeFilter, CostAwareTradeDecision

# round_trip_pct = (0.0025*2 + 0.001 + 0.001) * 100 = 0.7%
_EXPECTED_RT = (config.COMMISSION_RATE * 2
                + config.COMMUNITY_ESTIMATED_SLIPPAGE_PCT
                + config.COMMUNITY_ESTIMATED_SPREAD_PCT) * 100.0


def _f():
    return CostAwareTradeFilter()


# --- T1: 필터 OFF → allowed (회귀 0) ---
def test_t1_filter_disabled():
    orig = config.COMMUNITY_ENABLE_COST_AWARE_FILTER
    try:
        config.COMMUNITY_ENABLE_COST_AWARE_FILTER = False
        d = _f().evaluate(atr_pct=0.1)   # 평소면 SKIP될 값
        assert d.allowed is True
        assert d.cost_risk_factor == 1.0
        assert "FILTER_DISABLED" in d.reason_codes
    finally:
        config.COMMUNITY_ENABLE_COST_AWARE_FILTER = orig


# --- T2: round_trip_cost 계산 ---
def test_t2_round_trip_cost():
    d = _f().evaluate(atr_pct=5.0)
    assert abs(d.round_trip_cost_pct - _EXPECTED_RT) < 1e-6, d.round_trip_cost_pct


# --- T3: 충분한 edge(ATR) → ENTER, factor 1.0 ---
def test_t3_sufficient_edge_enter():
    d = _f().evaluate(atr_pct=5.0)        # ratio 5/0.7 ≈ 7.1 ≥ 3.0
    assert d.allowed is True
    assert d.recommended_action == "ENTER"
    assert d.cost_risk_factor == 1.0
    assert "EDGE_FROM_ATR" in d.reason_codes


# --- T4: edge < cost×2 → SKIP ---
def test_t4_edge_below_cost_skip():
    # atr_pct=1.0 (== MIN_ATR, 통과) → ratio 1.0/0.7 ≈ 1.43 < 2.0 → SKIP
    d = _f().evaluate(atr_pct=config.COMMUNITY_MIN_ATR_PCT_FOR_TRADE)
    assert d.allowed is False
    assert d.recommended_action == "SKIP"
    assert "EDGE_BELOW_COST_THRESHOLD" in d.reason_codes


# --- T5: ATR pct < MIN → SKIP ---
def test_t5_atr_below_min_skip():
    d = _f().evaluate(atr_pct=config.COMMUNITY_MIN_ATR_PCT_FOR_TRADE - 0.5)
    assert d.allowed is False
    assert d.recommended_action == "SKIP"
    assert "ATR_BELOW_MIN" in d.reason_codes


# --- T6: 경계 edge → DOWNSIZE, factor 0.7 ---
def test_t6_marginal_downsize():
    # ratio ∈ [2.0, 3.0): edge ≈ 1.5% → 1.5/0.7 ≈ 2.14
    d = _f().evaluate(atr_pct=1.5)
    assert d.allowed is True
    assert d.recommended_action == "DOWNSIZE"
    assert d.cost_risk_factor == 0.7
    assert "EDGE_MARGINAL" in d.reason_codes


# --- T7: edge 우선순위 (ATR > 변동폭 > conviction) ---
def test_t7_edge_priority():
    d = _f().evaluate(atr_pct=5.0, recent_volatility_pct=0.1, opinion_conviction=0.9)
    assert "EDGE_FROM_ATR" in d.reason_codes
    d2 = _f().evaluate(recent_volatility_pct=5.0, opinion_conviction=0.9)
    assert "EDGE_FROM_VOLATILITY" in d2.reason_codes
    d3 = _f().evaluate(opinion_conviction=1.0)
    assert "EDGE_FROM_CONVICTION" in d3.reason_codes


# --- T8: edge 데이터 없음 → SKIP (NO_EDGE_DATA) ---
def test_t8_no_edge_data_skip():
    d = _f().evaluate()
    assert d.allowed is False
    assert "NO_EDGE_DATA" in d.reason_codes


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\nCostAwareTradeFilter 단위 테스트 - {len(tests)}건\n" + "-" * 50)
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
