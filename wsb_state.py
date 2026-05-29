# Design Ref: §wsb-signal-v3 §2 — mention_history + position_scores 파일 I/O 전담
# Plan SC: SC-04 velocity_state 저장, SC-05/SC-06/SC-07 감성역전·RSI hold 상태 관리
import json
import logging
import os

import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mention History (mention_history.json)
# ---------------------------------------------------------------------------

def load_mention_history() -> dict[str, list[int]]:
    """
    data/mention_history.json 로드.
    {"NVDA": [10, 8, 12, 9, 11, 10, 9]}  # 최신→과거 순, 최대 7개
    파일 없으면 {} 반환 (첫 실행 안전 폴백).
    """
    path = config.MENTION_HISTORY_FILE
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"mention_history 로드 실패: {e} → 빈 이력 사용")
        return {}


def save_mention_history(history: dict[str, list[int]]) -> None:
    """mention_history 저장. data/ 디렉토리 자동 생성."""
    os.makedirs(os.path.dirname(config.MENTION_HISTORY_FILE), exist_ok=True)
    with open(config.MENTION_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def update_mention_entry(
    history: dict[str, list[int]],
    symbol: str,
    today_count: int,
    max_days: int = None,
) -> dict[str, list[int]]:
    """
    종목별 멘션 이력 업데이트 (선입선출).

    최신값을 index 0에 삽입하고 max_days 초과분 제거.
    history를 in-place 수정 후 반환.
    """
    if max_days is None:
        max_days = config.WSB_VELOCITY_LOOKBACK_DAYS
    existing = history.get(symbol, [])
    updated = [today_count] + existing
    history[symbol] = updated[:max_days]
    return history


# ---------------------------------------------------------------------------
# Position Scores (position_scores.json)
# ---------------------------------------------------------------------------

def load_position_scores() -> dict[str, dict]:
    """
    data/position_scores.json 로드.
    {
      "NVDA": {
        "entry_score": 72.0,
        "yesterday_below": false,
        "rsi_held": false
      }
    }
    파일 없으면 {} 반환.
    """
    path = config.POSITION_SCORES_FILE
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"position_scores 로드 실패: {e} → 빈 상태 사용")
        return {}


def save_position_scores(scores: dict[str, dict]) -> None:
    """position_scores 저장."""
    os.makedirs(os.path.dirname(config.POSITION_SCORES_FILE), exist_ok=True)
    with open(config.POSITION_SCORES_FILE, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)


def upsert_position_score(
    scores: dict[str, dict],
    symbol: str,
    *,
    entry_score: float | None = None,
    yesterday_below: bool | None = None,
    rsi_held: bool | None = None,
    # Design Ref: community-opinion-trend-sizing §7 — 진입 시점 의견 스냅샷
    entry_bullish_count: int | None = None,
    entry_bearish_count: int | None = None,
    entry_neutral_count: int | None = None,
    entry_neutral_ratio: float | None = None,
    entry_consensus_ratio: float | None = None,
    entry_velocity_state: str | None = None,
    entry_opinion_trend: str | None = None,
    entry_persistence_days: int | None = None,
    size_factor: float | None = None,
    stop_loss_pct: float | None = None,
    trailing_stop_pct: float | None = None,
) -> None:
    """
    특정 종목의 position_score를 부분 업데이트 (in-place).
    None인 필드는 변경하지 않음. (기존 entry_score/yesterday_below/rsi_held 호환 유지)
    """
    record = scores.setdefault(symbol, {
        "entry_score": None,
        "yesterday_below": False,
        "rsi_held": False,
    })
    # community-opinion-trend-sizing 스냅샷 필드는 제공될 때만 기록 (하위호환)
    _updates = {
        "entry_score": entry_score,
        "yesterday_below": yesterday_below,
        "rsi_held": rsi_held,
        "entry_bullish_count": entry_bullish_count,
        "entry_bearish_count": entry_bearish_count,
        "entry_neutral_count": entry_neutral_count,
        "entry_neutral_ratio": entry_neutral_ratio,
        "entry_consensus_ratio": entry_consensus_ratio,
        "entry_velocity_state": entry_velocity_state,
        "entry_opinion_trend": entry_opinion_trend,
        "entry_persistence_days": entry_persistence_days,
        "size_factor": size_factor,
        "stop_loss_pct": stop_loss_pct,
        "trailing_stop_pct": trailing_stop_pct,
    }
    for key, value in _updates.items():
        if value is not None:
            record[key] = value


