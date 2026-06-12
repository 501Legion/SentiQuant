# funnel-fix 2026-06-13 — 신호 깔때기 정상화 회귀 테스트.
# 배경: 라이브 에이전트가 가동 후 매수 0건 (중립 킬스위치·극소표본 랭킹·역추세 RSI 창).
# 1) score 표본 수축: 방향성 멘션 n이 적을수록 50으로 수축 (극소표본 노이즈 랭킹 차단)
# 2) 중립 필터 → 방향성 멘션 최소치(3) + 극단 노이즈 컷(0.95)
# 3) RSI 매수 창 30~50 폐지 → 과매수(>=70)만 회피
import pytest

import config
from wsb_signal_engine import WSBSignalEngine


class _StubProvider:
    """details의 finbert_label을 그대로 흘려보내는 감성 Provider 스텁."""

    def __init__(self, score: float, labels: list[str]):
        self._score = score
        self._labels = labels

    def score(self, posts):
        details = [{"finbert_label": lab} for lab in self._labels]
        return self._score, details


def _engine(score: float, labels: list[str]) -> WSBSignalEngine:
    return WSBSignalEngine(_StubProvider(score, labels), ranking="sentiment")


def _scored(score: float, labels: list[str], symbol: str = "NVDA") -> dict:
    eng = _engine(score, labels)
    return eng._score_posts({symbol: [{"title": "t"}] * len(labels)})[symbol]


# --- 1) score shrinkage ---

def test_shrinkage_single_mention_pulled_to_neutral():
    # 글 1개짜리 raw 90 → 50 근처로 수축 (기존: 90 그대로 랭킹 최상위)
    d = _scored(90.0, ["positive"])
    assert d["score_raw"] == 90.0
    expected = 50.0 + 40.0 * 1 / (1 + config.WSB_SCORE_SHRINKAGE_K)
    assert d["score"] == pytest.approx(expected, abs=0.01)
    # 단독 멘션은 점수가 크게 압축되고, 진입은 방향성 최소치(3) 필터가 차단
    assert d["score"] < 55.0
    assert config.WSB_MIN_DIRECTIONAL_MENTIONS > 1


def test_shrinkage_large_sample_keeps_score():
    # 방향성 16건이면 raw 80의 2/3을 유지 → 임계값 통과 가능
    labels = ["positive"] * 14 + ["negative"] * 2
    d = _scored(80.0, labels)
    expected = 50.0 + 30.0 * 16 / (16 + config.WSB_SCORE_SHRINKAGE_K)
    assert d["score"] == pytest.approx(expected, abs=0.01)
    assert d["score"] > config.WSB_BUY_SCORE


def test_shrinkage_preserves_bearish_direction():
    # 약세 score도 동일하게 50쪽으로 수축 (방향 부호 유지)
    d = _scored(10.0, ["negative"] * 8)
    assert 10.0 < d["score"] < 50.0


# --- 2) 방향성 멘션 최소치 + 극단 노이즈 컷 ---

def test_neutral_filter_blocks_low_directional():
    eng = _engine(85.0, ["positive", "neutral", "neutral"])
    scored = eng._score_posts({"NOK": [{"title": "t"}] * 3})
    overrides = eng._apply_neutral_filter(scored)
    assert overrides.get("NOK") == "NEUTRAL"  # 방향성 1건 < 3


def test_neutral_filter_allows_high_neutral_with_directional_mass():
    # 중립 76%라도 방향성 15건이면 통과 (기존 0.75 킬스위치는 차단했음)
    labels = ["positive"] * 11 + ["negative"] * 4 + ["neutral"] * 48
    eng = _engine(70.0, labels)
    scored = eng._score_posts({"JPM": [{"title": "t"}] * len(labels)})
    overrides = eng._apply_neutral_filter(scored)
    assert "JPM" not in overrides


def test_neutral_filter_extreme_noise_still_blocked():
    # 방향성 3건이어도 중립 > 0.95면 극단 노이즈로 차단
    labels = ["positive"] * 3 + ["neutral"] * 97
    eng = _engine(70.0, labels)
    scored = eng._score_posts({"ORCL": [{"title": "t"}] * len(labels)})
    overrides = eng._apply_neutral_filter(scored)
    assert overrides.get("ORCL") == "NEUTRAL"


# --- 3) RSI 게이트: 과매수만 회피 ---

def test_buy_allowed_above_rsi50():
    eng = _engine(60.0, [])
    # 기존: RSI 60이면 무조건 NEUTRAL. 변경: RSI < 70이면 score로 결정.
    assert eng._determine_signal_v3(60.0, 60.0, "NORMAL") == "BUY"
    assert eng._determine_signal_v3(69.0, 60.0, "NORMAL") == "STRONG_BUY"


def test_overbought_rsi_blocks_buy():
    eng = _engine(60.0, [])
    assert eng._determine_signal_v3(80.0, config.WSB_RSI_BUY_MAX, "NORMAL") == "NEUTRAL"
    assert eng._determine_signal_v3(80.0, 75.0, "NORMAL") == "NEUTRAL"


def test_strong_buy_no_longer_requires_oversold():
    eng = _engine(60.0, [])
    # 기존: STRONG_BUY는 RSI < 30 필수 → 사실상 미발동. 변경: score만으로 결정.
    assert eng._determine_signal_v3(75.0, 45.0, "NORMAL") == "STRONG_BUY"


def test_new_ignore_still_blocked():
    eng = _engine(60.0, [])
    assert eng._determine_signal_v3(99.0, 40.0, "NEW_IGNORE") == "NEUTRAL"
