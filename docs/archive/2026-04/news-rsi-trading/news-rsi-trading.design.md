# Design: News-RSI Stock Trading System

**Feature**: news-rsi-trading
**Date**: 2026-04-01
**Architecture**: Option C — 실용적 모듈 분리
**Status**: Design

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 뉴스 감성 + RSI 결합 전략의 실효성을 실제 시장 조건에서 검증 (페이퍼 트레이딩) |
| **WHO** | 알고리즘 트레이딩 연구자/개발자 (개인 사용) |
| **RISK** | NewsAPI 무료 티어 한도, Polygon.io 속도 제한, 스케줄러 프로세스 중단 |
| **SUCCESS** | 정규장 타이밍에 맞춰 신호 자동 생성 + 가상 포트폴리오 실시간 추적 |
| **SCOPE** | 미국 주식(US), Python, 페이퍼 트레이딩 (실제 주문 없음) |

---

## 1. 아키텍처 개요

### 1.1 선택된 아키텍처: Option C (실용적 모듈 분리)

관심사별로 파일을 분리하되, 추상화 레이어 없이 직접 함수 호출 방식을 사용한다.
모듈 간 의존성은 단방향으로 유지한다.

```
의존성 흐름 (단방향):
scheduler → collector → indicators → signals → trader → portfolio
                                                         ↑
                                              config (공통 설정)
```

### 1.2 디렉토리 구조

```
C:\Users\SentiQuant\
├── main.py              # 진입점 (스케줄러 시작)
├── scheduler.py         # APScheduler 잡 정의 및 실행 루프
├── collector.py         # 외부 API 데이터 수집 (Polygon.io, NewsAPI)
├── indicators.py        # RSI, RSI MA, Sentiment Score 계산
├── signals.py           # 5단계 매매 신호 결정 로직
├── trader.py            # 페이퍼 트레이딩 엔진 (매수/매도 판단 및 실행)
├── portfolio.py         # 포지션 관리, 거래 이력, 리포트 생성
├── config.py            # 종목 목록, API 엔드포인트, 임계값 상수
├── .env                 # API 키 (Polygon.io, NewsAPI) — git 제외
├── requirements.txt     # 의존성 목록
└── data/
    ├── portfolio.json   # 현재 포지션 상태 (영속화)
    ├── signals.json     # 최근 신호 기록
    └── trades.csv       # 전체 거래 이력
```

---

## 2. 모듈별 상세 설계

### 2.1 `config.py` — 설정 관리

```python
# 종목 목록 (변경 가능)
SYMBOLS = ["AAPL", "MSFT", "NVDA"]

# API 설정
POLYGON_BASE_URL = "https://api.polygon.io"
NEWSAPI_BASE_URL = "https://newsapi.org/v2"

# 지표 파라미터
RSI_PERIOD = 14
RSI_MA_PERIOD = 7
OHLCV_LOOKBACK_DAYS = 70       # RSI 계산용 (60거래일 + 버퍼)
NEWS_LOOKBACK_DAYS = 7

# 신호 임계값
SENTIMENT_STRONG_BUY = 70
SENTIMENT_BUY = 50
SENTIMENT_NEUTRAL_LOW = 40
SENTIMENT_NEUTRAL_HIGH = 60
SENTIMENT_STRONG_SELL = 30

RSI_OVERSOLD = 30
RSI_NEUTRAL_LOW = 40
RSI_NEUTRAL_HIGH = 60
RSI_OVERBOUGHT = 70

# 페이퍼 트레이딩 설정
INITIAL_CASH = 100_000.0       # 초기 가상 자금 (USD)
POSITION_SIZE_PCT = 0.2        # 종목당 자금 비율 (20%)
PROFIT_TARGET_PCT = 1.0        # 기본 매도 목표 수익률
PROFIT_TARGET_ADJUSTED_PCT = 0.25  # 14일 경과 후 조정 목표
HOLDING_PERIOD_DAYS = 14       # 보유 기간 조정 기준일

# 스케줄러 (ET 기준)
SIGNAL_JOB_HOUR = 16           # 신호 계산: 16:30 ET
SIGNAL_JOB_MINUTE = 30
ORDER_JOB_HOUR = 9             # 가상 주문: 09:35 ET
ORDER_JOB_MINUTE = 35
TIMEZONE = "America/New_York"

# 데이터 저장 경로
DATA_DIR = "data"
PORTFOLIO_FILE = "data/portfolio.json"
TRADES_FILE = "data/trades.csv"
SIGNALS_FILE = "data/signals.json"
LOG_FILE = "data/trading.log"
```

