# Design: Signal V2 — 감성 고도화 + 백테스팅 + Volume Spike

**Feature**: signal-v2
**Date**: 2026-04-05
**Architecture**: Option C — Pragmatic Balance
**Status**: Design

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | "FinBERT가 TextBlob보다 실제로 나은가?"를 데이터로 증명. 중립 기사 노이즈 제거 + 거래량 결합으로 신호 품질 향상 |
| **WHO** | news-rsi-trading 시스템 운영자 |
| **RISK** | Finnhub 60 req/min 제한 (백테스팅 시 rate limit) / neutral 필터 과적용 시 유효 기사 부족 / FinBERT 100건 처리 시간 (~50초/종목) |
| **SUCCESS** | 모델별 백테스팅 결과 비교 출력, articles_detail.json 생성, Volume Spike 신호 동작 확인 |
| **SCOPE** | `sentiment_provider.py`(신규) + `backtester.py`(신규) + `indicators.py`, `signals.py`, `collector.py`, `config.py`, `main.py` 수정 |

---

## 1. 아키텍처 개요

### 1.1 선택 근거 (Option C)

SentimentProvider ABC로 WSB/GPT 확장 경로를 열되, `_get_finbert_pipeline()` 은 `indicators.py`에 유지해 기존 ONNX 로컬 캐시 패턴을 그대로 재사용한다. Backtester는 기존 `collector`, `indicators`, `sentiment_provider` 컴포넌트를 직접 임포트하므로 signal 로직을 중복 구현하지 않는다.

### 1.2 모듈 구조 (변경 후)

```
신호 생성 파이프라인:

collector.get_ohlcv(QQQ) → market_filter.get_market_rsi()  [1회, 전 종목 재사용]

per symbol:
  collector.get_ohlcv(symbol)
    → indicators.get_latest_rsi()          → rsi, rsi_ma
    → indicators.calculate_volume_ma20()   → volume_ma20
  collector.get_news(symbol)               ← Finnhub (100건)
    → FinBERTProvider.score()              → (finbert_score, article_details)
    → TextBlobProvider.score()             → (textblob_score, _)
    → sentiment = avg(scores)
  signals.determine_signal(rsi, sentiment) → signal
  signals._check_volume_spike(...)         → override to BUY if spike
  market_filter.apply_market_filter()      → final signal
  signals._save_articles_detail()          → data/articles_detail.json

백테스팅 파이프라인:
  BacktestEngine(model="finbert")
    → for date in BACKTEST_START~BACKTEST_END:
         collector.get_ohlcv(symbol, to_date=date)
         collector.get_news(symbol, from_date=date-7d, limit=100)
         provider.score(articles) → (score, _)  [cache: data/backtest_cache.json]
         determine_signal(rsi, score)
         simulate_trade(signal, ohlcv_df, date)
    → BacktestResult → print_comparison()
```

---

## 2. 신규 파일: `sentiment_provider.py`

### 2.1 책임
- `SentimentProvider` ABC 정의
- `TextBlobProvider`: 기존 indicators.calculate_sentiment_score() 로직 이전
- `FinBERTProvider`: neutral 필터 + 새 점수 공식
- Provider 팩토리 함수

### 2.2 인터페이스 설계

```python
# sentiment_provider.py
from abc import ABC, abstractmethod

class SentimentProvider(ABC):
    """
    감성 점수 계산 추상 베이스 클래스.
    score() 구현체는 signals.py와 backtester.py에서 동일하게 사용된다.
    """

    @abstractmethod
    def score(self, articles: list[dict]) -> tuple[float, list[dict]]:
        """
        기사 목록으로부터 감성 점수와 기사별 분석 결과를 반환한다.

        Args:
            articles: list of {title, description, publishedAt}

        Returns:
            (score [0-100], article_details)
            - TextBlobProvider: article_details = [{title, included: True}]
            - FinBERTProvider:  article_details = [{title, finbert_label,
                                                    scores: {positive, negative, neutral},
                                                    included: bool}]
        """


class TextBlobProvider(SentimentProvider):
    """
    TextBlob 기반 감성 분석.
    모든 기사 포함, 공식: (avg_polarity + 1) * 50 → [0, 100]
    """
    def score(self, articles: list[dict]) -> tuple[float, list[dict]]: ...


class FinBERTProvider(SentimentProvider):
    """
    FinBERT 기반 감성 분석.
    - neutral ≥ NEUTRAL_FILTER_THRESHOLD → 제외 (included=False)
    - 유효 기사 < NEUTRAL_FILTER_MIN_ARTICLES → 폴백 (avg_raw 방식 + 경고 로그)
    - 새 공식: pos / (pos + neg) * 100
    - indicators._get_finbert_pipeline() 재사용 (ONNX 캐시 유지)
    """
    def score(self, articles: list[dict]) -> tuple[float, list[dict]]: ...


def get_provider(name: str) -> SentimentProvider:
    """
    이름으로 Provider 인스턴스를 반환한다.
    Args:
        name: "textblob" | "finbert"
    Returns:
        SentimentProvider 인스턴스
    Raises:
        ValueError: 알 수 없는 provider 이름
    """
```

