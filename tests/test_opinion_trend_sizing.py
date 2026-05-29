"""Community Opinion Trend Sizing 단위 테스트 (T1~T12).

Design Ref: community-opinion-trend-sizing §8 — CommunityOpinionTrendSizer 7-factor,
진입 게이팅, opinion_reversal 청산, equal 회귀(opinion_mode=False), 기존 sizer 호환.

실행 방법:
  pytest tests/test_opinion_trend_sizing.py
  python tests/test_opinion_trend_sizing.py     # 단독 실행
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
import wsb_state
from position_sizer import (
    CommunityOpinionTrendSizer,
    EqualSizer,
    SentimentSizer,
    get_sizer,
)
from wsb_signal_engine import WSBSignalEngine
from reddit_backtester import OpinionMetrics

_CASH = 100_000.0
_PRICE = 100.0


def _om(**over) -> OpinionMetrics:
    """게이팅 통과 baseline (final factor = 1.0 → 100주)."""
    base = dict(
        opinion_score=75.0, sentiment_trend="FLAT", persistence_days=2,
        consensus_ratio=1.8, neutral_ratio=0.30, velocity_state="NORMAL",
        atr=None, prev_close=None,
    )
    base.update(over)
    return OpinionMetrics(**base)


def _shares(**over) -> int:
    return CommunityOpinionTrendSizer().calc_shares(_CASH, _PRICE, opinion=_om(**over))


# --- T1: sentiment_factor HIGH -------------------------------------------
def test_t1_sentiment_high_factor():
    base = _shares()                       # score75 → 1.0 → 100
    high = _shares(opinion_score=85.0)     # score85 → 1.2 → 120
    assert base == 100, base
    assert high == 120, high
    assert high > base


# --- T2: opinion_score < 60 → 진입 제외 ----------------------------------
def test_t2_low_score_excluded():
    assert _shares(opinion_score=55.0) == 0


# --- T3: neutral_ratio > 0.70 → 진입 제외 --------------------------------
def test_t3_high_neutral_excluded():
    assert _shares(neutral_ratio=0.75) == 0


# --- T4: consensus_ratio < 1.5 → 진입 제외 -------------------------------
def test_t4_weak_consensus_excluded():
    assert _shares(consensus_ratio=1.2) == 0


# --- T5: trend UP → factor 증가 ------------------------------------------
def test_t5_trend_up_increases():
    flat = _shares(sentiment_trend="FLAT")     # 100
    up = _shares(sentiment_trend="UP")         # 1.15 → 115
    assert up == 115, up
    assert up > flat


# --- T6: trend DOWN → factor 감소 ----------------------------------------
def test_t6_trend_down_decreases():
    flat = _shares(sentiment_trend="FLAT")     # 100
    down = _shares(sentiment_trend="DOWN")     # 0.5 → 50
    assert down == 50, down
    assert down < flat


# --- T7: NEW_SPIKE 단독(지속성 부족) → 축소 ------------------------------
def test_t7_new_spike_reduced():
    base = _shares()                                              # 100
    spike = _shares(velocity_state="NEW_SPIKE", persistence_days=1)  # 0.5×0.6=0.3 → 30
    assert spike == 30, spike
    assert spike < base


# --- T8: DECLINING → 축소 ------------------------------------------------
def test_t8_declining_reduced():
    base = _shares()                               # 100
    decl = _shares(velocity_state="DECLINING")     # 0.6 → 60
    assert decl == 60, decl
    assert decl < base


# --- T9: final_size_factor clamp ≤ 1.3 -----------------------------------
def test_t9_factor_clamped():
    sizer = CommunityOpinionTrendSizer()
    shares = sizer.calc_shares(_CASH, _PRICE, opinion=_om(
        opinion_score=99.0, sentiment_trend="UP", persistence_days=5,
        consensus_ratio=9.0, neutral_ratio=0.1, velocity_state="HIGH_MOMENTUM",
    ))
    assert sizer.last_size_factor == config.WSB_OPINION_SIZE_FACTOR_MAX, sizer.last_size_factor
    assert shares == math.floor(_CASH * config.EQUAL_POSITION_PCT * 1.3 / _PRICE), shares  # 130


# --- T10: opinion_reversal 감지 (opinion_mode=True) ----------------------
def _engine() -> WSBSignalEngine:
    return WSBSignalEngine(provider=None, ranking="sentiment")


def _exit(opinion, scored_bearish=1, entry_score=80.0, entry_bear=1):
    eng = _engine()
    pos = {"symbol": "X", "entry_price": 100.0, "highest_price": 101.0, "shares": 10}
    ohlcv = {"close": 101.0, "open": 101.0, "prev_close": 100.0, "rsi": 50.0}
    scored = {"X": {"score": opinion.opinion_score, "bearish": scored_bearish}}
    psc = {"X": {"entry_score": entry_score, "entry_bearish_count": entry_bear}}
    return eng.check_exit(pos, ohlcv, scored, {}, psc, opinion_mode=True, opinion=opinion)


def test_t10_opinion_reversal_detected():
    assert _exit(_om(neutral_ratio=0.80)) == (True, "neutral_spike")
    assert _exit(_om(consensus_ratio=0.90)) == (True, "consensus_break")
    assert _exit(_om(opinion_score=40.0)) == (True, "sentiment_reversal")  # 40 < 80×0.65=52
    assert _exit(_om()) == (False, "")                                     # 정상 → 유지


# --- T11: equal 회귀 (opinion_mode=False) --------------------------------
def test_t11_equal_regression_exit():
    eng = _engine()
    pos = {"symbol": "X", "entry_price": 100.0, "highest_price": 101.0, "shares": 10}
    ohlcv = {"close": 101.0, "open": 101.0, "prev_close": 100.0, "rsi": 50.0}
    # opinion이 역전이어도 opinion_mode=False면 의견 청산 미발동
    scored = {"X": {"score": 75.0, "bearish": 1}}
    psc = {"X": {"entry_score": 80.0}}
    res = eng.check_exit(pos, ohlcv, scored, {}, psc,
                         opinion_mode=False, opinion=_om(neutral_ratio=0.9))
    assert res == (False, ""), res

    # 기존 sentiment_reversal 경로는 그대로 동작 (2일 연속 below)
    scored2 = {"X": {"score": 50.0}}      # 50 < 100×0.6=60
    psc2 = {"X": {"entry_score": 100.0, "yesterday_below": True}}
    res2 = eng.check_exit(pos, ohlcv, scored2, {}, psc2)   # opinion_mode 기본 False
    assert res2 == (True, "sentiment_reversal"), res2


# --- T12: 기존 sizer가 opinion kwarg 받아도 동작 동일 --------------------
def test_t12_existing_sizers_ignore_opinion():
    eq = EqualSizer().calc_shares(_CASH, _PRICE, opinion=_om(), bullish_ratio=0.8)
    assert eq == math.floor(_CASH / config.MAX_POSITIONS / _PRICE), eq    # 100
    sent = SentimentSizer().calc_shares(_CASH, _PRICE, opinion=_om(), bullish_ratio=0.85)
    assert sent == math.floor(_CASH * config.SENTIMENT_SIZE_HIGH / _PRICE), sent  # 0.15 → 150
    assert isinstance(get_sizer("opinion_trend"), CommunityOpinionTrendSizer)


# --- 보조: wsb_state helper ----------------------------------------------
def test_helpers():
    assert wsb_state.compute_sentiment_trend([78, 72, 65]) == "UP"
    assert wsb_state.compute_sentiment_trend([60, 70, 80]) == "DOWN"
    assert wsb_state.compute_sentiment_trend([71, 70, 70]) == "FLAT"
    assert wsb_state.compute_consensus_ratio(5, 2) == 2.5
    assert wsb_state.compute_consensus_ratio(3, 0) == config.WSB_OPINION_CONSENSUS_STRONG_RATIO
    assert wsb_state.compute_consensus_ratio(1, 0) == 0.0
    assert wsb_state.compute_persistence_days(
        [{"bullish": 5, "bearish": 1}, {"bullish": 4, "bearish": 2}, {"bullish": 1, "bearish": 3}]
    ) == 2


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\nCommunity Opinion Trend Sizing 단위 테스트 - {len(tests)}건\n" + "-" * 50)
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