---

### 2.2 `collector.py` — 데이터 수집

#### 책임
- Polygon.io REST API로 OHLCV 수집
- NewsAPI로 뉴스 기사 수집
- API 실패 시 최대 3회 재시도 (지수 백오프)

#### 주요 함수

```python
def get_ohlcv(symbol: str, days: int = 70) -> pd.DataFrame:
    """
    Polygon.io /v2/aggs/ticker/{symbol}/range/1/day/{from}/{to} 호출
    Returns: DataFrame[date, open, high, low, close, volume]
    date는 ET 기준 거래일 날짜
    """

def get_news(symbol: str, company_name: str, days: int = 7) -> list[dict]:
    """
    NewsAPI /v2/everything 호출
    query: f"{symbol} OR {company_name}"
    language: "en"
    sources 필터: 정식 언론사만 (domains 파라미터)
    Returns: list of {title, description, publishedAt}
    """

def _request_with_retry(url: str, params: dict, max_retries: int = 3) -> dict:
    """
    requests.get with exponential backoff (1s, 2s, 4s)
    실패 시 logging.error 후 빈 결과 반환
    """
```

#### API 호출 예시

```
# Polygon.io OHLCV
GET https://api.polygon.io/v2/aggs/ticker/AAPL/range/1/day/2026-01-01/2026-04-01
    ?adjusted=true&sort=asc&limit=120&apiKey={POLYGON_API_KEY}

# NewsAPI
GET https://newsapi.org/v2/everything
    ?q=AAPL+OR+Apple+Inc&language=en&from=2026-03-25&sortBy=publishedAt
    &apiKey={NEWS_API_KEY}
```

---

### 2.3 `indicators.py` — 기술 지표 계산

#### 책임
- RSI(14) 계산
- RSI MA(7) 계산
- TextBlob Sentiment Score 계산

#### 주요 함수

```python
def calculate_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """
    Wilder's smoothing method (EWM 방식)
    delta = closes.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/period, adjust=False).mean()
    RS = gain / loss
    RSI = 100 - (100 / (1 + RS))
    Returns: pd.Series (index=date)
    """

def calculate_rsi_ma(rsi: pd.Series, period: int = 7) -> pd.Series:
    """
    RSI의 단순이동평균 (SMA)
    Returns: pd.Series (index=date)
    """

def calculate_sentiment_score(articles: list[dict]) -> float:
    """
    1. 각 기사의 title + " " + description 결합
    2. TextBlob(text).sentiment.polarity 계산 (-1 ~ 1)
    3. avg_polarity = mean(polarity values)
    4. scaled = (avg_polarity + 1) * 50  → [0, 100]
    5. 기사 없을 경우 기본값 50.0 반환 (중립)
    Returns: float [0, 100]
    """
```

---

### 2.4 `signals.py` — 신호 생성

#### 책임
- RSI + 감성 점수를 입력받아 5단계 신호 반환
- 엣지 케이스 처리 (조건 미해당 시 Neutral)

#### 신호 결정 로직

```python
SignalType = Literal["STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL"]

def determine_signal(rsi: float, sentiment: float) -> SignalType:
    """
    우선순위 순서로 조건 검사:
    1. STRONG_BUY:  sentiment > 70 AND rsi < 30
    2. STRONG_SELL: sentiment < 30 AND rsi > 70
    3. BUY:         sentiment > 50 AND 30 <= rsi < 50
    4. SELL:        sentiment < 50 AND rsi > 70
    5. NEUTRAL:     40 <= sentiment <= 60 AND 40 <= rsi <= 60
    6. 기본값:      NEUTRAL (조건 미해당)
    Returns: SignalType
    """

def generate_signals_for_all(symbols: list[str]) -> dict[str, dict]:
    """
    각 종목에 대해:
    1. get_ohlcv() → calculate_rsi() → latest RSI
    2. get_news() → calculate_sentiment_score()
    3. determine_signal()
    Returns: {symbol: {rsi, rsi_ma, sentiment, signal, timestamp}}
    """
```

---

### 2.5 `trader.py` — 페이퍼 트레이딩 엔진

#### 책임
- 신호 기반 가상 매수/매도 판단
- 보유 기간 조정 로직 적용
- portfolio.py에 체결 결과 전달

#### 주요 함수

