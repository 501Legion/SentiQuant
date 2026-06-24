"""OpinionTrendSizer +3 factor 단위 테스트
(community-opinion-agent §7 / Plan FR-1.6).

기존 test_opinion_trend_sizing.py(T1~T12)의 회귀를 유지하면서
source_quality / universe_size_multiplier / cost_risk_factor 신규 factor 검증.

실행:
  pytest tests/test_opinion_trend_sizer.py
  python tests/test_opinion_trend_sizer.py
"""
from __future__ import annotations

import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
from position_sizer import CommunityOpinionTrendSizer
from wsb_signal_engine import DailyOpinionSnapshot

_CASH = 100_000.0
_PRICE = 100.0


def _snap(**over) -> DailyOpinionSnapshot:
    """게이팅 통과 baseline (final factor 1.0 → 100주)."""
    base = dict(
        date="2026-03-01", symbol="X", opinion_score=75.0, opinion_trend="FLAT",
        persistence_days=2, consensus_ratio=1.8, neutral_ratio=0.30,
        velocity_state="NORMAL", source_quality_score=1.0,
        universe_size_multiplier=1.0, cost_risk_factor=1.0,
    )
    base.update(over)
    return DailyOpinionSnapshot(**base)


def _shares(**over) -> int:
    return CommunityOpinionTrendSizer().calc_shares(_CASH, _PRICE, opinion=_snap(**over))


# --- T1: baseline factor 1.0 → 100주 ---
def test_t1_baseline():
    assert _shares() == 100


# --- T2: opinion_score ≥ 80 → high factor ---
def test_t2_score_high():
    assert _shares(opinion_score=85.0) == 120


# --- T3: opinion_score가 현재 하한 미만이면 진입 제외 (0) ---
def test_t3_score_low_excluded():
    assert _shares(opinion_score=config.WSB_OPINION_SCORE_LOW - 1.0) == 0


# --- T4: 3일 상승 trend → factor 증가 ---
def test_t4_trend_up():
    assert _shares(opinion_trend="UP") == 115


# --- T5: 3일 하락 trend → factor 감소 ---
def test_t5_trend_down():
    assert _shares(opinion_trend="DOWN") == 50


# --- T6: consensus 약함(<1.5) → 진입 제외 ---
def test_t6_weak_consensus_excluded():
    assert _shares(consensus_ratio=1.2) == 0


# --- T7: neutral_ratio가 진입 상한을 넘으면 진입 제외 ---
def test_t7_high_neutral_excluded():
    assert _shares(neutral_ratio=config.WSB_OPINION_NEUTRAL_ENTRY_MAX + 0.01) == 0


# --- T8: NEW_SPIKE 단독(persistence 부족) → 축소 ---
def test_t8_new_spike_reduced():
    spike = _shares(velocity_state="NEW_SPIKE", persistence_days=1)  # 0.5×0.6=0.3 → 30
    assert spike == 30
    assert spike < _shares()


# --- T9: final_size_factor ≤ 1.3 clamp ---
def test_t9_factor_clamped():
    sizer = CommunityOpinionTrendSizer()
    shares = sizer.calc_shares(_CASH, _PRICE, opinion=_snap(
        opinion_score=99.0, opinion_trend="UP", persistence_days=5,
        consensus_ratio=9.0, neutral_ratio=0.1, velocity_state="HIGH_MOMENTUM",
        source_quality_score=1.5,
    ))
    assert sizer.last_size_factor == config.WSB_OPINION_SIZE_FACTOR_MAX  # 1.3
    assert shares == math.floor(_CASH * config.EQUAL_POSITION_PCT * 1.3 / _PRICE)  # 130


# --- T10: universe_size_multiplier 적용 (신규) ---
def test_t10_universe_size_multiplier():
    base = _shares()                                  # 100
    half = _shares(universe_size_multiplier=0.5)      # ×0.5 → 50
    assert half == 50
    assert half < base


# --- T11: cost_risk_factor 0 → skip (신규) ---
def test_t11_cost_skip():
    assert _shares(cost_risk_factor=0.0) == 0
    # 부분 비용 페널티
    assert _shares(cost_risk_factor=0.7) == 70


# --- T12: source_quality_factor 적용 (신규) ---
def test_t12_source_quality_factor():
    high = _shares(source_quality_score=1.2)   # ×1.1 → 110
    low = _shares(source_quality_score=0.4)    # ×0.7 → 70
    assert high == 110
    assert low == 70


# --- T13: 신규 factor 미제공(기본 1.0) → baseline 불변 (회귀 0) ---
def test_t13_defaults_no_regression():
    # 신규 3 factor 모두 1.0 기본 → baseline 100 유지
    assert _shares() == 100
    assert _shares(source_quality_score=1.0,
                   universe_size_multiplier=1.0, cost_risk_factor=1.0) == 100


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\nOpinionTrendSizer +3 factor 단위 테스트 - {len(tests)}건\n" + "-" * 50)
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
