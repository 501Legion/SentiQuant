"""DailyOpinionSnapshot + source quality/ambiguity 단위 테스트
(community-opinion-agent §3.6·§4 / Plan FR-1.1~5).

실행:
  pytest tests/test_community_opinion_snapshot.py
  python tests/test_community_opinion_snapshot.py
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
import wsb_state
import reddit_collector as rc
from wsb_signal_engine import (
    DailyOpinionSnapshot, build_daily_snapshot, compute_weighted_counts,
)


# --- T1: low quality flair → weight 0 ---
def test_t1_low_quality_flair_zero():
    assert rc.source_quality_weight("Meme", "post") == 0.0
    assert rc.source_quality_weight("Loss", "post") == 0.0


# --- T2: DD flair → 높은 weight ---
def test_t2_dd_high_weight():
    assert rc.source_quality_weight("DD", "post") == config.COMMUNITY_FLAIR_WEIGHT_DD
    assert rc.source_quality_weight("DD", "post") > rc.source_quality_weight("Options", "post")


# --- T3: daily_thread / fallback weight ---
def test_t3_daily_thread_and_fallback():
    assert rc.source_quality_weight("", "daily_thread") == config.COMMUNITY_FLAIR_WEIGHT_DAILY_THREAD
    assert rc.source_quality_weight(None, "post") == config.COMMUNITY_FLAIR_WEIGHT_DEFAULT  # 1.0


# --- T4: title mention이 body mention보다 높은 weight ---
def test_t4_title_gt_body_weight():
    wb_title, *_ = compute_weighted_counts(
        [{"label": "bullish", "source_quality_weight": 1.0, "location": "title"}])
    wb_body, *_ = compute_weighted_counts(
        [{"label": "bullish", "source_quality_weight": 1.0, "location": "body"}])
    wb_comment, *_ = compute_weighted_counts(
        [{"label": "bullish", "source_quality_weight": 1.0, "location": "comment"}])
    assert wb_title > wb_body > wb_comment


# --- T5: low quality flair(weight 0)는 가중 카운트에 기여 0 ---
def test_t5_low_quality_zero_contribution():
    wb, _, _, _ = compute_weighted_counts(
        [{"label": "bullish", "source_quality_weight": 0.0, "location": "title"}])
    assert wb == 0.0


# --- T6: ambiguity ticker는 일반 단어로 등장하면 제외 ---
def test_t6_ambiguity_excluded_bareword():
    assert rc.is_ambiguous_ticker("ALL", has_dollar=False) is True
    assert rc.is_ambiguous_ticker("NOW", has_dollar=False) is True
    assert rc.is_ambiguous_ticker("NVDA", has_dollar=False) is False


# --- T7: $가 붙은 single-letter ticker는 인정 ---
def test_t7_dollar_single_letter_accepted():
    assert rc.is_ambiguous_ticker("F", has_dollar=False) is True    # $ 없음 → 제외
    assert rc.is_ambiguous_ticker("F", has_dollar=True) is False    # $F → 인정
    assert rc.is_ambiguous_ticker("ALL", has_dollar=True) is False  # $ALL → 인정


# --- T8: neutral_ratio > COMMUNITY_NEUTRAL_RATIO_MAX(0.90) → consensus_buy False ---
def test_t8_high_neutral_no_consensus_buy():
    snap = build_daily_snapshot(
        "X",
        {"bullish": 3, "bearish": 1, "neutral": 60, "score": 72,
         "mentions": 64, "neutral_ratio": 0.94, "velocity_state": "NORMAL"},
        history=[],
    )
    assert snap.is_consensus_buy is False


# --- T9: bullish/bearish ratio ≥ 1.5 → consensus_buy True ---
def test_t9_strong_ratio_consensus_buy():
    snap = build_daily_snapshot(
        "X",
        {"bullish": 5, "bearish": 1, "neutral": 1, "score": 75,
         "mentions": 7, "neutral_ratio": 0.14, "velocity_state": "NORMAL"},
        history=[],
    )
    assert snap.is_consensus_buy is True
    assert snap.consensus_ratio >= config.COMMUNITY_CONSENSUS_MIN_RATIO
    # 약한 합의는 False
    weak = build_daily_snapshot(
        "Y",
        {"bullish": 2, "bearish": 2, "neutral": 1, "score": 70,
         "mentions": 5, "neutral_ratio": 0.20, "velocity_state": "NORMAL"},
        history=[],
    )
    assert weak.is_consensus_buy is False


# --- T10: query_* / summary 분리 생성 ---
def test_t10_query_fields_generated():
    snap = build_daily_snapshot(
        "TSLA",
        {"bullish": 4, "bearish": 1, "neutral": 1, "score": 78,
         "mentions": 6, "neutral_ratio": 0.16, "velocity_state": "HIGH_MOMENTUM"},
        history=[{"score": 78, "bullish": 4, "bearish": 1},
                 {"score": 70, "bullish": 3, "bearish": 1}],
    )
    assert "TSLA" in snap.summary
    assert snap.query_consensus and snap.query_risk and snap.query_opinion_trend
    assert snap.attention_state == "RISING"   # HIGH_MOMENTUM → RISING
    # weighted = raw (labeled_posts 없음)
    assert snap.weighted_bullish_count == 4.0


# --- T11: DailyOpinionSnapshot jsonl 저장/로드 ---
def test_t11_snapshot_jsonl_roundtrip():
    snap = build_daily_snapshot(
        "NVDA",
        {"bullish": 6, "bearish": 1, "neutral": 1, "score": 82,
         "mentions": 8, "neutral_ratio": 0.12, "velocity_state": "NORMAL"},
        history=[],
        date_str="2026-03-02",
    )
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "snap.jsonl")
        wsb_state.append_daily_snapshot(snap, path=path)
        wsb_state.append_daily_snapshot(snap, path=path)
        rows = wsb_state.load_daily_snapshots(path=path)
    assert len(rows) == 2
    assert rows[0]["symbol"] == "NVDA"
    assert rows[0]["opinion_score"] == 82
    assert rows[0]["is_consensus_buy"] is True


# --- T12: snapshot OFF → append no-op (회귀 0) ---
def test_t12_snapshot_disabled_noop():
    orig = config.COMMUNITY_ENABLE_DAILY_OPINION_SNAPSHOT
    try:
        config.COMMUNITY_ENABLE_DAILY_OPINION_SNAPSHOT = False
        snap = build_daily_snapshot(
            "X", {"bullish": 3, "bearish": 1, "neutral": 1, "score": 70,
                  "mentions": 5, "neutral_ratio": 0.2, "velocity_state": "NORMAL"},
            history=[])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "snap.jsonl")
            wsb_state.append_daily_snapshot(snap, path=path)
            assert not os.path.exists(path)  # 저장 안 됨
    finally:
        config.COMMUNITY_ENABLE_DAILY_OPINION_SNAPSHOT = orig


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\nDailyOpinionSnapshot 단위 테스트 - {len(tests)}건\n" + "-" * 50)
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