```python
def process_orders(signals: dict[str, dict], portfolio: Portfolio) -> list[Trade]:
    """
    다음 날 시가(Open Price) 기반 처리:
    각 종목에 대해:
      - open_price = get_ohlcv(symbol, days=2).iloc[-1]['open']
      - _process_buy_signal() 또는 _process_sell_signal() 호출
    Returns: 체결된 Trade 목록
    """

def _process_buy_signal(
    symbol: str, signal: str, open_price: float, portfolio: Portfolio
) -> Trade | None:
    """
    조건: signal in (BUY, STRONG_BUY)
    - 포지션 없음: 신규 매수 (POSITION_SIZE_PCT × 가용 현금 / open_price 수량)
    - 포지션 있음: open_price < position.avg_price → 추가 매수
    - 포지션 있음: open_price >= position.avg_price → 스킵
    """

def _process_sell_signal(
    symbol: str, signal: str, open_price: float, portfolio: Portfolio
) -> Trade | None:
    """
    조건: signal in (NEUTRAL, SELL, STRONG_SELL)
    - 포지션 없음: 스킵
    - NEUTRAL: net_profit > PROFIT_TARGET_PCT (1%) → 매도
    - NEUTRAL + 보유 14일 초과: net_profit > PROFIT_TARGET_ADJUSTED_PCT (0.25%) → 매도
    - SELL / STRONG_SELL: 무조건 매도
    순수익률 = (open_price - avg_price) / avg_price * 100
    """

def _calculate_shares_to_buy(
    available_cash: float, open_price: float
) -> int:
    """
    shares = floor(available_cash * POSITION_SIZE_PCT / open_price)
    최소 1주 보장
    """
```

---

### 2.6 `portfolio.py` — 포트폴리오 관리

#### 책임
- 포지션(보유 종목) 상태 영속화 (portfolio.json)
- 거래 이력 기록 (trades.csv)
- 신호 이력 기록 (signals.json)
- 포트폴리오 리포트 출력

#### 데이터 모델

```python
@dataclass
class Position:
    symbol: str
    shares: int
    avg_price: float        # 평균 매수가
    buy_date: str           # 최초 매수일 (ISO 8601)
    total_cost: float       # 총 투자 비용

@dataclass
class Trade:
    symbol: str
    date: str               # 체결일 (ISO 8601)
    action: str             # "BUY" | "SELL"
    signal: str             # 신호명
    price: float            # 체결가 (시가)
    shares: int
    amount: float           # price * shares
    net_profit_pct: float   # 매도 시 순수익률 (매수 시 0.0)
    net_profit_usd: float   # 매도 시 순수익 USD

@dataclass
class Portfolio:
    cash: float                          # 가용 현금
    positions: dict[str, Position]       # symbol → Position
```

#### 주요 함수

```python
def load_portfolio() -> Portfolio:
    """portfolio.json에서 로드, 없으면 초기 상태(INITIAL_CASH) 반환"""

def save_portfolio(portfolio: Portfolio) -> None:
    """portfolio.json에 저장 (atomic write)"""

def record_trade(trade: Trade) -> None:
    """trades.csv에 한 행 추가 (append 모드)"""

def save_signals(signals: dict) -> None:
    """signals.json 갱신"""

def print_portfolio_report(portfolio: Portfolio) -> None:
    """
    콘솔 출력:
    - 가용 현금
    - 보유 종목별: 수량, 평균매수가, 현재가, 평가손익
    - 총 포트폴리오 가치 및 수익률
    """
```

---

### 2.7 `scheduler.py` — 스케줄러

#### 책임
- APScheduler를 이용한 ET 기준 2개 잡 등록
- NYSE 휴장일 체크 후 잡 스킵
- 프로세스 무한 루프 유지

#### 잡 정의

```python
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import pandas_market_calendars as mcal

def is_trading_day(dt: datetime) -> bool:
    """NYSE 캘린더로 해당 날짜가 거래일인지 확인"""
    nyse = mcal.get_calendar("NYSE")
    schedule = nyse.schedule(start_date=dt.date(), end_date=dt.date())
    return not schedule.empty

def signal_calculation_job():
    """
    16:30 ET 실행
    1. is_trading_day() 체크 → False면 로그 후 리턴
    2. generate_signals_for_all(SYMBOLS) 호출
    3. save_signals() 저장
    4. 콘솔 출력
    """

def order_processing_job():
    """
    09:35 ET 실행
    1. is_trading_day() 체크 → False면 리턴
    2. load_portfolio() 로드
    3. 전날 signals.json 로드
    4. process_orders() 호출
    5. save_portfolio() + record_trade() 저장
    6. print_portfolio_report() 출력
    """

def start_scheduler():
    scheduler = BlockingScheduler(timezone="America/New_York")
    scheduler.add_job(
        signal_calculation_job,
        CronTrigger(hour=16, minute=30, timezone="America/New_York")
    )
    scheduler.add_job(
        order_processing_job,
        CronTrigger(hour=9, minute=35, timezone="America/New_York")
    )
    scheduler.start()  # 블로킹 루프
```

