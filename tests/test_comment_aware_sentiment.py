"""comment-aware-sentiment 단위 테스트 (M5 / Plan SC-03,04,07).

본문+댓글 개별 확장(M3) + engine labeled_posts 가중 배선(M4) 검증.
FinBERT는 가짜 파이프라인으로 대체 — 모델 로드/파일 I/O 없이 결정적.

실행:
  pytest tests/test_comment_aware_sentiment.py
  python tests/test_comment_aware_sentiment.py
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
import indicators
from sentiment_provider import FinBERTProvider
from wsb_signal_engine import (
    WSBSignalEngine, build_daily_snapshot, compute_weighted_counts,
)


# --- 가짜 FinBERT 파이프라인 (키워드 기반 결정적 분류) ---
def _fake_pipe(text, truncation=True):
    t = (text or "").lower()
    if any(k in t for k in ("buy", "call", "moon", "bull")):
        winner = "positive"
    elif any(k in t for k in ("sell", "put", "crash", "bear")):
        winner = "negative"
    else:
        winner = "neutral"
    scores = {"positive": 0.05, "negative": 0.05, "neutral": 0.05}
    scores[winner] = 0.90
    return [[{"label": k, "score": v} for k, v in scores.items()]]


def _finbert_with_fake() -> FinBERTProvider:
    """indicators._get_finbert_pipeline을 가짜로 대체한 FinBERTProvider."""
    indicators._get_finbert_pipeline = lambda: _fake_pipe  # type: ignore[attr-defined]
    return FinBERTProvider(use_wsb_preprocessor=False)


class _StubProvider:
    """미리 정한 details를 반환하는 provider (engine 배선 검증용)."""

    def __init__(self, details: list[dict], score: float = 70.0):
        self._details = details
        self._score = score

    def score(self, posts):
        return self._score, self._details


# --- TC-03a: _expand_articles — 본문 + 댓글 개별 unit ---
def test_tc03a_expand_body_and_comments():
    arts = [{
        "title": "NVDA DD", "body_excerpt": "thesis",
        "source_quality_weight": 1.4,
        "top_comments": ["great calls", "to the moon", "bearish puts"],
    }]
    units = FinBERTProvider._expand_articles(arts)
    assert len(units) == 4  # body + 3 comments
    assert units[0]["location"] == "body"
    assert all(u["location"] == "comment" for u in units[1:])
    # 댓글은 부모 글의 source_quality_weight 상속
    assert all(u["source_quality_weight"] == 1.4 for u in units)
    assert units[1]["body_excerpt"] == "great calls"


# --- TC-03b: score() — 본문+댓글 각각 개별 detail, location 표식 ---
def test_tc03b_score_details_have_location():
    prov = _finbert_with_fake()
    arts = [{
        "title": "x", "body_excerpt": "buy now",
        "source_quality_weight": 1.0,
        "top_comments": ["calls calls", "puts puts"],
    }]
    _score, details = prov.score(arts)
    assert len(details) == 3  # body + 2 comments
    locs = [d["location"] for d in details]
    assert locs.count("body") == 1
    assert locs.count("comment") == 2
    assert all("source_quality_weight" in d for d in details)
    # body=buy→positive, c1=calls→positive, c2=puts→negative
    assert details[0]["finbert_label"] == "positive"
    assert details[2]["finbert_label"] == "negative"


# --- TC-04: 가중 카운트 — 본문(1.0)+댓글(0.5), raw N=글+댓글 ---
def test_tc04_weighted_body_comment():
    details = [
        {"finbert_label": "positive", "location": "body",
         "source_quality_weight": 1.0, "included": True},
        {"finbert_label": "positive", "location": "comment",
         "source_quality_weight": 1.0, "included": True},
    ]
    eng = WSBSignalEngine(_StubProvider(details))
    scored = eng._score_posts({"NVDA": [{"x": 1}]})
    lp = scored["NVDA"]["labeled_posts"]
    assert len(lp) == 2
    wb, wbear, wneut, _sqs = compute_weighted_counts(lp)
    # body(BODY_WEIGHT) + comment(COMMENT_WEIGHT) — 상수 기반(튜닝에 안 깨짐)
    expected = config.COMMUNITY_BODY_MENTION_WEIGHT + config.COMMUNITY_COMMENT_MENTION_WEIGHT
    assert wb == round(expected, 4)
    # 댓글 가중 < 1 (본문보다 작음)
    assert config.COMMUNITY_COMMENT_MENTION_WEIGHT < config.COMMUNITY_BODY_MENTION_WEIGHT
    # raw bullish count = 2 (글+댓글 각 1)
    assert scored["NVDA"]["bullish"] == 2


# --- TC-05: N 게이트 — 댓글 포함 n_valid≥10 → 폴백 미발생 ---
def test_tc05_n_gate_includes_comments():
    prov = _finbert_with_fake()
    arts = [{
        "title": "", "body_excerpt": "meh whatever",  # neutral → included=False
        "source_quality_weight": 1.0,
        "top_comments": ["calls"] * 12,                # 12 positive 댓글
    }]
    score, details = prov.score(arts)
    assert len(details) == 13  # body + 12 comments
    valid = [d for d in details if d["included"]]
    assert len(valid) >= config.NEUTRAL_FILTER_MIN_ARTICLES  # 댓글이 N 채움
    # 폴백(avg p-n)이 아니라 pos/(pos+neg) 공식 → 전부 positive → 100.0
    assert score == 100.0


# --- TC-06a: 공용 경로 — _score_posts가 labeled_posts 생성, snapshot 가중 적용 ---
def test_tc06a_shared_path_weighted_snapshot():
    details = [
        {"finbert_label": "positive", "location": "body", "source_quality_weight": 1.0},
        {"finbert_label": "positive", "location": "comment", "source_quality_weight": 1.0},
    ]
    eng = WSBSignalEngine(_StubProvider(details))
    scored = eng._score_posts({"AMD": [{"x": 1}]})
    assert "labeled_posts" in scored["AMD"]
    snap = build_daily_snapshot(
        "AMD", scored["AMD"], history=[],
        labeled_posts=scored["AMD"]["labeled_posts"],
    )
    # 가중(body+comment) != raw(2) → 댓글 가중 활성화 확인 (상수 기반)
    expected = config.COMMUNITY_BODY_MENTION_WEIGHT + config.COMMUNITY_COMMENT_MENTION_WEIGHT
    assert snap.weighted_bullish_count == round(expected, 4)
    assert snap.weighted_bullish_count != snap.bullish_count  # 가중 ≠ raw
    assert snap.bullish_count == 2


# --- TC-06b: 라이브 경로 — run_pipeline signal_details가 labeled_posts 전파 ---
def test_tc06b_run_pipeline_propagates_labeled_posts():
    import wsb_state as ws
    orig = (ws.load_mention_history, ws.save_mention_history, ws.update_mention_entry)
    ws.load_mention_history = lambda: {}
    ws.save_mention_history = lambda h: None
    ws.update_mention_entry = lambda h, s, m: None
    try:
        details = [
            {"finbert_label": "positive", "location": "body", "source_quality_weight": 1.0},
            {"finbert_label": "positive", "location": "comment", "source_quality_weight": 1.0},
            {"finbert_label": "positive", "location": "comment", "source_quality_weight": 1.0},
        ]
        eng = WSBSignalEngine(_StubProvider(details))
        _top, sig = eng.run_pipeline({"AMD": [{"x": 1}]}, {})
        entry = next(s for s in sig if s["symbol"] == "AMD")
        assert "labeled_posts" in entry
        assert len(entry["labeled_posts"]) == 3
    finally:
        ws.load_mention_history, ws.save_mention_history, ws.update_mention_entry = orig


# --- TC-08: 단일 DD(글 1개) → 라이브 consensus-buy 게이트 통과 (G2 / Open-2 fix) ---
def test_tc08_single_dd_passes_consensus_gate():
    # 글 1개 DD + 댓글 가중(9×0.5)으로 강한 bullish. mentions(글 수)=1.
    # COMMUNITY_MIN_DAILY_MENTIONS=1 완화로 단일 DD가 매수 게이트 통과해야 함.
    labeled = [{"label": "bullish", "location": "body", "source_quality_weight": 1.0}]
    labeled += [{"label": "bullish", "location": "comment",
                 "source_quality_weight": 1.0}] * 9
    scored_entry = {
        "bullish": 10, "bearish": 0, "neutral": 0, "score": 80.0,
        "mentions": 1, "neutral_ratio": 0.0, "velocity_state": "NORMAL",
    }
    snap = build_daily_snapshot(
        "NVDA", scored_entry, history=[], labeled_posts=labeled,
    )
    assert snap.total_mentions == 1                       # 글 1개
    assert config.COMMUNITY_MIN_DAILY_MENTIONS <= 1       # 완화 확인
    assert snap.is_consensus_buy is True                  # 단일 DD 매수 통과


# --- TC-07: 뉴스 무영향 — top_comments 없는 article → 확장 0, 기존 동작 ---
def test_tc07_news_no_comments_unaffected():
    arts = [{"title": "Fed news", "description": "rate decision today"}]
    units = FinBERTProvider._expand_articles(arts)
    assert len(units) == 1                       # 본문만, 댓글 확장 0
    assert units[0]["location"] == "body"
    prov = _finbert_with_fake()
    _score, details = prov.score(arts)
    assert len(details) == 1
    assert details[0]["location"] == "body"


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\ncomment-aware-sentiment 단위 테스트 - {len(tests)}건\n" + "-" * 50)
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
