# Design Ref: §2.1 — 모든 설정값을 한 곳에서 관리, 코드 수정 없이 종목/파라미터 변경 가능
import os
from dotenv import load_dotenv

load_dotenv()

# --- API 키 ---
POLYGON_API_KEY: str = os.getenv("POLYGON_API_KEY", "")
NEWS_API_KEY: str = os.getenv("NEWS_API_KEY", "")       # 레거시 — Finnhub 전환 후 미사용
FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "")

# --- 대상 종목 (Plan SC-05: .env 또는 여기서 변경) ---
SYMBOLS: list[str] = ["NVDA", "TSLA"]

# 종목별 검색에 사용할 회사명 (NewsAPI 검색 품질 향상)
COMPANY_NAMES: dict[str, str] = {
    "NVDA": "Nvidia",
    "": "TQQQ"
}

# --- API 엔드포인트 ---
POLYGON_BASE_URL = "https://api.massive.com"
NEWSAPI_BASE_URL = "https://newsapi.org/v2"             # 레거시 — 미사용
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
FINNHUB_REQUEST_DELAY = 1.0                             # 초 (60 req/min → 1초 간격)

# --- 지표 파라미터 ---
RSI_PERIOD = 14
RSI_MA_PERIOD = 7
OHLCV_LOOKBACK_DAYS = 70       # RSI 계산용 (~60거래일 + 버퍼)
NEWS_LOOKBACK_DAYS = 7
NEWS_MAX_ARTICLES = 100        # Finnhub 수집 최대 기사 수
VOLUME_MA_PERIOD = 20          # Volume MA 계산 기간

# --- 신호 임계값 ---
SENTIMENT_STRONG_BUY = 70.0
SENTIMENT_BUY = 50.0
SENTIMENT_NEUTRAL_LOW = 40.0
SENTIMENT_NEUTRAL_HIGH = 60.0
SENTIMENT_STRONG_SELL = 30.0

RSI_OVERSOLD = 30.0
RSI_NEUTRAL_LOW = 40.0
RSI_NEUTRAL_HIGH = 60.0
RSI_OVERBOUGHT = 70.0

# --- 페이퍼 트레이딩 파라미터 ---
INITIAL_CASH = 100_000.0           # 초기 가상 자금 (USD)
POSITION_SIZE_PCT = 0.20           # 종목당 투자 비율 (20%)
PROFIT_TARGET_PCT = 1.0            # 기본 Neutral 매도 목표 수익률 (%)
PROFIT_TARGET_ADJUSTED_PCT = 0.25  # 14일 경과 후 조정 목표 수익률 (%)
HOLDING_PERIOD_DAYS = 14           # 보유 기간 조정 기준일

# --- 스케줄러 (ET 기준) ---
SIGNAL_JOB_HOUR = 16       # 신호 계산 실행 시각
SIGNAL_JOB_MINUTE = 30
ORDER_JOB_HOUR = 9         # 가상 주문 처리 시각
ORDER_JOB_MINUTE = 35
TIMEZONE = "America/New_York"

# --- 데이터 저장 경로 ---
DATA_DIR = "data"
PORTFOLIO_FILE = "data/portfolio.json"
TRADES_FILE = "data/trades.csv"
SIGNALS_FILE = "data/signals.json"
LOG_FILE = "data/trading.log"

# --- Signal V2: Neutral 필터 ---
NEUTRAL_FILTER_THRESHOLD = 0.80      # FinBERT neutral 확률 이상이면 제외
NEUTRAL_FILTER_MIN_ARTICLES = 10     # 필터 후 최소 유효 기사 수 (미달 시 폴백)

# --- Signal V2: Volume Spike ---
VOLUME_SPIKE_MULTIPLIER = 2.0        # 20일 평균 대비 급증 배수
VOLUME_SPIKE_RSI_MAX = 40.0          # Volume Spike 발동 시 RSI 상한

# --- Signal V2: 감성 Provider ---
SENTIMENT_PROVIDERS: list[str] = ["finbert", "textblob"]  # 활성 Provider 목록

# --- 백테스팅 ---
BACKTEST_START = "2026-02-01"
BACKTEST_END   = "2026-04-01"
BACKTEST_CACHE_FILE = "data/backtest_cache.json"
ARTICLES_DETAIL_FILE = "data/articles_detail.json"

# --- Market RSI Filter (Design Ref: §2.3) ---
MARKET_SYMBOL = "QQQ"              # 나스닥 100 추종 ETF
MARKET_RSI_OVERBOUGHT = 70.0       # 초과열 임계값 → 매수 신호 다운그레이드
MARKET_RSI_DOWNTREND = 30.0        # 하락 추세 임계값 → 매수 신호 다운그레이드

# --- API 요청 설정 ---
REQUEST_MAX_RETRIES = 3
REQUEST_RETRY_BASE_DELAY = 1.0     # 초 (지수 백오프 기반)
REQUEST_TIMEOUT = 10               # 초