---

### 2.8 `main.py` — 진입점

```python
"""
사용법:
    python main.py              # 스케줄러 시작 (실시간 운영)
    python main.py --run-now    # 즉시 신호 계산 후 종료 (테스트용)
    python main.py --report     # 포트폴리오 현황 출력 후 종료
"""
import argparse
from scheduler import start_scheduler
from signals import generate_signals_for_all
from portfolio import load_portfolio, print_portfolio_report
from config import SYMBOLS

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()

    if args.run_now:
        signals = generate_signals_for_all(SYMBOLS)
        for symbol, data in signals.items():
            print(f"{symbol}: {data['signal']} (RSI={data['rsi']:.1f}, Sentiment={data['sentiment']:.1f})")
    elif args.report:
        portfolio = load_portfolio()
        print_portfolio_report(portfolio)
    else:
        start_scheduler()

if __name__ == "__main__":
    main()
```

---

## 3. 데이터 플로우

```
[16:30 ET - 신호 계산]
collector.get_ohlcv(symbol) ──→ indicators.calculate_rsi()
                                indicators.calculate_rsi_ma()
collector.get_news(symbol)  ──→ indicators.calculate_sentiment_score()
                                         ↓
                               signals.determine_signal(rsi, sentiment)
                                         ↓
                               portfolio.save_signals(signals)

[09:35 ET - 가상 주문]
portfolio.load_portfolio() ──→ trader.process_orders(signals, portfolio)
collector.get_ohlcv(open_price)──┘
                                         ↓
                               portfolio.save_portfolio()
                               portfolio.record_trade()
                               portfolio.print_portfolio_report()
```

---

## 4. 데이터 스키마

### 4.1 `data/portfolio.json`

```json
{
  "cash": 95000.00,
  "positions": {
    "AAPL": {
      "symbol": "AAPL",
      "shares": 25,
      "avg_price": 196.50,
      "buy_date": "2026-03-15T09:35:00-04:00",
      "total_cost": 4912.50
    }
  },
  "updated_at": "2026-04-01T09:35:00-04:00"
}
```

### 4.2 `data/trades.csv`

```csv
date,symbol,action,signal,price,shares,amount,net_profit_pct,net_profit_usd
2026-03-15T09:35:00,AAPL,BUY,BUY,196.50,25,4912.50,0.0,0.0
2026-03-28T09:35:00,AAPL,SELL,SELL,205.20,25,5130.00,4.42,217.50
```

### 4.3 `data/signals.json`

```json
{
  "date": "2026-04-01",
  "signals": {
    "AAPL": {
      "rsi": 28.5,
      "rsi_ma": 32.1,
      "sentiment": 72.3,
      "signal": "STRONG_BUY",
      "timestamp": "2026-04-01T16:30:00-04:00"
    }
  }
}
```

---

## 5. 엣지 케이스 처리

| 상황 | 처리 방식 |
|------|-----------|
| 뉴스 기사 0건 | sentiment = 50.0 (중립) |
| RSI 계산 데이터 부족 (<14일) | 해당 종목 스킵 + 경고 로그 |
| 신호 조건 미해당 | NEUTRAL 반환 |
| 매수 가능 현금 부족 | 해당 종목 스킵 |
| Open Price API 실패 | 재시도 3회 후 주문 스킵 + 에러 로그 |
| 포지션 없는데 매도 신호 | 스킵 |
| NYSE 휴장일 | 양쪽 잡 모두 실행 안 함 |
| portfolio.json 없음 | 초기 상태($100,000) 자동 생성 |

---

## 6. 의존성 (`requirements.txt`)

```
polygon-api-client>=1.13.0
newsapi-python>=0.2.7
textblob>=0.17.1
pandas>=2.0.0
numpy>=1.24.0
APScheduler>=3.10.0
pandas_market_calendars>=4.3.0
python-dotenv>=1.0.0
requests>=2.31.0
pytz>=2024.1
```

---

## 7. 환경 변수 (`.env`)

```env
POLYGON_API_KEY=your_polygon_api_key_here
NEWS_API_KEY=your_newsapi_key_here
```

---

## 8. 테스트 시나리오

