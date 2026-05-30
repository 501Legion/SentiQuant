"""UniverseFilter 단위 테스트 (community-opinion-agent §3.1 / Plan FR-00.1~3).

mode별 allowed 판정, CORE/EXPANDED/COMMUNITY_LIQUID/BLOCKED tier,
penny/저유동/OTC/ambiguity 차단, 필터 OFF → 회귀 0.

실행:
  pytest tests/test_universe_filter.py
  python tests/test_universe_filter.py
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
import universe_filter as uf
from universe_filter import (
    UniverseFilter, UniverseDecision,
    TIER_CORE, TIER_EXPANDED, TIER_COMMUNITY_LIQUID, TIER_BLOCKED,
)

# 테스트용 주입 index 집합 (파일 의존 제거 → 결정성)
_SP500 = {"AAPL", "MSFT", "NVDA"}
_NQ100 = {"AAPL", "NVDA", "ARM"}
_LIQUID_DV = config.COMMUNITY_MIN_AVG_DOLLAR_VOLUME * 3   # 충분한 유동성
_THIN_DV = config.COMMUNITY_MIN_AVG_DOLLAR_VOLUME * 0.1   # 저유동


def _filter(mode):
    return UniverseFilter(mode=mode, sp500=_SP500, nasdaq100=_NQ100, market_caps={})


def _toggle(flag: bool):
    """COMMUNITY_ENABLE_UNIVERSE_FILTER 토글 (복원은 호출측 책임)."""
    config.COMMUNITY_ENABLE_UNIVERSE_FILTER = flag


# --- T1: 필터 OFF → 무조건 허용 (회귀 0) ---
def test_t1_filter_disabled_allows_all():
    orig = config.COMMUNITY_ENABLE_UNIVERSE_FILTER
    try:
        _toggle(False)
        d = _filter("sp500_only").decide("PENNYSTOCK", price=0.1, avg_dollar_volume=1.0)
        assert d.allowed is True
        assert d.universe_tier == TIER_CORE
        assert d.size_multiplier == 1.0
        assert "FILTER_DISABLED" in d.reason_codes
    finally:
        _toggle(orig)


# --- T2: 인덱스 종목 → CORE, community_liquid 허용, size 1.0 ---
def test_t2_index_core():
    d = _filter("community_liquid").decide(
        "AAPL", price=200.0, avg_dollar_volume=_LIQUID_DV)
    assert d.universe_tier == TIER_CORE
    assert d.allowed is True
    assert d.size_multiplier == 1.0
    assert "INDEX_CORE" in d.reason_codes


# --- T3: 인덱스 외 + 유동성 통과 → COMMUNITY_LIQUID, size 0.5 ---
def test_t3_non_index_liquid():
    d = _filter("community_liquid").decide(
        "GME", price=30.0, avg_dollar_volume=_LIQUID_DV)
    assert d.universe_tier == TIER_COMMUNITY_LIQUID
    assert d.allowed is True
    assert d.size_multiplier == config.COMMUNITY_NON_INDEX_SIZE_MULTIPLIER
    assert "NON_INDEX_LIQUID" in d.reason_codes


# --- T4: 인덱스 외 liquid는 sp500_only에서 차단 ---
def test_t4_non_index_blocked_in_sp500_only():
    d = _filter("sp500_only").decide(
        "GME", price=30.0, avg_dollar_volume=_LIQUID_DV)
    assert d.allowed is False
    assert "NOT_SP500" in d.reason_codes


# --- T5: penny stock → BLOCKED ---
def test_t5_penny_stock_blocked():
    d = _filter("community_liquid").decide(
        "PENNY", price=2.0, avg_dollar_volume=_LIQUID_DV)
    assert d.universe_tier == TIER_BLOCKED
    assert d.allowed is False
    assert "PENNY_STOCK" in d.reason_codes


# --- T6: 인덱스 외 저유동 → BLOCKED ---
def test_t6_low_volume_non_index_blocked():
    d = _filter("community_liquid").decide(
        "THINLY", price=50.0, avg_dollar_volume=_THIN_DV)
    assert d.universe_tier == TIER_BLOCKED
    assert d.allowed is False
    assert "LOW_DOLLAR_VOLUME" in d.reason_codes


# --- T7: ticker ambiguity → BLOCKED ---
def test_t7_ambiguity_blocked():
    d = _filter("community_liquid").decide(
        "ALL", price=100.0, avg_dollar_volume=_LIQUID_DV, ambiguity_risk=True)
    assert d.universe_tier == TIER_BLOCKED
    assert d.allowed is False
    assert "TICKER_AMBIGUOUS" in d.reason_codes
    assert d.tradeability_score == 0.0


# --- T8: sp500_only는 AAPL 허용, nasdaq100_only는 ARM(인덱스 멤버) 허용 ---
def test_t8_index_only_membership():
    assert _filter("sp500_only").decide(
        "AAPL", price=200.0, avg_dollar_volume=_LIQUID_DV).allowed is True
    # ARM은 nasdaq100에만 → sp500_only 차단
    assert _filter("sp500_only").decide(
        "ARM", price=100.0, avg_dollar_volume=_LIQUID_DV).allowed is False
    assert _filter("nasdaq100_only").decide(
        "ARM", price=100.0, avg_dollar_volume=_LIQUID_DV).allowed is True


# --- T9: 시총 게이팅 (market_cap < MIN, 인덱스 외) → BLOCKED ---
def test_t9_low_market_cap_non_index_blocked():
    f = UniverseFilter(mode="community_liquid", sp500=_SP500, nasdaq100=_NQ100,
                       market_caps={"SMALLCAP": 100_000_000})  # < 1e9
    d = f.decide("SMALLCAP", price=50.0, avg_dollar_volume=_LIQUID_DV)
    assert d.universe_tier == TIER_BLOCKED
    assert "LOW_MARKET_CAP" in d.reason_codes


# --- T10: liquid_us는 COMMUNITY_LIQUID 차단(EXPANDED까지만) ---
def test_t10_liquid_us_excludes_community_liquid():
    d = _filter("liquid_us").decide(
        "GME", price=30.0, avg_dollar_volume=_LIQUID_DV)
    assert d.universe_tier == TIER_COMMUNITY_LIQUID
    assert d.allowed is False


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\nUniverseFilter 단위 테스트 - {len(tests)}건\n" + "-" * 50)
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
