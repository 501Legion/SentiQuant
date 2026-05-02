# Design: WSB Signal V3 — Reddit 매수/매도 신호 전면 개편

**Feature**: wsb-signal-v3
**Date**: 2026-04-22
**Status**: Design
**Architecture**: Option B — 유틸 함수 분리

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 30MA가 빠른 모멘텀 종목 진입을 막고, 청산이 Stop-Loss 이후에야 발동되어 손실이 커짐. Velocity 보정으로 관심 식은 종목 자동 필터링 |
| **WHO** | Reddit 기반 페이퍼 트레이딩 시스템 운영자 |
| **RISK** | mention_history 없는 첫 실행 / position_scores 동기화 실패 / RSI 유예 후 추가 손실 |
| **SUCCESS** | 30MA 제거 후 진입 증가 / 감성 역전 청산 로그 / velocity_state 필드 저장 |
| **SCOPE** | wsb_signal_engine.py 재작성, wsb_state.py 신규, config.py 수정, reddit_portfolio.py Gap Down 임계값 수정 |

---

## 1. 아키텍처 개요

### 선택 근거 (Option B)

파일 I/O(`mention_history.json`, `position_scores.json`)를 `wsb_state.py`로 분리하여:
- `WSBSignalEngine`은 신호 로직에만 집중
- I/O 함수를 다른 모듈(backtester, 테스트 등)에서 재사용 가능
- `WSBSignalEngine`을 모킹할 때 상태 파일을 별도로 제어 가능

### 컴포넌트 구조

```
wsb_signal_engine.py          wsb_state.py (신규)
┌─────────────────────┐       ┌─────────────────────────┐
│ WSBSignalEngine     │──────►│ load_mention_history()  │
│                     │       │ save_mention_history()  │
│ run_pipeline()      │       │ load_position_scores()  │
│ _score_posts()      │       │ save_position_scores()  │
│ _apply_neutral_     │       └─────────────────────────┘
│   filter()  [신규]  │
│ _determine_         │       config.py
│   signal_v3() [신규]│──────►│ WSB_STRONG_BUY_SCORE   │
│ _apply_velocity()   │       │ WSB_BUY_SCORE           │
│   [신규]            │       │ WSB_VELOCITY_*          │
│ _filter_consensus() │       │ WSB_SENTIMENT_REVERSAL_*│
│ _filter_ma30() [삭제│       │ WSB_GAP_DOWN_PCT        │
│ _rank()             │       │ WSB_RSI_EXIT_OVERBOUGHT │
│ check_exit()  [재작성│      │ MENTION_HISTORY_FILE    │
└─────────────────────┘       │ POSITION_SCORES_FILE    │
                              └─────────────────────────┘

reddit_portfolio.py
└── Gap Down 임계값: STOP_LOSS_PCT(-7%) → WSB_GAP_DOWN_PCT(-5%)
```

---

## 2. 신규 파일: `wsb_state.py`

### 2.1 역할

`mention_history.json`과 `position_scores.json`의 로드/저장 전담. 비즈니스 로직 없음.

### 2.2 인터페이스

```python
# wsb_state.py

def load_mention_history() -> dict[str, list[int]]:
    """
    data/mention_history.json 로드.
    {"NVDA": [10, 8, 12, 9, 11, 10, 9]}  # 최신순 7일 (index 0 = 가장 최근)
    파일 없으면 {} 반환 (첫 실행 안전 폴백).
    """

def save_mention_history(history: dict[str, list[int]]) -> None:
    """mention_history 저장. data/ 디렉토리 자동 생성."""

def update_mention_entry(
    history: dict[str, list[int]],
    symbol: str,
    today_count: int,
    max_days: int = 7,
) -> dict[str, list[int]]:
    """
    특정 종목의 멘션 이력 업데이트 (선입선출, max_days 이상 초과 시 오래된 값 제거).
    history를 직접 수정하고 반환.
    """

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

def save_position_scores(scores: dict[str, dict]) -> None:
    """position_scores 저장."""

def upsert_position_score(
    scores: dict[str, dict],
    symbol: str,
    *,
    entry_score: float | None = None,
    yesterday_below: bool | None = None,
    rsi_held: bool | None = None,
) -> None:
    """
    특정 종목의 position_score를 부분 업데이트.
    None인 필드는 변경 없음. scores를 in-place 수정.
    """

def remove_position_score(scores: dict[str, dict], symbol: str) -> None:
    """청산된 종목 제거."""
```

---

