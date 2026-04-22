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
) -> None:
    """
    특정 종목의 position_score를 부분 업데이트 (in-place).
    None인 필드는 변경하지 않음.
    """
    record = scores.setdefault(symbol, {
        "entry_score": None,
        "yesterday_below": False,
        "rsi_held": False,
    })
    if entry_score is not None:
        record["entry_score"] = entry_score
    if yesterday_below is not None:
        record["yesterday_below"] = yesterday_below
    if rsi_held is not None:
        record["rsi_held"] = rsi_held


def remove_position_score(scores: dict[str, dict], symbol: str) -> None:
    """청산된 종목 제거."""
    scores.pop(symbol, None)
