# Design: 뉴스+Reddit 5-Model 감성 분석 비교 전략

**Feature**: reddit-gpt4-quant
**Date**: 2026-04-17
**Architecture**: Option B — Clean Architecture
**Status**: Design

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 뉴스 vs Reddit, TextBlob vs FinBERT vs GPT-4, Equal vs Sentiment vs Volatility 실증 비교 |
| **WHO** | news-rsi-trading 시스템 운영자 |
| **RISK** | GPT-4 비용 / PRAW rate limit / Reddit 과거 데이터 접근 / ATR 계산 OHLCV 필요 |
| **SUCCESS** | 15가지 전략 각각 수익률 비교 출력, 캐시 재활용, 기존 뉴스 로직 동작 유지 |
| **SCOPE** | 신규 5개(reddit_collector, wsb_signal_engine, position_sizer, reddit_portfolio, reddit_backtester) + 수정 5개 |

---

## 1. 아키텍처 개요 (Option B — Clean Architecture)

### 1.1 파일 구조

```
[신규 5개]
reddit_collector.py      ← PRAW 3 subreddits + ticker extract + daily storage
wsb_signal_engine.py     ← full pipeline (consensus/30MA/ranking) + exit logic
position_sizer.py        ← PositionSizer ABC + EqualSizer + SentimentSizer + VolatilitySizer
reddit_portfolio.py      ← RedditPortfolio (position tracking, highest_price, gap-down)
reddit_backtester.py     ← RedditReplayBacktester (reads data/reddit/YYYY-MM-DD/)

[수정 5개]
backtester.py            ← BaseBacktestEngine 추출 + gpt4 모델 + commission
sentiment_provider.py    ← + GPTProvider (batch 10, gpt_cache.json)
indicators.py            ← + get_ma(period), get_atr(period=14)
config.py                ← all new constants
main.py                  ← new CLI flags
```

### 1.2 전체 데이터 흐름

```
[뉴스 파이프라인 — 기존 + gpt4]
Finnhub API
  └─→ collector.get_news()
       └─→ SentimentProvider (TextBlob | FinBERT | GPTProvider)
            └─→ score(articles) → float
                 └─→ BacktestEngine.run() [BaseBacktestEngine 상속]
                      └─→ commission 공제 → BacktestResult

[Reddit 파이프라인 — 신규]
PRAW (wsb + investing + stocks)
  └─→ RedditCollector.collect()
       └─→ _extract_tickers() → ticker list
           └─→ _validate_polygon() → valid tickers
               └─→ save data/reddit/YYYY-MM-DD/wsb_posts.json
                    └─→ WSBSignalEngine.run_pipeline()
                         └─→ consensus filter (×1.5)
                             └─→ 30MA filter (prev_close < MA30)
                                 └─→ ranking (mentions | ratio)
                                     └─→ top_n symbols
                                          └─→ RedditPortfolio.process_day()
                                               └─→ exit checks (gap-down / stop-loss / trailing)
                                                   └─→ new buys with PositionSizer
                                                       └─→ save portfolio_state.json
```

---

## 2. 클래스 인터페이스

### 2.1 GPTProvider (sentiment_provider.py 추가)

```python
class GPTProvider(SentimentProvider):
    """
    OpenAI GPT-4o 기반 감성 분석.
    - 배치 처리: 10건/API 호출
    - 캐시: data/gpt_cache.json (키: sha256(text)[:16])
    - 입력: title + body_excerpt[:300] + top_comments (Reddit) 또는 title + description (뉴스)
    - 출력: bullish/bearish/neutral 분류 후 pos/(pos+neg)*100
    """

    def score(self, articles: list[dict]) -> tuple[float, list[dict]]:
        """
        articles[i] 형태:
          뉴스: {"title": str, "description": str}
          Reddit: {"title": str, "body_excerpt": str, "top_comments": list[str]}

        Returns: (score [0-100], article_details)
        article_details[i]: {"title": str, "label": "bullish"|"bearish"|"neutral",
                              "included": bool, "cached": bool}
        """

    def _batch_call(self, texts: list[str]) -> list[str]:
        """10건씩 GPT-4o 호출. 응답: ["bullish", "neutral", ...] (같은 길이)"""

    def _load_cache(self) -> dict:
        """data/gpt_cache.json 로드. 없으면 {} 반환"""

    def _save_cache(self, cache: dict) -> None:
        """캐시 저장"""

    def _text_key(self, text: str) -> str:
        """sha256(text)[:16] → 캐시 키"""
```