### 2.3 FinBERTProvider 로직 상세

```
score(articles):
  1. articles가 없으면 → (50.0, [])
  2. FinBERT 파이프라인 로드: indicators._get_finbert_pipeline()
     실패 시 → (50.0, []) + 경고 로그
  3. 각 기사에 대해:
     - text = title + " " + description, 512자 truncation
     - result = pipe(text) → {positive, negative, neutral} 확률
     - finbert_label = argmax(positive, negative, neutral)
     - included = (neutral < NEUTRAL_FILTER_THRESHOLD)
     - article_detail = {title, finbert_label, scores, included}
  4. valid_articles = [a for a in article_details if a.included]
  5. if len(valid_articles) < NEUTRAL_FILTER_MIN_ARTICLES:
       # 폴백: 전체 기사의 avg(positive - negative) 방식
       logger.warning("[FinBERT] 유효 기사 부족({n}건) — 폴백 방식 사용")
       avg_raw = mean(p - n for each article)
       score = (avg_raw + 1) * 50
     else:
       pos_count = count(finbert_label == "positive" in valid_articles)
       neg_count = count(finbert_label == "negative" in valid_articles)
       if pos_count + neg_count == 0: score = 50.0
       else: score = pos_count / (pos_count + neg_count) * 100
  6. return (round(score, 2), article_details)
```

---

## 3. 수정 파일: `indicators.py`

### 3.1 변경 사항

| 항목 | 변경 |
|------|------|
| `calculate_sentiment_score()` | **삭제** — TextBlobProvider로 이전 |
| `calculate_finbert_sentiment_score()` | **삭제** — FinBERTProvider로 이전 |
| `_get_finbert_pipeline()` | **유지** — FinBERTProvider가 `import indicators` 후 직접 호출 |
| `_finbert_pipeline`, `_finbert_initialized` | **유지** |
| `calculate_rsi()`, `calculate_rsi_ma()`, `get_latest_rsi()` | **유지** |
| `calculate_volume_ma20()` | **신규 추가** |

### 3.2 신규 함수: `calculate_volume_ma20()`

```python
def calculate_volume_ma20(ohlcv_df: pd.DataFrame) -> float | None:
    """
    OHLCV DataFrame에서 20일 평균 거래량(SMA)을 계산한다.

    Args:
        ohlcv_df: OHLCV DataFrame (volume 컬럼 필요)

    Returns:
        float: 최근 VOLUME_MA_PERIOD일 평균 거래량.
               데이터 부족 시 None.
    """
    if ohlcv_df.empty or len(ohlcv_df) < config.VOLUME_MA_PERIOD:
        return None
    return float(ohlcv_df["volume"].tail(config.VOLUME_MA_PERIOD).mean())
```

---

## 4. 수정 파일: `collector.py`

### 4.1 변경 사항

| 항목 | 변경 |
|------|------|
| `get_news()` | Finnhub 전환. `from_date`, `limit` 파라미터 추가. NewsAPI 로직 삭제 |
| `_newsapi_request()` | **삭제** |
| `_finnhub_request()` | **신규** — Finnhub HTTP GET 헬퍼 (지수 백오프) |

### 4.2 `get_news()` 새 시그니처