def remove_position_score(scores: dict[str, dict], symbol: str) -> None:
    """청산된 종목 제거."""
    scores.pop(symbol, None)


# ---------------------------------------------------------------------------
# Score History (score_history.json) — community-opinion-trend-sizing
# Design Ref: community-opinion-trend-sizing §4.4
# 라이브 모드 영속 저장용. 백테스트 replay는 전역 파일 대신 인메모리 dict 사용(결정성).
# ---------------------------------------------------------------------------

def load_score_history() -> dict[str, list[dict]]:
    """
    data/score_history.json 로드.
    {"NVDA": [{"date","score","bullish","bearish","neutral","neutral_ratio"}, …]}  # 최신→과거
    파일 없으면 {} 반환.
    """
    path = config.SCORE_HISTORY_FILE
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"score_history 로드 실패: {e} → 빈 이력 사용")
        return {}


def save_score_history(history: dict[str, list[dict]]) -> None:
    """score_history 저장. data/ 디렉토리 자동 생성."""
    os.makedirs(os.path.dirname(config.SCORE_HISTORY_FILE), exist_ok=True)
    with open(config.SCORE_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def update_score_entry(
    history: dict[str, list[dict]],
    symbol: str,
    entry: dict,
    max_days: int = None,
) -> dict[str, list[dict]]:
    """
    종목별 일별 의견 점수 이력 업데이트 (선입선출, 최신→과거).
    entry = {"date","score","bullish","bearish","neutral","neutral_ratio"}.
    persistence 계산 버퍼를 위해 lookback보다 넉넉히 보관.
    """
    if max_days is None:
        max_days = config.WSB_OPINION_TREND_LOOKBACK_DAYS + 4
    existing = history.get(symbol, [])
    history[symbol] = ([entry] + existing)[:max_days]
    return history


# ---------------------------------------------------------------------------
# Opinion Metric 계산 helper — community-opinion-trend-sizing §4.4
# ---------------------------------------------------------------------------

def compute_consensus_ratio(bullish: int, bearish: int) -> float:
    """
    합의도 = bullish / bearish.
    bearish=0이면 bullish≥2일 때 강한 합의(STRONG_RATIO 반환), 아니면 0.0.
    """
    if bearish > 0:
        return bullish / bearish
    if bullish >= 2:
        return float(config.WSB_OPINION_CONSENSUS_STRONG_RATIO)
    return 0.0


def compute_sentiment_trend(scores: list[float], lookback: int = None) -> str:
    """
    최신→과거 순 score 리스트로 추세 판정.
    Returns: "UP" | "FLAT" | "DOWN". 데이터 2개 미만이면 "FLAT".
    최근(index 0)이 과거 끝보다 +2점 초과 상승이면 UP, -2점 미만이면 DOWN.
    """
    if lookback is None:
        lookback = config.WSB_OPINION_TREND_LOOKBACK_DAYS
    window = scores[:lookback]
    if len(window) < 2:
        return "FLAT"
    diff = window[0] - window[-1]   # 최신 - 과거
    if diff > 2.0:
        return "UP"
    if diff < -2.0:
        return "DOWN"
    return "FLAT"


def compute_persistence_days(history: list[dict]) -> int:
    """
    최신→과거 순 history에서 bullish>bearish가 연속 유지된 일수.
    오늘부터 거꾸로 세다가 bullish<=bearish인 날에서 중단.
    """
    days = 0
    for entry in history:
        if entry.get("bullish", 0) > entry.get("bearish", 0):
            days += 1
        else:
            break
    return days