## 3. `wsb_signal_engine.py` 변경 상세

### 3.1 삭제: `_filter_ma30()`

```python
# 삭제 대상
def _filter_ma30(self, symbols, ohlcv_cache): ...
```

`run_pipeline()` 내부에서도 해당 호출 라인 제거.

---

### 3.2 신규: `_apply_neutral_filter()`

```python
def _apply_neutral_filter(
    self,
    scored: dict[str, dict],
) -> dict[str, str]:
    """
    종목별 neutral/total 비율이 config.WSB_NEUTRAL_RATIO_MAX 초과 시 NEUTRAL 강제.

    Returns:
        {symbol: "NEUTRAL"} — 필터 적용된 종목만. 통과 종목은 포함 안 됨.
    """
```

**로직:**
```python
neutral_overrides = {}
for symbol, data in scored.items():
    total = data["bullish"] + data["bearish"] + data["neutral"]
    if total == 0:
        continue
    neutral_ratio = data["neutral"] / total
    if neutral_ratio > config.WSB_NEUTRAL_RATIO_MAX:
        neutral_overrides[symbol] = "NEUTRAL"
        logger.info(
            f"[중립 필터] {symbol}: 중립비율={neutral_ratio:.0%} → NEUTRAL"
        )
return neutral_overrides
```

---

### 3.3 신규: `_apply_velocity()`

```python
def _apply_velocity(
    self,
    symbol: str,
    today_mentions: int,
    history: dict[str, list[int]],
) -> tuple[float | None, str]:
    """
    Mention Velocity 계산 및 velocity_state 반환.

    Returns:
        (velocity, velocity_state)
        velocity: float (계산 불가 시 None)
        velocity_state: "HIGH_MOMENTUM" | "NORMAL" | "DECLINING"
                        | "NEW_SPIKE" | "NEW_IGNORE"
    """
```

**로직:**
```python
past = history.get(symbol, [])

# 신규 종목 (이력 없음)
if not past:
    if today_mentions >= config.WSB_NEW_SPIKE_MIN_MENTIONS:
        return None, "NEW_SPIKE"
    return None, "NEW_IGNORE"

# 이력 있는 종목
avg = sum(past) / len(past)
if avg == 0:
    return None, "NORMAL"
velocity = today_mentions / avg

if velocity > config.WSB_VELOCITY_HIGH_THRESHOLD:
    return velocity, "HIGH_MOMENTUM"
if velocity < config.WSB_VELOCITY_LOW_THRESHOLD:
    return velocity, "DECLINING"
return velocity, "NORMAL"
```

---

### 3.4 신규: `_determine_signal_v3()`

```python
def _determine_signal_v3(
    self,
    score: float,
    rsi: float,
    velocity_state: str,
) -> str:
    """
    Velocity 보정 매트릭스 기반 매수 신호 결정.
    반환값: "STRONG_BUY" | "BUY" | "NEUTRAL"
    SELL/STRONG_SELL은 Reddit 모델에서 생성하지 않음.
    """
```

**Velocity 보정 매트릭스 구현:**
```python
adjust = config.WSB_VELOCITY_SCORE_ADJUST  # 5.0

thresholds = {
    "HIGH_MOMENTUM": (
        config.WSB_STRONG_BUY_SCORE - adjust,  # 65
        config.WSB_BUY_SCORE - adjust,          # 50
    ),
    "NORMAL": (
        config.WSB_STRONG_BUY_SCORE,            # 70
        config.WSB_BUY_SCORE,                   # 55
    ),
    "DECLINING": (
        config.WSB_STRONG_BUY_SCORE + adjust,   # 75
        config.WSB_BUY_SCORE + adjust,           # 60
    ),
    "NEW_SPIKE": (
        config.WSB_NEW_SPIKE_SCORE,             # 65
        config.WSB_BUY_SCORE - adjust,          # 50
    ),
    "NEW_IGNORE": (float("inf"), float("inf")),  # 항상 NEUTRAL
}

sb_threshold, buy_threshold = thresholds.get(velocity_state, thresholds["NORMAL"])

if score > sb_threshold and rsi < config.RSI_OVERSOLD:       # rsi < 30
    return "STRONG_BUY"
if score > buy_threshold and config.RSI_OVERSOLD <= rsi < 50: # 30 ≤ rsi < 50
    return "BUY"
return "NEUTRAL"
```

---

### 3.5 재작성: `check_exit()`