```python
def get_news(
    symbol: str,
    days: int = None,
    from_date: str = None,
    limit: int = None,
) -> list[dict]:
    """
    Finnhub company-news API로 종목 관련 뉴스를 수집한다.

    Args:
        symbol: 종목 티커 (예: "AAPL")
        days: 몇 일 전부터 (기본값: config.NEWS_LOOKBACK_DAYS)
              from_date가 있으면 무시됨
        from_date: 명시적 시작 날짜 "YYYY-MM-DD" (백테스팅용)
        limit: 최대 기사 수 (기본값: config.NEWS_MAX_ARTICLES = 100)

    Returns:
        list of {title, description, publishedAt}
        - Finnhub headline → title
        - Finnhub summary  → description
        - Finnhub datetime (unix) → publishedAt (ISO 8601)
    """
```

### 4.3 Finnhub API 호출

```
GET https://finnhub.io/api/v1/company-news
  ?symbol={symbol}
  &from={from_date}       # YYYY-MM-DD
  &to={to_date}           # YYYY-MM-DD (today 또는 백테스팅 기준일)
  &token={FINNHUB_API_KEY}

응답 필드 매핑:
  article["headline"] → title
  article["summary"]  → description (없으면 "" fallback)
  article["datetime"] → publishedAt (unix timestamp → ISO 8601)

limit 처리: 응답 리스트를 [:limit]으로 슬라이싱
```

### 4.4 `_finnhub_request()` 설계

```python
def _finnhub_request(url: str, params: dict) -> list | None:
    """
    Finnhub HTTP GET 요청 (지수 백오프 재시도).
    Returns:
        list: 응답 JSON 배열. 실패 시 None.
    """
```

---

## 5. 수정 파일: `signals.py`

### 5.1 변경 사항

| 항목 | 변경 |
|------|------|
| `import indicators` 직접 감성 호출 | Provider 방식으로 교체 |
| Volume Spike 로직 | **신규** — `determine_signal()` 호출 후 예외 체크 |
| `articles_detail.json` 저장 | **신규** — `_save_articles_detail()` |
| `_get_active_providers()` | **신규** — config.SENTIMENT_PROVIDERS 기반 Provider 로드 |

### 5.2 `generate_signals_for_all()` 변경 흐름

```python
def generate_signals_for_all(symbols: list[str]) -> dict[str, dict]:
    ...
    mkt_rsi = market_filter.get_market_rsi()
    providers = _get_active_providers()         # ← 신규

    for symbol in symbols:
        # 1. OHLCV + RSI (기존 유지)
        ohlcv_df = collector.get_ohlcv(symbol)
        rsi, rsi_ma = indicators.get_latest_rsi(symbol, ohlcv_df)

        # 2. Volume MA20 (신규)
        volume_ma20 = indicators.calculate_volume_ma20(ohlcv_df)
        current_volume = float(ohlcv_df.iloc[-1]["volume"]) if not ohlcv_df.empty else None

        # 3. 뉴스 수집 (Finnhub)
        articles = collector.get_news(symbol)

        # 4. Provider별 감성 점수 계산 (신규)
        scores = []
        all_article_details = []
        for provider in providers:
            s, details = provider.score(articles)
            scores.append(s)
            if isinstance(provider, FinBERTProvider):
                all_article_details = details   # FinBERT 상세 결과만 저장
        sentiment = round(sum(scores) / len(scores), 2) if scores else 50.0

        # 5. 신호 결정 (기존 유지)
        signal_original = determine_signal(rsi, sentiment)

        # 6. Volume Spike 예외 처리 (신규, Market Filter 전)
        volume_spike = _check_volume_spike(current_volume, volume_ma20, rsi, sentiment)
        if volume_spike:
            signal_original = "BUY"
            logger.info(
                f"[Volume Spike] {symbol}: BUY "
                f"(vol={current_volume:.0f}/ma20={volume_ma20:.0f}, "
                f"×{current_volume/volume_ma20:.1f})"
            )

        # 7. Market Filter (기존 유지)
        signal = market_filter.apply_market_filter(signal_original, mkt_rsi)

        # 8. articles_detail 저장 (신규)
        if all_article_details:
            _save_articles_detail(symbol, all_article_details)

        results[symbol] = {
            ...,
            "volume_ma20": round(volume_ma20, 0) if volume_ma20 else None,
            "volume_spike": volume_spike,               # ← 신규 필드
            ...
        }
```

### 5.3 신규 헬퍼 함수