**GPT-4o 프롬프트 설계:**
```
System: "You are a financial sentiment classifier. Classify each text as bullish, bearish, or neutral.
         Return a JSON array with one label per item."
User:   "[1] {text1}\n[2] {text2}\n..."
Response: ["bullish", "neutral", "bearish", ...]
```

**get_provider() 수정:**
```python
def get_provider(name: str) -> SentimentProvider:
    if name == "textblob": return TextBlobProvider()
    if name == "finbert":  return FinBERTProvider()
    if name == "gpt4":     return GPTProvider()
    raise ValueError(f"Unknown provider: '{name}'")
```

---

### 2.2 indicators.py 추가 함수

```python
def get_ma(ohlcv_df: pd.DataFrame, period: int) -> float | None:
    """
    단순이동평균 계산.
    Args:
        ohlcv_df: Polygon OHLCV DataFrame (columns: open, high, low, close, volume)
        period: MA 기간 (30 or 90)
    Returns:
        최신 MA 값. 데이터 부족(rows < period) 시 None.
    """

def get_atr(ohlcv_df: pd.DataFrame, period: int = 14) -> float | None:
    """
    Average True Range 계산 (Wilder's smoothing).
    True Range = max(H-L, |H-prevC|, |L-prevC|)
    Returns:
        ATR 값. 데이터 부족(rows < period+1) 시 None.
    """
```

---

### 2.3 PositionSizer ABC (position_sizer.py — 신규)

```python
from abc import ABC, abstractmethod
import math

class PositionSizer(ABC):
    @abstractmethod
    def calc_shares(self, total_cash: float, open_price: float, **kwargs) -> int:
        """
        매수할 주식 수 계산.
        Returns: 0 이상 정수. 현금 부족 시 0.
        """

class EqualSizer(PositionSizer):
    """
    total_cash / MAX_POSITIONS 균등 배분.
    kwargs: (없음)
    """
    def calc_shares(self, total_cash: float, open_price: float, **kwargs) -> int:
        slot = total_cash / config.MAX_POSITIONS
        return math.floor(slot / open_price)

class SentimentSizer(PositionSizer):
    """
    bullish_ratio에 따라 5% / 10% / 15%.
    kwargs: bullish_ratio (float, 0-1)
    """
    def calc_shares(self, total_cash: float, open_price: float, **kwargs) -> int:
        ratio = kwargs.get("bullish_ratio", 0.5)
        if ratio >= config.SENTIMENT_SIZE_HIGH_THRESHOLD:   # 0.80
            pct = config.SENTIMENT_SIZE_HIGH                 # 0.15
        elif ratio >= config.SENTIMENT_SIZE_MID_THRESHOLD:  # 0.65
            pct = config.SENTIMENT_SIZE_MID                  # 0.10
        else:
            pct = config.SENTIMENT_SIZE_LOW                  # 0.05
        return math.floor(total_cash * pct / open_price)

class VolatilitySizer(PositionSizer):
    """
    ATR 기반 volatility-weighted sizing.
    kwargs: atr (float), prev_close (float)
    """
    def calc_shares(self, total_cash: float, open_price: float, **kwargs) -> int:
        atr = kwargs.get("atr")
        prev_close = kwargs.get("prev_close")
        if not atr or not prev_close or prev_close == 0:
            # ATR 없으면 Equal 폴백
            return math.floor(total_cash / config.MAX_POSITIONS / open_price)
        atr_pct = atr / prev_close
        raw_size = config.VOLATILITY_TARGET_RISK / atr_pct
        size_pct = max(config.VOLATILITY_MIN_PCT,
                       min(config.VOLATILITY_MAX_PCT, raw_size))
        return math.floor(total_cash * size_pct / open_price)

def get_sizer(method: str) -> PositionSizer:
    """"equal" | "sentiment" | "volatility" → PositionSizer"""
    if method == "equal":      return EqualSizer()
    if method == "sentiment":  return SentimentSizer()
    if method == "volatility": return VolatilitySizer()
    raise ValueError(f"Unknown sizing method: '{method}'")
```