```python
def check_exit(
    self,
    position: dict,
    today_ohlcv: dict,
    scored: dict[str, dict],
    ohlcv_cache: dict[str, pd.DataFrame],
    position_scores: dict[str, dict],
    velocity_state: str = "NORMAL",
    holding_days: int = 0,
) -> tuple[bool, str]:
    """
    5단계 우선순위 청산 조건.

    Args:
        position_scores: wsb_state.load_position_scores() 결과 (in-place 수정됨)
        velocity_state: 오늘 velocity 상태 (RSI 유예 판단용)

    Returns:
        (should_exit, reason)
        reason: "sentiment_reversal" | "rsi_overbought" | "rsi_hold"
                | "gap_down" | "stop_loss" | "trailing_stop" | ""
    """
```

**청산 우선순위 구현:**
```python
symbol = position["symbol"]
entry_price = position["entry_price"]
highest_price = position["highest_price"]
close = today_ohlcv.get("close")
open_price = today_ohlcv.get("open")
prev_close = today_ohlcv.get("prev_close")

if close is None or entry_price <= 0:
    return False, ""

pnl_pct = (close - entry_price) / entry_price * 100
drawdown = (close - highest_price) / highest_price * 100

ps = position_scores.get(symbol, {})
entry_score = ps.get("entry_score")
sym_scored = scored.get(symbol, {})
today_score = sym_scored.get("score")

# 1. 감성 역전 (2일 연속)
if entry_score is not None and today_score is not None:
    reversal_threshold = entry_score * config.WSB_SENTIMENT_REVERSAL_RATIO
    today_below = today_score < reversal_threshold
    if today_below and ps.get("yesterday_below", False):
        return True, "sentiment_reversal"
    # yesterday_below 갱신 (caller가 save_position_scores 호출)
    wsb_state.upsert_position_score(
        position_scores, symbol, yesterday_below=today_below
    )

# 2. RSI 과매수 × Velocity 교차
rsi = today_ohlcv.get("rsi")
if rsi is not None and rsi > config.WSB_RSI_EXIT_OVERBOUGHT:
    rsi_held = ps.get("rsi_held", False)
    if not rsi_held and velocity_state == "HIGH_MOMENTUM":
        # 1회 유예
        wsb_state.upsert_position_score(
            position_scores, symbol, rsi_held=True
        )
        return False, "rsi_hold"
    else:
        return True, "rsi_overbought"

# 3. Gap Down
if open_price is not None and prev_close is not None and prev_close > 0:
    gap_pct = (open_price - prev_close) / prev_close * 100
    if gap_pct <= config.WSB_GAP_DOWN_PCT:
        return True, "gap_down"

# 4. Stop-Loss
if pnl_pct <= config.STOP_LOSS_PCT:
    return True, "stop_loss"

# 5. Trailing Stop
if drawdown <= config.TRAILING_STOP_PCT and pnl_pct > 0:
    return True, "trailing_stop"

return False, ""
```

---

### 3.6 `run_pipeline()` 변경

```python
def run_pipeline(
    self,
    posts_by_symbol: dict[str, list[dict]],
    ohlcv_cache: dict[str, pd.DataFrame],
    date_str: str = None,
) -> tuple[list[str], list[dict]]:
```

**변경 흐름:**
```
기존:
  _score_posts → _filter_consensus → _filter_ma30 → _rank

신규:
  _score_posts
    → _apply_neutral_filter  (NEUTRAL 강제 종목 제외)
    → _apply_velocity         (velocity_state 계산)
    → _determine_signal_v3    (신호 결정)
    → _filter_consensus       (유지: 진입 품질 2차 필터)
    → _rank                   (유지)
    → wsb_state.update_mention_entry + save_mention_history
```

**`signal_details`에 추가 필드:**
```python
{
    "symbol": symbol,
    "bullish": ...,
    "bearish": ...,
    "neutral": ...,
    "ratio": ...,
    "mentions": ...,
    "score": ...,
    "velocity": velocity,           # 신규
    "velocity_state": velocity_state, # 신규
    "neutral_ratio": neutral_ratio,  # 신규
    "neutral_filtered": is_filtered, # 신규
    "signal": signal,               # 신규 (V3 결정 신호)
    "passed_consensus": ...,
    "in_top_n": ...,
    "rank": ...,
}
```

---

## 4. `reddit_portfolio.py` 변경

### 4.1 Gap Down 임계값 수정

```python
# 기존 (라인 93)
if gap_pct <= config.STOP_LOSS_PCT:   # -7%

# 신규
if gap_pct <= config.WSB_GAP_DOWN_PCT:  # -5%
```