```python
def _get_active_providers() -> list[SentimentProvider]:
    """config.SENTIMENT_PROVIDERS 에서 Provider 인스턴스 목록 반환"""

def _check_volume_spike(
    current_volume: float | None,
    volume_ma20: float | None,
    rsi: float,
    sentiment: float,
) -> bool:
    """
    Volume Spike 조건 검사.
    IF volume >= volume_ma20 × VOLUME_SPIKE_MULTIPLIER
       AND rsi < VOLUME_SPIKE_RSI_MAX (40)
       AND SENTIMENT_NEUTRAL_LOW <= sentiment <= SENTIMENT_NEUTRAL_HIGH
    """

def _save_articles_detail(symbol: str, article_details: list[dict]) -> None:
    """
    data/articles_detail.json에 당일 기사별 FinBERT 분석 결과를 저장한다.
    기존 파일을 덮어씀 (당일 데이터만 유지 — NFR-03).

    파일 스키마:
    {
      "date": "YYYY-MM-DD",
      symbol: [
        {title, finbert_label, scores: {positive, negative, neutral}, included}
      ]
    }
    """
```

### 5.4 `signals.json` 스키마 변경 (신규 필드)

```json
{
  "AAPL": {
    "rsi": 49.65,
    "rsi_ma": 42.1,
    "sentiment": 50.68,
    "sentiment_textblob": 54.42,
    "sentiment_finbert": 46.94,
    "volume_ma20": 58000000,
    "volume_spike": false,
    "market_rsi": 46.34,
    "market_filter_applied": false,
    "signal": "BUY",
    "signal_original": "BUY",
    "timestamp": "..."
  }
}
```

---

## 6. 신규 파일: `backtester.py`

### 6.1 책임
- 고정 기간(2026-02-01 ~ 2026-04-01) 백테스팅
- 모델별(`textblob`/`finbert`/`combined`) 신호 재계산
- 거래 시뮬레이션 및 성과 지표 계산
- `data/backtest_cache.json` 캐시로 재실행 최적화

### 6.2 클래스 / 함수 설계

```python
# backtester.py
import json
from dataclasses import dataclass, field

@dataclass
class TradeRecord:
    symbol: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    pnl_pct: float          # (exit - entry) / entry * 100
    is_win: bool


@dataclass
class BacktestResult:
    model: str
    total_return_pct: float
    trade_count: int
    win_rate_pct: float
    mdd_pct: float          # Max Drawdown (%)
    per_symbol: dict[str, dict]   # {symbol: {return_pct, trade_count}}
    trades: list[TradeRecord] = field(default_factory=list)


class BacktestEngine:
    def __init__(self, model: str):
        """
        Args:
            model: "textblob" | "finbert" | "combined"
        """
        self.model = model
        self._cache: dict = _load_backtest_cache()

    def run(self, symbols: list[str]) -> BacktestResult:
        """
        BACKTEST_START ~ BACKTEST_END 기간 백테스팅 실행.

        알고리즘:
        1. 거래일 목록 생성 (pandas_market_calendars, NYSE)
        2. per symbol, per date:
           a. OHLCV 수집 (Polygon, to_date 기준)
           b. RSI 계산
           c. 뉴스 수집 (Finnhub, from_date=date-7d, to_date=date)
           d. 감성 점수 계산 (캐시 히트 시 API/모델 재호출 없음)
           e. 신호 결정
        3. 거래 시뮬레이션:
           - BUY/STRONG_BUY → 다음 거래일 시가로 진입
           - Exit 조건: 진입가 대비 1% 이상 상승 OR 14 거래일 경과 → 종가로 청산
        4. 성과 계산: total_return, win_rate, MDD
        5. 캐시 저장
        """

    def _get_sentiment(
        self,
        symbol: str,
        date_str: str,
        articles: list[dict],
    ) -> float:
        """
        캐시 조회 → 미스 시 Provider 계산 후 캐시 저장.
        캐시 키: "{symbol}_{date_str}_{self.model}"
        """

    def _simulate_trades(
        self,
        symbol: str,
        signals_by_date: dict[str, str],    # {date: signal}
        ohlcv_by_date: dict[str, dict],     # {date: {open, close}}
    ) -> list[TradeRecord]:
        """거래 시뮬레이션. 동시 포지션 없음 (1종목 1포지션)."""

    def _calculate_mdd(self, trades: list[TradeRecord]) -> float:
        """거래 목록에서 최대 낙폭(MDD) 계산."""


def run_all_models(symbols: list[str]) -> dict[str, BacktestResult]:
    """
    textblob, finbert, combined 3개 모델 순차 실행.
    Returns: {"textblob": BacktestResult, "finbert": ..., "combined": ...}
    """


def print_comparison(results: dict[str, BacktestResult]) -> None:
    """
    모델별 결과 비교 출력 (Plan §4 출력 포맷).

    출력 예:
    === 백테스팅 결과 (2026-02-01 ~ 2026-04-01) ===
    모델: TextBlob
      총 수익률: +5.1% | 거래: 10회 | 승률: 60.0% | MDD: -2.8%
    ...
    종목별 (FinBERT 기준):
      AAPL  | +5.2% | 4거래
    """


def _load_backtest_cache() -> dict:
    """data/backtest_cache.json 로드. 없으면 {} 반환."""


def _save_backtest_cache(cache: dict) -> None:
    """data/backtest_cache.json 저장."""
```