---

### 2.4 RedditCollector (reddit_collector.py — 신규)

```python
class RedditCollector:
    """
    PRAW 기반 3개 서브레딧 수집 + 티커 추출 + 날짜별 저장.
    """

    def __init__(self):
        """PRAW Reddit 인스턴스 초기화 (config 값 사용)"""

    def collect(self, date_str: str = None) -> dict[str, list[dict]]:
        """
        3개 서브레딧에서 게시글 수집 후 종목별로 분류.
        Args:
            date_str: "YYYY-MM-DD" (저장 파일명용). None이면 오늘.
        Returns:
            {"NVDA": [{"title": ..., "body_excerpt": ..., "top_comments": [...],
                       "subreddit": ..., "created_utc": ...}], ...}
        저장: data/reddit/{date_str}/wsb_posts.json
        """

    def _fetch_subreddit(self, name: str) -> list[dict]:
        """
        단일 서브레딧 수집.
        Flair 필터: REDDIT_ALLOWED_FLAIRS (DD, Discussion, Fundamentals, etc.)
        제외 Flair: Gain/Loss, Meme, YOLO
        최근 24시간 게시글만. rate limit 초과 시 빈 리스트 반환.
        """

    def _extract_tickers(self, posts: list[dict]) -> dict[str, list[dict]]:
        """
        게시글에서 $TICKER 패턴 + config.COMPANY_NAMES 매칭으로 티커 추출.
        Returns: {"NVDA": [post1, post2, ...], ...}
        """

    def _validate_polygon(self, symbols: list[str]) -> list[str]:
        """
        Polygon.io OHLCV 조회 성공 = 유효 종목.
        조회 실패(동전주/비상장) = 제외.
        Returns: 유효한 symbol 리스트
        """

    def _truncate_post(self, post: dict) -> dict:
        """
        GPT-4 텍스트 최적화:
        title[:GPT_POST_TITLE_MAX] + body[:GPT_POST_BODY_MAX] + top 3 comments[:GPT_COMMENT_MAX]
        """

    def _save_posts(self, date_str: str, posts_by_symbol: dict) -> None:
        """data/reddit/{date_str}/wsb_posts.json 저장"""
```

---

### 2.5 WSBSignalEngine (wsb_signal_engine.py — 신규)

