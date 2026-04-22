# Design Ref: §2.1 — 모든 설정값을 한 곳에서 관리, 코드 수정 없이 종목/파라미터 변경 가능
import os
from dotenv import load_dotenv

load_dotenv()

# --- API 키 ---
POLYGON_API_KEY: str = os.getenv("POLYGON_API_KEY", "")
NEWS_API_KEY: str = os.getenv("NEWS_API_KEY", "")       # 레거시 — Finnhub 전환 후 미사용
FINNHUB_API_KEY: str = os.getenv("FINNHUB_API_KEY", "")

# --- OpenAI (Design Ref: §2.1 GPTProvider) ---
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
GPT_MODEL = "gpt-4o"
GPT_BATCH_SIZE = 10                 # 호출당 처리 기사 수
GPT_CACHE_FILE = "data/gpt_cache.json"
GPT_POST_TITLE_MAX = 200            # Reddit 제목 최대 200자
GPT_POST_BODY_MAX = 300             # Reddit 본문 최대 300자
GPT_TOP_COMMENTS = 3                # Top 댓글 수
GPT_COMMENT_MAX = 100               # 댓글당 최대 100자

# --- Reddit (Design Ref: §2.4 RedditCollector) ---
REDDIT_CLIENT_ID: str = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET: str = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = "trading-bot/1.0"
REDDIT_SUBREDDITS = [
    "wallstreetbets", "investing", "stocks",
    "options", "StockMarket", "thetagang",
]
REDDIT_ALLOWED_FLAIRS = ["DD", "Discussion", "Fundamentals", "Daily Discussion", "Earnings"]
REDDIT_LOOKBACK_HOURS = 24          # 최근 24시간 게시글 수집
REDDIT_DAILY_THREAD_COMMENTS = 1000  # Daily Discussion Thread 수집 댓글 수 (wsb-daily-comments)
# 서브레딧별 Daily Discussion Thread 탐색 패턴 (소문자 부분일치)
REDDIT_DAILY_PATTERNS: dict[str, list[str]] = {
    "wallstreetbets": ["what are your moves", "daily discussion"],
    "investing":      ["daily general discussion", "daily discussion"],
    "stocks":         ["daily discussion"],
    "options":        ["megathread", "safe haven", "what are your moves"],
    "StockMarket":    ["daily discussion"],
    "thetagang":      ["daily discussion", "what are your moves"],
}
REDDIT_DATA_DIR = "data/reddit"     # data/reddit/YYYY-MM-DD/ 루트

# 수동 제외 티커 — 실제 종목이 아닌 단어가 수집될 때 여기에 추가
REDDIT_TICKER_BLACKLIST: set[str] = {
    "WTF", "WAR", "YOU", "ARE", "TACO", "USO",
}
REDDIT_MODE = False                 # True: Reddit 실시간 모드 활성화
REDDIT_BACKTEST_MIN_DAYS = 14       # 최소 거래일 미만 시 경고

# --- Reddit 신호 파라미터 (Design Ref: §2.5 WSBSignalEngine) ---
WSB_CONSENSUS_RATIO = 1.5           # bullish/bearish 진입 기준
WSB_SELL_RATIO = 1.5                # bearish/bullish 청산 기준
TOP_N = 3                           # 매일 선정 최대 종목 수
MAX_POSITIONS = 10                  # 최대 동시 보유 포지션 수
MA_ENTRY_PERIOD = 30                # 진입 30MA 기준
MA_BREAKDOWN_GRACE_DAYS = 5         # 30MA 청산 유예 기간 (보유 5일↑)
ATR_PERIOD = 14

# --- 손절매 / 익절 (Design Ref: §3.2) ---
STOP_LOSS_PCT = -7.0                # 손절: 진입가 대비 -7% → 즉시 SELL
TRAILING_STOP_PCT = -5.0            # 트레일링 익절: 최고점 대비 -5% (수익 중일 때만)