### 6.3 rate limit 대응

```python
# Finnhub 60 req/min → 백테스팅 루프에서 호출 간 delay
import time
time.sleep(config.FINNHUB_REQUEST_DELAY)  # 1.0초

# FinBERT 100건 처리 최적화:
# 캐시 키 "{symbol}_{date}_{model}" 히트 시 모델 재호출 없음
```

---

## 7. 수정 파일: `config.py`

### 7.1 신규 상수

```python
# --- Finnhub ---
FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "")
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
FINNHUB_REQUEST_DELAY = 1.0          # 초 (60 req/min → 1초 간격)

# --- 뉴스 수집 ---
NEWS_PROVIDER = "finnhub"            # "newsapi" → "finnhub"
NEWS_MAX_ARTICLES = 100              # 50 → 100

# --- Signal V2: Neutral 필터 ---
NEUTRAL_FILTER_THRESHOLD = 0.80      # FinBERT neutral 이상이면 제외
NEUTRAL_FILTER_MIN_ARTICLES = 10     # 필터 후 최소 유효 기사 수

# --- Signal V2: Volume Spike ---
VOLUME_SPIKE_MULTIPLIER = 2.0        # 20일 평균 대비 급증 배수
VOLUME_SPIKE_RSI_MAX = 40.0          # Volume Spike 발동 시 RSI 상한
VOLUME_MA_PERIOD = 20

# --- 백테스팅 ---
BACKTEST_START = "2026-02-01"
BACKTEST_END   = "2026-04-01"
BACKTEST_CACHE_FILE = "data/backtest_cache.json"
ARTICLES_DETAIL_FILE = "data/articles_detail.json"

# --- 활성 Provider 목록 ---
SENTIMENT_PROVIDERS: list[str] = ["finbert", "textblob"]
```

### 7.2 수정 상수

```python
# 기존 유지 (변경 없음 — NFR-05)
NEWS_API_KEY: str = os.getenv("NEWS_API_KEY", "")   # 삭제 대상이지만 .env 호환성 유지
NEWSAPI_BASE_URL = ...                               # 동일하게 유지 (미사용)
```

---

## 8. 수정 파일: `main.py`

### 8.1 신규 플래그

```python
parser.add_argument(
    "--backtest",
    action="store_true",
    help="백테스팅 실행 (2026-02-01 ~ 2026-04-01)",
)
parser.add_argument(
    "--model",
    choices=["textblob", "finbert", "combined"],
    default="combined",
    help="백테스팅 감성 모델 (기본값: combined)",
)
```

### 8.2 `--backtest` 처리 흐름

```python
if args.backtest:
    import config
    import backtester

    if not config.FINNHUB_API_KEY:
        logger.error(".env에 FINNHUB_API_KEY가 없습니다.")
        sys.exit(1)

    if args.model == "combined":
        # 3개 모델 모두 실행 → 비교 출력
        results = backtester.run_all_models(config.SYMBOLS)
        backtester.print_comparison(results)
    else:
        # 단일 모델 실행
        engine = backtester.BacktestEngine(args.model)
        result = engine.run(config.SYMBOLS)
        backtester.print_comparison({args.model: result})
```

### 8.3 `_check_env()` 수정