```python
class WSBSignalEngine:
    """
    Reddit 게시글 → Top N 종목 선정 전체 파이프라인.
    Consensus filter → 30MA filter → Ranking → Top N
    """

    def __init__(self, provider: SentimentProvider, ranking: str = "mentions"):
        """
        Args:
            provider: FinBERTProvider | GPTProvider
            ranking: "mentions" | "ratio"
        """

    def run_pipeline(
        self,
        posts_by_symbol: dict[str, list[dict]],
        ohlcv_cache: dict[str, pd.DataFrame],
        date_str: str,
    ) -> tuple[list[str], list[dict]]:
        """
        완전한 파이프라인 실행.
        Returns:
            (top_n_symbols, signal_details)
            signal_details: 종목별 {symbol, bullish, bearish, ratio, mentions, ma30, passed_consensus, passed_ma, rank}
        """

    def _score_posts(
        self,
        posts_by_symbol: dict[str, list[dict]],
    ) -> dict[str, dict]:
        """
        종목별 감성 점수 + bullish/bearish count 계산.
        Returns: {"NVDA": {"bullish": 5, "bearish": 2, "neutral": 3, "ratio": 0.71, ...}}
        """

    def _filter_consensus(self, scored: dict[str, dict]) -> list[str]:
        """
        WSB_CONSENSUS_RATIO(1.5) 기준 필터.
        bearish=0이면 bullish >= 2 조건.
        """

    def _filter_ma30(
        self,
        symbols: list[str],
        ohlcv_cache: dict[str, pd.DataFrame],
    ) -> list[str]:
        """
        30MA 필터: prev_close < MA30 → 통과. MA30 계산 실패 시 통과(보수적).
        """

    def _rank(self, symbols: list[str], scored: dict) -> list[str]:
        """
        ranking="mentions": 총 게시글 수 내림차순
        ranking="ratio":    bullish/(bullish+bearish) 내림차순
        Returns: TOP_N개 종목 리스트
        """

    def check_exit(
        self,
        position: dict,
        today_ohlcv: dict,
        scored: dict[str, dict],
        ohlcv_cache: dict[str, pd.DataFrame],
    ) -> tuple[bool, str]:
        """
        보유 포지션 청산 조건 체크 (우선순위순).
        Args:
            position: {"symbol": ..., "entry_price": ..., "entry_date": ..., "highest_price": ..., "shares": ...}
            today_ohlcv: {"open": ..., "close": ..., "prev_close": ...}
            scored: 오늘 Reddit 감성 점수 (없으면 컨센서스 반전 체크 불가)
        Returns:
            (should_exit, reason)
            reason: "stop_loss" | "trailing_stop" | "gap_down" | "consensus_reversal" | "ma30_breakdown" | "profit_take"
        """
```

---

### 2.6 RedditPortfolio (reddit_portfolio.py — 신규)

```python
@dataclass
class Position:
    symbol: str
    entry_date: str
    entry_price: float
    shares: int
    highest_price: float   # 보유 이후 최고 종가 추적 (Trailing Stop용)

class RedditPortfolio:
    """
    Reddit Forward Testing 전용 포트폴리오.
    날짜별 상태를 data/reddit/YYYY-MM-DD/portfolio_state.json에 저장.
    """

    def __init__(self, strategy_key: str):
        """
        Args:
            strategy_key: "{model}_{ranking}_{sizing}" (예: "finbert_mentions_equal")
                          파일명: data/reddit_portfolio_{strategy_key}.json
        """

    def process_day(
        self,
        date_str: str,
        top_n: list[str],
        exit_signals: dict[str, str],  # symbol → reason
        ohlcv: dict[str, dict],        # symbol → {open, close, prev_close}
        sizer: PositionSizer,
        scored: dict[str, dict],       # 감성 점수 (SentimentSizer용)
        atr_cache: dict[str, float],   # ATR (VolatilitySizer용)
    ) -> dict:
        """
        하루 처리:
        1. Gap Down 체크 → 시가 청산
        2. 청산 신호 있는 포지션 종가 청산
        3. highest_price 업데이트
        4. 빈 슬롯에 top_n 신규 매수
        Returns: 당일 처리 요약 {"buys": [...], "sells": [...], "pnl": float}
        """

    def _calc_commission(self, trade_value: float) -> float:
        """max(trade_value × COMMISSION_RATE, COMMISSION_MIN_USD)"""

    def save_state(self, date_str: str) -> None:
        """data/reddit/{date_str}/portfolio_state.json 저장"""

    def load_state(self, date_str: str) -> bool:
        """지정 날짜 상태 로드. 없으면 False 반환"""

    def get_summary(self) -> dict:
        """총 수익률, 거래 수, 승률, MDD 계산"""
```

---

### 2.7 BaseBacktestEngine (backtester.py 리팩토링)