### 4.2 `process_day()` 시그니처 변경

`check_exit()`이 `position_scores`와 `velocity_state`를 필요로 하므로, `process_day()` 호출 측에서 전달:

```python
def process_day(
    self,
    date_str: str,
    top_n: list[str],
    exit_signals: dict[str, str],
    ohlcv: dict[str, dict],
    sizer: PositionSizer,
    scored: dict[str, dict] = None,
    atr_cache: dict[str, float] = None,
) -> dict:
```

> **변경 없음**: `exit_signals`는 기존처럼 외부에서 계산해 전달. `check_exit()`은 WSBSignalEngine에서 호출하고 그 결과를 `exit_signals`로 넘기는 방식 유지.

### 4.3 `process_day()` 내 `_buy()` 호출 시 entry_score 저장

신규 포지션 매수 시 `position_scores`에 `entry_score` 기록:

```python
# _buy() 이후 position_scores 갱신 (호출 측에서 처리)
wsb_state.upsert_position_score(scores, symbol, entry_score=today_score)
wsb_state.save_position_scores(scores)
```

---

## 5. `config.py` 추가 상수

```python
# --- WSB Signal V3: 매수 기준 ---
WSB_STRONG_BUY_SCORE = 70.0
WSB_BUY_SCORE = 55.0
WSB_NEUTRAL_RATIO_MAX = 0.70

# --- WSB Signal V3: Mention Velocity ---
WSB_VELOCITY_LOOKBACK_DAYS = 7
WSB_VELOCITY_HIGH_THRESHOLD = 2.0
WSB_VELOCITY_LOW_THRESHOLD = 0.5
WSB_VELOCITY_SCORE_ADJUST = 5.0
WSB_NEW_SPIKE_MIN_MENTIONS = 20
WSB_NEW_SPIKE_SCORE = 65.0

# --- WSB Signal V3: 청산 조건 ---
WSB_SENTIMENT_REVERSAL_RATIO = 0.60
WSB_RSI_EXIT_OVERBOUGHT = 70.0
WSB_GAP_DOWN_PCT = -5.0
WSB_RSI_HOLD_ONCE = True

# --- 데이터 파일 ---
MENTION_HISTORY_FILE = "data/mention_history.json"
POSITION_SCORES_FILE = "data/position_scores.json"
```

---

## 6. `indicators.py` 추가: `calculate_atr()`

```python
def calculate_atr(ohlcv_df: pd.DataFrame, period: int = None) -> float | None:
    """
    ATR(Average True Range) 계산 — wsb-atr-stop 피처를 위한 준비.
    이번 버전에서는 계산만 제공하고 신호에 미연동.

    Returns:
        float: 최신 ATR 값. 데이터 부족 시 None.
    """
    if period is None:
        period = config.ATR_PERIOD  # 14
    if ohlcv_df.empty or len(ohlcv_df) < period + 1:
        return None
    high = ohlcv_df["high"]
    low = ohlcv_df["low"]
    close = ohlcv_df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    return float(atr.iloc[-1]) if not atr.empty else None
```

---

## 7. 데이터 흐름

```
매일 실행 순서:

1. reddit_collector → posts_by_symbol
2. WSBSignalEngine.run_pipeline()
   a. _score_posts() → scored (bullish/bearish/neutral/score)
   b. wsb_state.load_mention_history() → history
   c. _apply_velocity() → velocity, velocity_state (종목별)
   d. _apply_neutral_filter() → neutral_overrides
   e. _determine_signal_v3() → signal (종목별)
   f. _filter_consensus() → 진입 후보
   g. _rank() → top_n
   h. wsb_state.update_mention_entry() → history 갱신
   i. wsb_state.save_mention_history()

3. wsb_state.load_position_scores() → scores
4. 보유 포지션별 check_exit() 호출
   → position_scores in-place 수정 (yesterday_below, rsi_held)
5. wsb_state.save_position_scores()

6. RedditPortfolio.process_day()
   → Gap Down 체크 (WSB_GAP_DOWN_PCT)
   → exit_signals 청산
   → top_n 신규 매수
   → 매수 시 wsb_state.upsert_position_score(entry_score)
   → wsb_state.save_position_scores()
```

---

## 8. 테스트 시나리오