| TC | 시나리오 | 검증 포인트 |
|----|----------|------------|
| TC-01 | RSI 계산 정확성 | pandas로 수동 계산값과 일치 |
| TC-02 | Sentiment 스케일링 | polarity=-1 → score=0, +1 → score=100, 0 → score=50 |
| TC-03 | Strong Buy 신호 | sentiment=75, rsi=25 → STRONG_BUY |
| TC-04 | Strong Sell 신호 | sentiment=25, rsi=75 → STRONG_SELL |
| TC-05 | 엣지케이스 신호 | sentiment=55, rsi=55 → NEUTRAL |
| TC-06 | 신규 매수 | STRONG_BUY 신호 + 포지션 없음 → 매수 체결 |
| TC-07 | 추가 매수 조건 | BUY + open < avg_price → 추가 매수 |
| TC-08 | 추가 매수 스킵 | BUY + open >= avg_price → 스킵 |
| TC-09 | Neutral 매도 조건 | NEUTRAL + profit=1.5% → 매도 |
| TC-10 | Neutral 매도 스킵 | NEUTRAL + profit=0.5% → 스킵 |
| TC-11 | 14일 조정 매도 | 보유 15일 + profit=0.5% → 매도 (0.25% 기준) |
| TC-12 | NYSE 휴장일 | 잡 실행 안 됨 |

---

## 9. 로깅 설계

```python
# 모든 모듈에서 공통 사용
import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("data/trading.log"),
        logging.StreamHandler()  # 콘솔 동시 출력
    ]
)
```

로그 이벤트:
- `INFO`: 신호 생성, 가상 주문 체결, 포트폴리오 업데이트
- `WARNING`: 데이터 부족, 뉴스 없음, 매수 자금 부족
- `ERROR`: API 호출 실패 (재시도 포함), 파일 저장 실패

---

## 10. 보안 고려사항

- `.env` 파일은 `.gitignore`에 반드시 추가
- API 키는 절대 `config.py` 하드코딩 금지
- `data/` 디렉토리는 로컬 전용 (git 제외 권장)

---

## 11. 구현 가이드

### 11.1 구현 순서

```
Phase 1: 기반 설정
  1. .env + config.py + requirements.txt + .gitignore
  2. data/ 디렉토리 생성
  3. 로깅 설정

Phase 2: 데이터 수집
  4. collector.py (get_ohlcv, get_news, _request_with_retry)
  5. python main.py --run-now 으로 API 연결 확인

Phase 3: 지표 계산
  6. indicators.py (calculate_rsi, calculate_rsi_ma, calculate_sentiment_score)
  7. signals.py (determine_signal, generate_signals_for_all)

Phase 4: 페이퍼 트레이딩
  8. portfolio.py (dataclasses, load/save/record)
  9. trader.py (process_orders, buy/sell logic)

Phase 5: 스케줄러 및 통합
  10. scheduler.py (잡 정의, NYSE 캘린더 연동)
  11. main.py (argparse 진입점)
  12. 통합 테스트 (--run-now 로 전체 플로우 확인)
```

### 11.2 실행 방법

```bash
# 1. 의존성 설치
pip install -r requirements.txt

# 2. TextBlob 코퍼스 다운로드 (최초 1회)
python -c "import textblob; textblob.download_corpora()"

# 3. .env 파일 작성
# POLYGON_API_KEY=...
# NEWS_API_KEY=...

# 4. 즉시 신호 테스트
python main.py --run-now

# 5. 포트폴리오 현황 확인
python main.py --report

# 6. 실시간 스케줄러 시작
python main.py
```

### 11.3 Session Guide

| Module | 파일 | 예상 구현량 |
|--------|------|------------|
| M1: 설정/기반 | config.py, .env, requirements.txt, .gitignore | ~50 lines |
| M2: 데이터 수집 | collector.py | ~120 lines |
| M3: 지표 계산 | indicators.py | ~60 lines |
| M4: 신호 생성 | signals.py | ~70 lines |
| M5: 포트폴리오 | portfolio.py | ~120 lines |
| M6: 트레이더 | trader.py | ~100 lines |
| M7: 스케줄러+진입점 | scheduler.py, main.py | ~80 lines |

**권장 세션 분할**:
- Session 1: M1 + M2 (환경 설정 + API 연결 확인)
- Session 2: M3 + M4 (지표 및 신호 로직)
- Session 3: M5 + M6 (페이퍼 트레이딩 엔진)
- Session 4: M7 (스케줄러 통합 + 전체 테스트)