```python
class BaseBacktestEngine(ABC):
    """
    공통 거래 시뮬레이션 로직. 뉴스/Reddit 공통 사용.
    """

    def __init__(self):
        self.trades: list[TradeRecord] = []
        self.cash = config.INITIAL_CASH
        self.positions: dict = {}

    def _calc_commission(self, trade_value: float) -> float:
        """max(trade_value × 0.0025, $2.0)"""

    def _record_buy(self, symbol: str, date: str, price: float, shares: int) -> None:
        """포지션 기록 + 현금 차감 + 수수료 공제"""

    def _record_sell(self, symbol: str, date: str, price: float, reason: str) -> TradeRecord:
        """포지션 청산 + P&L 계산(수수료 포함) + 거래 기록"""

    def _calc_mdd(self) -> float:
        """최대 낙폭 계산"""

    @abstractmethod
    def run(self) -> "BacktestResult":
        """서브클래스가 구현"""

class BacktestEngine(BaseBacktestEngine):
    """
    기존 뉴스 기반 백테스터. BaseBacktestEngine 상속.
    --model gpt4 지원 추가.
    """
    def __init__(self, model: str = "finbert"):
        super().__init__()
        self.model = model  # "textblob" | "finbert" | "gpt4"

    def run(self) -> BacktestResult: ...
```

---

### 2.8 RedditReplayBacktester (reddit_backtester.py — 신규)

```python
class RedditReplayBacktester(BaseBacktestEngine):
    """
    data/reddit/YYYY-MM-DD/ 폴더 순서대로 읽어 백테스팅 replay.
    실시간 API 호출 없음 — 저장된 파일만 사용.
    """

    def __init__(
        self,
        model: str,           # "finbert" | "gpt4"
        ranking: str,         # "mentions" | "ratio"
        sizing: str,          # "equal" | "sentiment" | "volatility"
        from_date: str,       # "YYYY-MM-DD"
        to_date: str,         # "YYYY-MM-DD"
    ):
        super().__init__()
        self.model = model
        self.ranking = ranking
        self.sizing = sizing
        self.from_date = from_date
        self.to_date = to_date

    def run(self) -> BacktestResult:
        """
        1. from_date ~ to_date 범위 data/reddit/YYYY-MM-DD/ 폴더 목록 수집
        2. NFR-06: 유효 거래일 < REDDIT_BACKTEST_MIN_DAYS(14)이면 경고 출력
        3. 날짜별 wsb_posts.json + wsb_signals.json + OHLCV 로드
        4. BaseBacktestEngine._record_buy/_record_sell로 거래 시뮬레이션
        5. commission 공제된 BacktestResult 반환
        """

    def _load_day(self, date_str: str) -> dict | None:
        """wsb_posts.json + wsb_signals.json 로드. 없으면 None."""

    def _discover_dates(self) -> list[str]:
        """
        data/reddit/ 하위 YYYY-MM-DD 폴더 중
        from_date ≤ date ≤ to_date 범위 날짜 리스트 반환.
        """
```

---

## 3. 데이터 플로우 상세

### 3.1 Reddit Forward Testing — 일일 실행 흐름

```
[매일 16:30 ET — python main.py --reddit-run-now]

1. RedditCollector.collect(today)
   → wsb_posts.json 저장

2. collector.get_ohlcv() for top candidates
   → OHLCV DataFrame 캐시

3. WSBSignalEngine.run_pipeline(posts, ohlcv)
   → top_n, signal_details
   → wsb_signals.json 저장

4. [09:35 ET next day — python main.py --order-reddit]
   RedditPortfolio.process_day()
   → gap_down 체크 → exit → buy
   → portfolio_state.json 저장
```

### 3.2 Gap Down 처리 상세

```
09:35 ET 주문 처리 시:
  for symbol in current_positions:
    gap_down_pct = (today_open - prev_close) / prev_close * 100
    if gap_down_pct <= config.STOP_LOSS_PCT:   # -7.0
      exit at today_open
      reason = "gap_down"
      pnl = (today_open - entry_price) / entry_price * 100 - commission

16:30 ET 청산 체크 (우선순위순):
  1. stop_loss:          pnl_pct <= -7.0
  2. trailing_stop:      (close - highest_price) / highest_price <= -5.0 AND pnl > 0
  3. consensus_reversal: bearish > bullish × 1.5
  4. ma30_breakdown:     close < MA30 AND holding_days >= 5
  5. profit_take:        NEUTRAL sentiment AND net_pnl > 1.0%
```