| SC | 시나리오 | 기대 결과 |
|----|----------|-----------|
| SC-01 | `run_pipeline()` 실행 — `passed_ma` 단계 없음 | 로그에 `30MA` 관련 출력 없음 |
| SC-02 | neutral/total = 0.75인 종목 | `[중립 필터]` 로그 + NEUTRAL |
| SC-03 | score=52, rsi=40, NORMAL | NEUTRAL (BUY 기준 55 미충족) |
| SC-04 | score=52, rsi=40, HIGH_MOMENTUM | BUY (기준 50으로 완화) |
| SC-05 | 신규 종목, mentions=25 | NEW_SPIKE → score>65이면 STRONG_BUY |
| SC-06 | 신규 종목, mentions=10 | NEW_IGNORE → NEUTRAL |
| SC-07 | 감성 역전 2일 연속 | `sentiment_reversal` 청산 |
| SC-08 | RSI=72, HIGH_MOMENTUM, rsi_held=false | `rsi_hold` (유예), rsi_held=true 저장 |
| SC-09 | RSI=72, HIGH_MOMENTUM, rsi_held=true | `rsi_overbought` 즉시 청산 |
| SC-10 | RSI=72, NORMAL | `rsi_overbought` 즉시 청산 |
| SC-11 | open = prev_close × 0.94 | `gap_down` 청산 |
| SC-12 | mention_history.json 없는 첫 실행 | `{}` 반환, NEW_IGNORE/NEW_SPIKE 분기 정상 |

---

## 9. 엣지 케이스

| 케이스 | 처리 방법 |
|--------|-----------|
| position_scores.json 없음 | `load_position_scores()` → `{}` 반환, entry_score=None → 감성 역전 체크 생략 |
| 종목 이력 1~6일 (7일 미만) | 있는 이력으로 평균 계산 (partial average 허용) |
| bullish=0, bearish=0 | ratio=0, score=0 → NEUTRAL |
| today_score == entry_score × 0.6 정확히 | `<` 조건 → 미충족 → 유예 |
| rsi=None (계산 실패) | RSI 청산 조건 스킵 |

---

## 10. 마이그레이션 주의

| 항목 | 내용 |
|------|------|
| 기존 `check_exit()` 시그니처 변경 | `position_scores`, `velocity_state` 인자 추가 — 호출 측(reddit_backtester.py 등) 수정 필요 |
| `reddit_backtester.py` | `check_exit()` 호출 시 `position_scores={}`, `velocity_state="NORMAL"` 기본값 전달로 하위 호환 유지 |
| `run_pipeline()` 반환값 변경 | `signal_details`에 `velocity`, `velocity_state`, `neutral_ratio`, `neutral_filtered`, `signal` 추가 |

---

## 11. 구현 가이드

### 11.1 Module Map

| Module | 파일 | 작업 | 의존성 |
|--------|------|------|--------|
| M1 | `config.py` | WSB V3 상수 추가 | 없음 |
| M2 | `wsb_state.py` | 파일 I/O 함수 4종 신규 | M1 |
| M3 | `wsb_signal_engine.py` | `_filter_ma30()` 삭제 | M1 |
| M4 | `wsb_signal_engine.py` | `_apply_neutral_filter()` 신규 | M1 |
| M5 | `wsb_signal_engine.py` | `_apply_velocity()` 신규 | M1, M2 |
| M6 | `wsb_signal_engine.py` | `_determine_signal_v3()` 신규 | M1 |
| M7 | `wsb_signal_engine.py` | `run_pipeline()` 수정 (M3~M6 통합) | M2~M6 |
| M8 | `wsb_signal_engine.py` | `check_exit()` 재작성 | M1, M2 |
| M9 | `reddit_portfolio.py` | Gap Down 임계값 수정 + entry_score 저장 | M1, M2 |
| M10 | `indicators.py` | `calculate_atr()` 추가 (미연동) | M1 |

### 11.2 권장 구현 세션

**Session 1** (M1~M4): config 상수 + wsb_state.py + neutral_filter — 낮은 복잡도, 즉시 검증 가능

**Session 2** (M5~M7): velocity + signal_v3 + run_pipeline 통합 — 핵심 로직, 단위 테스트 권장

**Session 3** (M8~M10): check_exit 재작성 + portfolio 수정 + ATR 준비 — 청산 로직

### 11.3 Session Guide

```
/pdca do wsb-signal-v3 --scope M1,M2,M3,M4    # Session 1
/pdca do wsb-signal-v3 --scope M5,M6,M7        # Session 2
/pdca do wsb-signal-v3 --scope M8,M9,M10       # Session 3
```