# --- Position Sizing (Design Ref: §2.3 PositionSizer) ---
POSITION_SIZING = "equal"           # "equal" | "sentiment" | "volatility"
EQUAL_POSITION_PCT = 0.10           # Equal: 10% 고정
SENTIMENT_SIZE_HIGH_THRESHOLD = 0.80
SENTIMENT_SIZE_MID_THRESHOLD = 0.65
SENTIMENT_SIZE_HIGH = 0.15          # bullish ratio >= 80% → 15%
SENTIMENT_SIZE_MID = 0.10           # bullish ratio >= 65% → 10%
SENTIMENT_SIZE_LOW = 0.05           # 나머지 → 5%
VOLATILITY_TARGET_RISK = 0.01       # 포지션당 1% 리스크
VOLATILITY_MIN_PCT = 0.05           # 최소 5%
VOLATILITY_MAX_PCT = 0.15           # 최대 15%

# --- 수수료 (한국투자증권 미국주식 위탁매매, Design Ref: §2.6) ---
COMMISSION_RATE = 0.0025            # 0.25% (매수/매도 각각)
COMMISSION_MIN_USD = 2.0            # 최소 $2.0 per leg

# --- 대상 종목 (Plan SC-05: .env 또는 여기서 변경) ---
SYMBOLS: list[str] = ["NVDA", "TSLA"]

# 종목별 검색에 사용할 회사명 (NewsAPI 검색 품질 향상)
COMPANY_NAMES: dict[str, str] = {
    "NVDA": "Nvidia",
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "GOOGL": "Google",
    "AMZN": "Amazon",
    "META": "Meta",
    "TSLA": "Tesla",
    "AMD": "AMD",
    "PLTR": "Palantir",
    "SOFI": "SoFi",
    "MSTR": "MicroStrategy",
    "COIN": "Coinbase",
    "SMCI": "Super Micro",
    "ARM": "ARM Holdings",
    "HOOD": "Robinhood",
    "GME": "GameStop",
    "AMC": "AMC",
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

# --- WSB Signal V3: 매수 기준 (Design Ref: §wsb-signal-v3 §3) ---
WSB_STRONG_BUY_SCORE = 70.0          # 기본 STRONG_BUY score 임계값
WSB_BUY_SCORE = 55.0                 # 기본 BUY score 임계값 (기존 50 → 55 강화)
WSB_NEUTRAL_RATIO_MAX = 0.70         # neutral/total 초과 시 NEUTRAL 강제

# --- WSB Signal V3: Mention Velocity ---
WSB_VELOCITY_LOOKBACK_DAYS = 7       # 7일 평균 멘션
WSB_VELOCITY_HIGH_THRESHOLD = 2.0    # HIGH_MOMENTUM 기준
WSB_VELOCITY_LOW_THRESHOLD = 0.5     # DECLINING 기준
WSB_VELOCITY_SCORE_ADJUST = 5.0      # 보정 점수 (±5)
WSB_NEW_SPIKE_MIN_MENTIONS = 20      # 신규 종목 NEW_SPIKE 최소 멘션
WSB_NEW_SPIKE_SCORE = 65.0           # NEW_SPIKE STRONG_BUY score 기준

# --- WSB Signal V3: 청산 조건 ---
WSB_SENTIMENT_REVERSAL_RATIO = 0.60  # entry_score × 0.6 미만 시 감성 역전
WSB_RSI_EXIT_OVERBOUGHT = 70.0       # 청산 RSI 과매수 기준
WSB_GAP_DOWN_PCT = -5.0              # Gap Down 임계값 (%) — reddit_portfolio 전용
WSB_RSI_HOLD_ONCE = True             # HIGH_MOMENTUM 시 RSI 과매수 1회 유예

# --- WSB Signal V3: 데이터 파일 ---
MENTION_HISTORY_FILE = "data/mention_history.json"
POSITION_SCORES_FILE = "data/position_scores.json"

# --- API 요청 설정 ---
REQUEST_MAX_RETRIES = 3
REQUEST_RETRY_BASE_DELAY = 1.0     # 초 (지수 백오프 기반)
REQUEST_TIMEOUT = 10               # 초