### 3.3 Position Sizing — kwargs 매핑

| Sizer | 필요 kwargs | 출처 |
|-------|------------|------|
| EqualSizer | (없음) | - |
| SentimentSizer | `bullish_ratio` | WSBSignalEngine 출력 |
| VolatilitySizer | `atr`, `prev_close` | indicators.get_atr() + OHLCV |

---

## 4. API 계약

### 4.1 wsb_posts.json

```json
{
  "date": "YYYY-MM-DD",
  "NVDA": [
    {
      "title": "str (≤200자)",
      "body_excerpt": "str (≤300자)",
      "top_comments": ["str (≤100자)", "str", "str"],
      "subreddit": "wallstreetbets | investing | stocks",
      "created_utc": 1234567890,
      "bullish": true | false | null
    }
  ]
}
```

### 4.2 wsb_signals.json

```json
{
  "date": "YYYY-MM-DD",
  "model": "finbert | gpt4",
  "ranking": "mentions | ratio",
  "sizing": "equal | sentiment | volatility",
  "top_n": ["NVDA", "AMD", "PLTR"],
  "signal_details": [
    {
      "symbol": "NVDA",
      "bullish": 8, "bearish": 3, "ratio": 0.73,
      "mentions": 11,
      "ma30": 820.5, "prev_close": 815.0,
      "passed_consensus": true, "passed_ma": true,
      "rank": 1
    }
  ],
  "sell_signals": [
    {"symbol": "BBBY", "reason": "stop_loss", "pnl_pct": -7.2}
  ]
}
```

### 4.3 portfolio_state.json

```json
{
  "date": "YYYY-MM-DD",
  "cash": 85000.0,
  "positions": {
    "NVDA": {
      "entry_date": "YYYY-MM-DD",
      "entry_price": 820.0,
      "shares": 12,
      "highest_price": 870.0
    }
  },
  "total_value": 95400.0,
  "daily_trades": [
    {"type": "buy|sell", "symbol": "NVDA", "price": 820.0,
     "shares": 12, "reason": "new_buy|stop_loss|...", "commission": 2.46}
  ]
}
```

### 4.4 gpt_cache.json

```json
{
  "abc123def456": {
    "label": "bullish",
    "cached_at": "YYYY-MM-DD"
  }
}
```

### 4.5 CLI 계약 (main.py)

```
# 뉴스 백테스팅 (기존 + gpt4)
python main.py --backtest [--model textblob|finbert|gpt4] [--from DATE] [--to DATE]

# Reddit Forward Testing
python main.py --reddit-run-now [--model finbert|gpt4] [--ranking mentions|ratio] [--sizing equal|sentiment|volatility]

# Reddit Replay 백테스팅 (데이터 쌓인 후)
python main.py --backtest --source reddit --from YYYY-MM-DD --to YYYY-MM-DD

# Reddit 결과 리포트
python main.py --report-reddit
```

---

## 5. 의존성 관계

```
config.py
  ↑ (import)
  ├── reddit_collector.py
  │     ↑ praw, indicators (get_ma), collector (Polygon OHLCV)
  ├── wsb_signal_engine.py
  │     ↑ sentiment_provider, indicators (get_ma, get_atr), position_sizer
  ├── position_sizer.py
  │     ↑ indicators (get_atr)
  ├── reddit_portfolio.py
  │     ↑ position_sizer, indicators
  ├── reddit_backtester.py
  │     ↑ backtester (BaseBacktestEngine), position_sizer
  ├── sentiment_provider.py
  │     ↑ indicators (_get_finbert_pipeline), openai (GPTProvider)
  └── backtester.py (BaseBacktestEngine)
        ↑ sentiment_provider, collector

[변경 없음]
signals.py, trader.py, portfolio.py, scheduler.py, market_filter.py
```

---

## 6. config.py 신규 상수 목록