```python
def _check_env() -> None:
    missing = []
    if not config.POLYGON_API_KEY:
        missing.append("POLYGON_API_KEY")
    if not config.FINNHUB_API_KEY:
        missing.append("FINNHUB_API_KEY")
    # NEWS_API_KEY 체크 제거 (Finnhub 전환)
```

---

## 9. `data/articles_detail.json` 스키마

```json
{
  "date": "2026-04-05",
  "AAPL": [
    {
      "title": "Apple reports record revenue...",
      "finbert_label": "positive",
      "scores": {"positive": 0.87, "negative": 0.05, "neutral": 0.08},
      "included": true
    },
    {
      "title": "Apple holds annual developer conference",
      "finbert_label": "neutral",
      "scores": {"positive": 0.03, "negative": 0.04, "neutral": 0.93},
      "included": false
    }
  ]
}
```

- 당일 데이터만 저장 (덮어씌움) — NFR-03
- FinBERT Provider가 있을 때만 생성 (TextBlob-only 모드에서는 생략)

---

## 10. 오류 처리 설계

| 상황 | 처리 |
|------|------|
| Finnhub API 실패 | 기존 지수 백오프 재시도 (REQUEST_MAX_RETRIES), 빈 리스트 반환 |
| FinBERT 초기화 실패 | 50.0 반환 + 에러 로그 (기존 동작 유지) |
| 유효 기사 < 10건 | 폴백 방식 (avg_raw) + 경고 로그 |
| Volume Spike 데이터 없음 | volume_ma20 = None → spike 체크 건너뜀 |
| 백테스팅 캐시 손상 | try/except → 빈 캐시로 재시작, 경고 로그 |

---

## 11. Implementation Guide

### 11.1 구현 순서

| # | 모듈 | 파일 | 작업 |
|---|------|------|------|
| M1 | Config | `config.py` | 신규 상수 추가 (Finnhub, backtest, volume spike 등) |
| M2 | Collector | `collector.py` | Finnhub `get_news()` 교체, `_finnhub_request()` 추가 |
| M3 | Provider | `sentiment_provider.py` | SentimentProvider ABC + TextBlobProvider + FinBERTProvider 신규 생성 |
| M4 | Indicators | `indicators.py` | NLP 함수 삭제, `calculate_volume_ma20()` 추가 |
| M5 | Signals | `signals.py` | Provider 호출, Volume Spike, articles_detail 저장 |
| M6 | Backtester | `backtester.py` | BacktestEngine + 캐시 + 출력 신규 생성 |
| M7 | Main | `main.py` | --backtest --model 플래그, _check_env 수정 |

### 11.2 의존 관계

```
M1 (config) → M2 (collector) → M3 (provider) → M4 (indicators)
                                                  ↓
                                             M5 (signals)
                                                  ↓
M1 (config) → M2 + M3 + M4 → M6 (backtester) → M7 (main)
```

### 11.3 Session Guide

| 세션 | 모듈 | 예상 LOC | 목표 |
|------|------|---------|------|
| Session 1 | M1 + M2 + M3 | ~180줄 | config 상수 + Finnhub 수집 + Provider 추상화 |
| Session 2 | M4 + M5 | ~80줄 | volume_ma20 + signals Provider 통합 + articles_detail |
| Session 3 | M6 + M7 | ~200줄 | 백테스팅 엔진 + main 플래그 |

**Session 1 시작**: `/pdca do signal-v2 --scope M1,M2,M3`
**Session 2 시작**: `/pdca do signal-v2 --scope M4,M5`
**Session 3 시작**: `/pdca do signal-v2 --scope M6,M7`

---

## 12. 성공 기준 매핑

| SC | 기준 | 관련 모듈 |
|----|------|-----------|
| SC-01 | `--backtest --model textblob/finbert/combined` 수익률/승률/MDD 출력 | M6, M7 |
| SC-02 | 모델별 결과 비교 요약 출력 | M6 |
| SC-03 | `data/articles_detail.json` 생성, `included` 필드 존재 | M3, M5 |
| SC-04 | Volume Spike 조건 충족 시 `[Volume Spike]` 로그 출력 | M5 |
| SC-05 | neutral 필터 후 유효 기사 < 10건 시 폴백 로그 출력 | M3 |
| SC-06 | Finnhub 전환 후 기사 100건 수집 확인 | M2 |