```python
# --- OpenAI ---
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
GPT_MODEL = "gpt-4o"
GPT_BATCH_SIZE = 10
GPT_CACHE_FILE = "data/gpt_cache.json"
GPT_POST_TITLE_MAX = 200
GPT_POST_BODY_MAX = 300
GPT_TOP_COMMENTS = 3
GPT_COMMENT_MAX = 100

# --- Reddit ---
REDDIT_CLIENT_ID: str = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET: str = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = "trading-bot/1.0"
REDDIT_SUBREDDITS = ["wallstreetbets", "investing", "stocks"]
REDDIT_ALLOWED_FLAIRS = ["DD", "Discussion", "Fundamentals", "Daily Discussion", "Earnings"]
REDDIT_LOOKBACK_HOURS = 24
REDDIT_DATA_DIR = "data/reddit"
REDDIT_MODE = False
REDDIT_BACKTEST_MIN_DAYS = 14

# --- Reddit 신호 파라미터 ---
WSB_CONSENSUS_RATIO = 1.5
WSB_SELL_RATIO = 1.5
TOP_N = 3
MAX_POSITIONS = 10
MA_ENTRY_PERIOD = 30
MA_BREAKDOWN_GRACE_DAYS = 5

# --- Position Sizing ---
POSITION_SIZING = "equal"
EQUAL_POSITION_PCT = 0.10
SENTIMENT_SIZE_HIGH_THRESHOLD = 0.80
SENTIMENT_SIZE_MID_THRESHOLD = 0.65
SENTIMENT_SIZE_HIGH = 0.15
SENTIMENT_SIZE_MID = 0.10
SENTIMENT_SIZE_LOW = 0.05
VOLATILITY_TARGET_RISK = 0.01
VOLATILITY_MIN_PCT = 0.05
VOLATILITY_MAX_PCT = 0.15
ATR_PERIOD = 14

# --- 손절매 / 익절 ---
STOP_LOSS_PCT = -7.0
TRAILING_STOP_PCT = -5.0

# --- 수수료 ---
COMMISSION_RATE = 0.0025
COMMISSION_MIN_USD = 2.0
```

---

## 7. 하위 호환성 보장

| 항목 | 보장 방식 |
|------|----------|
| `--backtest` (기존) | `--source` 기본값 = "news", 기존 동작 그대로 |
| `BacktestEngine` | `BaseBacktestEngine` 상속으로 기존 인터페이스 유지 |
| `signals.py` 등 5개 파일 | 변경 없음 |
| `get_provider("textblob"|"finbert")` | 기존 분기 유지, gpt4 분기만 추가 |
| `INITIAL_CASH`, `POSITION_SIZE_PCT` 등 기존 상수 | 변경 없음 |

---

## 8. 테스트 계획

| 테스트 | 방법 | 기준 |
|--------|------|------|
| GPTProvider 캐시 히트 | 동일 텍스트 2회 호출 → API 1회만 호출 | gpt_cache.json 생성 확인 |
| EqualSizer 계산 | total_cash=100000, max_positions=10, open=100 → 100주 | 정확한 정수 반환 |
| SentimentSizer 경계 | ratio=0.80 → 15%, ratio=0.79 → 10% | 경계값 정확성 |
| VolatilitySizer clamp | atr_pct=0.001 → 15% 상한 고정 | max(0.15) 적용 |
| Gap Down 청산 | prev_close=100, open=92 → gap=-8% → stop_loss 발동 | 시가 청산 기록 |
| Trailing Stop | highest=110, close=104 → drawdown=-5.45% AND pnl>0 → exit | reason="trailing_stop" |
| Commission 계산 | trade_value=500 → max(500×0.0025, 2) = $2.0 | 최소 $2 보장 |
| Replay Backtest | data/reddit/2026-04-17/ 있을 때 --source reddit 실행 | BacktestResult 반환 |
| NFR-06 경고 | 거래일 < 14일 → 경고 출력 | 경고 로그 확인 |

---

## 9. 구현 순서 (의존성 기준)

```
M1 config.py           → 신규 상수 추가 (다른 모듈 모두 의존)
M2 indicators.py       → get_ma(), get_atr() 추가
M3 sentiment_provider  → GPTProvider 추가 (gpt_cache.json)
M4 position_sizer.py   → 신규 파일 (PositionSizer ABC + 3 subclasses)
M5 reddit_collector.py → 신규 파일 (PRAW + storage)
M6 wsb_signal_engine   → 신규 파일 (M2, M3, M4 의존)
M7 reddit_portfolio.py → 신규 파일 (M4 의존)
M8 backtester.py       → BaseBacktestEngine 추출 + gpt4 + commission
   reddit_backtester.py → 신규 파일 (M8 BaseBacktestEngine 상속)
M9 main.py             → 신규 CLI 플래그 추가
```

---

## 10. 리스크 및 대응

| 리스크 | 대응 |
|--------|------|
| PRAW rate limit (600 req/10min) | 수집 간 1초 딜레이, 실패 시 빈 리스트 반환 + 경고 로그 |
| GPT-4o 비용 | 텍스트 truncation (FR-20) + gpt_cache.json 캐시 |
| FinBERT 100건 × 날짜 수 처리 시간 | 기존 백테스팅 캐시(backtest_cache.json) 재사용 |
| ATR 계산용 OHLCV 부족 | get_atr() → None 시 EqualSizer 폴백 |
| Reddit 게시글 0건 | collect() → {} 반환, 신규 매수 없음, 기존 포지션 유지 |
| BaseBacktester 리팩토링 중 기존 뉴스 로직 오염 | BacktestEngine → BaseBacktestEngine 상속, run() 오버라이드 구조 유지 |

---

## 11. 구현 가이드

### 11.1 핵심 설계 결정

| 결정 | 이유 |
|------|------|
| PositionSizer ABC | 3가지 sizing을 백테스팅에서 동일 인터페이스로 교체 가능 |
| RedditPortfolio 분리 | 기존 Portfolio(뉴스용)와 상태 충돌 방지. strategy_key로 12개 전략 별도 파일 |
| BaseBacktestEngine | commission 계산 + P&L 로직 중복 제거. 뉴스/Reddit replay 공통 사용 |
| WSBSignalEngine 분리 | wsb_signal_engine이 signals.py를 건드리지 않음. 뉴스 로직 오염 방지 |
| Reddit Forward Testing | PRAW 과거 데이터 한계 극복. 매일 데이터 수집 → 2-4주 후 replay 백테스팅 가능 |

### 11.2 주의사항

| 항목 | 내용 |
|------|------|
| `signals.py`, `trader.py` 등 5개 파일 | **절대 수정 금지** — 기존 뉴스 로직 동작 유지 |
| GPTProvider 프롬프트 | JSON 배열 응답 강제. 길이 불일치 시 "neutral" 폴백 처리 필수 |
| Gap Down 처리 시점 | 09:35 ET 주문 처리 시에만 적용. 16:30 ET 청산 체크와 구분 |
| 수수료 | 매수/매도 각각 독립 계산. `max(value × 0.0025, 2.0)` |
| `highest_price` 업데이트 | 매도 전, 당일 종가 확인 후 업데이트 (순서 중요) |

### 11.3 Session Guide

| 세션 | --scope | 모듈 | 설명 |
|------|---------|------|------|
| Session 1 | `module-1,module-2,module-3` | M1+M2+M3 | config 상수 + indicators 함수 + GPTProvider |
| Session 2 | `module-4,module-5` | M4+M5 | PositionSizer ABC + RedditCollector |
| Session 3 | `module-6,module-7` | M6+M7 | WSBSignalEngine + RedditPortfolio |
| Session 4 | `module-8` | M8 | backtester BaseBacktestEngine + reddit_backtester |
| Session 5 | `module-9` | M9 | main.py CLI 플래그 + 통합 테스트 |

```bash
/pdca do reddit-gpt4-quant --scope module-1,module-2,module-3
/pdca do reddit-gpt4-quant --scope module-4,module-5
/pdca do reddit-gpt4-quant --scope module-6,module-7
/pdca do reddit-gpt4-quant --scope module-8
/pdca do reddit-gpt4-quant --scope module-9
```
