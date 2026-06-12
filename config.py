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
GPT_MODEL = "gpt-5.4-mini"
GPT_MODEL_ALIAS = "gpt5"
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
REDDIT_DAILY_THREAD_COMMENTS = 1000  # Daily Discussion Thread 수집 댓글 수 — 품질 필터 통과분 기준 (wsb-daily-comments)
REDDIT_DAILY_THREAD_REPLACE_MORE = 10  # Daily Thread MoreComments 확장 횟수 — 대형 스레드(WSB 8천+)에서 1000개 확보용

# --- comment-aware-sentiment: 댓글 개별 감성 집계 (Design Ref: §6) ---
COMMENT_COLLECT_NORMAL = 100         # 일반 글 댓글 상위 N
COMMENT_COLLECT_DD = 1000            # DD형 글 댓글 상위 N
DD_FLAIRS = {"DD", "Discussion"}     # DD형 판별 flair 집합 (소문자 비교)
COMMENT_REPLACE_MORE_LIMIT = 4       # replace_more 확장 상한 (0=이미 로드분만, 비용 가드 NFR-02)
COMMENT_COLLECT_TIMEOUT_SEC = 20     # 글당 댓글 수집 wall-clock 타임아웃(초)
COMMENT_MAX_DD_POSTS_PER_SUB = 10    # 서브레딧당 DD형 대량수집 글 수 상한
COMMENT_MIN_LEN = 15                 # 댓글 최소 글자수 (품질 필터 FR-08)
COMMENT_TEXT_MAX = 200               # FinBERT 입력용 댓글 최대 길이
COMMENT_BOT_AUTHORS = {"AutoModerator", "VisualMod"}  # 봇 작성자 제외
# 댓글 방향 가중은 기존 COMMUNITY_COMMENT_MENTION_WEIGHT(0.5) 재사용 (신규 상수 없음)
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
    "ALL", "EOD", "WAY", "OUT", "AMA",
}
REDDIT_MODE = False                 # True: Reddit 실시간 모드 활성화
REDDIT_BACKTEST_MIN_DAYS = 14       # 최소 거래일 미만 시 경고
REDDIT_BACKTEST_FETCH_THROTTLE = 12.0  # replay OHLCV 사전수집 시 캐시 미스당 대기(초, 무료 플랜 5req/min)

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
# timing-fix 2026-06-13: 수집 16:30 → 08:45 ET. 기존엔 전일 16:30 수집 여론을 익일
# 09:35에 매매(17시간 지연 + 오버나잇 갭 부담). 소셜 감성은 반감기가 짧으므로
# 장 시작 직전 수집(최근 24h = 전일 장중+오버나잇+프리장 여론)으로 지연을 50분으로 단축.
# 수집 잡 실측 소요 6~9분 → 09:35 주문 잡까지 여유 충분.
SIGNAL_JOB_HOUR = 8        # Reddit 수집/신호 준비 실행 시각 (구 16:30)
SIGNAL_JOB_MINUTE = 45
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
BACKTEST_SNAPSHOT_DIR = "data/backtest_snapshots"
ARTICLES_DETAIL_FILE = "data/articles_detail.json"

# --- Market RSI Filter (Design Ref: §2.3) ---
MARKET_SYMBOL = "QQQ"              # 나스닥 100 추종 ETF
MARKET_RSI_OVERBOUGHT = 70.0       # 초과열 임계값 → 매수 신호 다운그레이드
MARKET_RSI_DOWNTREND = 30.0        # 하락 추세 임계값 → 매수 신호 다운그레이드

# --- WSB Signal V3: 매수 기준 (Design Ref: §wsb-signal-v3 §3) ---
WSB_STRONG_BUY_SCORE = 68.0          # 기본 STRONG_BUY score 임계값 (70→68, FinBERT 보수성 완화 2026-06-06)
WSB_BUY_SCORE = 52.0                 # 기본 BUY score 임계값 (50→55 강화 후 →52 소폭 완화 2026-06-06)
# funnel-fix 2026-06-13: 중립비율 킬스위치(0.75)를 폐지하고 "방향성 멘션 최소치 + 극단 노이즈 컷"으로 대체.
# FinBERT는 소셜 텍스트에서 중립 편향이 강해 표본이 클수록 neutral/total이 올라가므로,
# 중립비율이 아니라 방향성(bull+bear) 의견 수로 신호 유효성을 판정한다.
WSB_NEUTRAL_RATIO_MAX = 0.95         # 극단 노이즈 컷 (0.75→0.95) — 초과 시에만 NEUTRAL 강제
WSB_MIN_DIRECTIONAL_MENTIONS = 3     # bull+bear 합계 미만 → NEUTRAL 강제 (극소표본 노이즈 차단)
WSB_SCORE_SHRINKAGE_K = 8            # score를 50으로 수축하는 prior 멘션 수 — score*=50+(raw-50)·n/(n+K)
WSB_RSI_BUY_MAX = 70.0               # 매수 허용 RSI 상한 — 과매수만 회피 (기존 30~50 역추세 창 폐지)

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
SCORE_HISTORY_FILE = "data/score_history.json"   # community-opinion-trend-sizing: 일별 의견 점수 이력 (라이브용)

# --- Community Opinion Trend Sizing (Design Ref: community-opinion-trend-sizing §5) ---
# opinion_score band (= 기존 sentiment score 0~100 재사용)
WSB_OPINION_SCORE_HIGH = 80.0
WSB_OPINION_SCORE_MID  = 70.0
WSB_OPINION_SCORE_LOW  = 57.0          # 미만 진입 제외 (60→57, router/sizer 게이트 완화 2026-06-08)
WSB_OPINION_FACTOR_HIGH = 1.2
WSB_OPINION_FACTOR_MID  = 1.0
WSB_OPINION_FACTOR_LOW  = 0.7

WSB_OPINION_TREND_LOOKBACK_DAYS = 3
WSB_OPINION_TREND_UP_FACTOR   = 1.15
WSB_OPINION_TREND_FLAT_FACTOR = 1.0
WSB_OPINION_TREND_DOWN_FACTOR = 0.5

WSB_OPINION_PERSISTENCE_MIN_DAYS    = 2
WSB_OPINION_PERSISTENCE_STRONG_DAYS = 3
WSB_OPINION_PERSISTENCE_WEAK_FACTOR   = 0.6
WSB_OPINION_PERSISTENCE_NORMAL_FACTOR = 1.0
WSB_OPINION_PERSISTENCE_STRONG_FACTOR = 1.2

WSB_OPINION_CONSENSUS_STRONG_RATIO = 2.0
WSB_OPINION_CONSENSUS_MIN_RATIO    = 1.5

# funnel-fix 2026-06-13: FinBERT 중립 편향 감안 0.70/0.75 → 0.90 (극단 노이즈만 차단/청산,
# 0.5~0.9 구간은 sizer의 neutral damper가 사이즈 축소로 처리)
WSB_OPINION_NEUTRAL_ENTRY_MAX  = 0.90  # 초과 진입 제외
WSB_OPINION_NEUTRAL_EXIT_RATIO = 0.90  # 초과 청산
WSB_OPINION_REVERSAL_RATIO     = 0.65  # opinion_score < entry_score × 0.65 → 청산

WSB_OPINION_NEW_SPIKE_FACTOR      = 0.5
WSB_OPINION_HIGH_ATTENTION_FACTOR = 1.1
WSB_OPINION_DECLINING_FACTOR      = 0.6

WSB_OPINION_SIZE_FACTOR_MIN = 0.0
WSB_OPINION_SIZE_FACTOR_MAX = 1.3

WSB_USE_PROFIT_TARGET = False           # 고정 익절 비활성 (의견 변화 청산 우선)

# ===========================================================================
# Community Opinion Agent (Design Ref: community-opinion-agent §6 / Plan §5.1)
# v0 Universe/Cost · v1 source quality/ambiguity/snapshot · v2 memory/reflection
# · v3 decision router. 모든 신규 기능은 flag로 토글 — OFF 시 기존 동작 회귀 0.
# 의견 파라미터는 기존 WSB_OPINION_* 값을 alias (D2: 단일 소스, 회귀 보호).
# ===========================================================================

# --- ⓪ Universe Filter (Design Ref: §3.1) ---
COMMUNITY_UNIVERSE_MODE = "community_liquid"
# options: "sp500_only" | "nasdaq100_only" | "sp500_nasdaq100"
#          | "liquid_us" | "community_liquid" | "custom_watchlist"
COMMUNITY_ENABLE_UNIVERSE_FILTER = True
COMMUNITY_UNIVERSE_DATA_DIR = "data/universe"   # sp500.json, nasdaq100.json
COMMUNITY_CUSTOM_WATCHLIST: list[str] = []      # custom_watchlist 모드 종목

COMMUNITY_MIN_PRICE_USD = 5.0
COMMUNITY_MIN_AVG_DOLLAR_VOLUME = 20_000_000    # 최근 20일 평균 거래대금(USD)
COMMUNITY_MIN_MARKET_CAP = 1_000_000_000        # 시총 데이터 있을 때만 게이팅
COMMUNITY_EXCLUDE_OTC = True
COMMUNITY_EXCLUDE_PENNY_STOCKS = True
COMMUNITY_ALLOW_NON_INDEX_IF_LIQUID = True
COMMUNITY_NON_INDEX_SIZE_MULTIPLIER = 0.5       # EXPANDED/COMMUNITY_LIQUID size 배수 (CORE=1.0)
COMMUNITY_NEW_SYMBOL_OBSERVATION_DAYS = 2

# --- ⓪ Cost-aware Trade Filter (Design Ref: §3.2) ---
COMMUNITY_ENABLE_COST_AWARE_FILTER = True
COMMUNITY_ESTIMATED_SLIPPAGE_PCT = 0.001        # 0.1%
COMMUNITY_ESTIMATED_SPREAD_PCT = 0.001          # 0.1%
# funnel-fix 2026-06-13: 2.0→1.5 — ATR 최소게이트(1.0%)·DOWNSIZE 밴드와 삼중 게이트라
# 요구 ATR이 1.8%+로 과도했음. 1.5배(왕복 0.9% 기준 ATR 1.35%)로 완화.
COMMUNITY_MIN_EDGE_TO_COST_MULTIPLIER = 1.5     # edge < cost×1.5 → SKIP
COMMUNITY_MIN_ATR_PCT_FOR_TRADE = 1.0           # ATR pct(%) 미만 → SKIP
COMMUNITY_MAX_TURNOVER_PER_DAY = 0.25
COMMUNITY_MIN_HOLDING_DAYS_SOFT = 2
COMMUNITY_COOLDOWN_DAYS_AFTER_EXIT = 2

# --- v1 Source Quality Filter (Design Ref: §3.6 reddit_collector) ---
COMMUNITY_ENABLE_SOURCE_QUALITY_FILTER = True
COMMUNITY_ENABLE_TICKER_AMBIGUITY_FILTER = True
COMMUNITY_ENABLE_DAILY_OPINION_SNAPSHOT = True

COMMUNITY_HIGH_QUALITY_FLAIRS = [
    "DD", "Discussion", "News", "Options",
    "Technical Analysis", "Technicals", "Fundamentals", "Stocks",
]
COMMUNITY_LOW_QUALITY_FLAIRS = [
    "Meme", "Gain", "Loss", "Shitpost", "Satire", "Storytime", "Donation",
]
COMMUNITY_FLAIR_WEIGHT_DD = 1.5
COMMUNITY_FLAIR_WEIGHT_DISCUSSION = 1.0
COMMUNITY_FLAIR_WEIGHT_NEWS = 1.2
COMMUNITY_FLAIR_WEIGHT_OPTIONS = 0.9
COMMUNITY_FLAIR_WEIGHT_TECHNICAL = 1.0
COMMUNITY_FLAIR_WEIGHT_FUNDAMENTALS = 1.2
COMMUNITY_FLAIR_WEIGHT_DAILY_THREAD = 0.5
COMMUNITY_FLAIR_WEIGHT_LOW_QUALITY = 0.0
COMMUNITY_FLAIR_WEIGHT_DEFAULT = 1.0            # flair 없는 기존 데이터 fallback

COMMUNITY_TITLE_MENTION_WEIGHT = 2.0
COMMUNITY_BODY_MENTION_WEIGHT = 1.0
COMMUNITY_COMMENT_MENTION_WEIGHT = 0.6   # (0.5→0.6, 댓글 방향 희석 완화 2026-06-08)

# comment-aware-sentiment(Act 튜닝, 2026-06-06): 3→1. 댓글이 score() n_valid(≥10)
# 게이트를 채우므로 "충분한 DD 토론" 판정은 거기서 수행. 글 수 하한은 단일 DD 스레드가
# 통과하도록 완화(단일 DD 주도 = 의도된 동작). consensus_ratio·neutral·N≥10·품질가중이
# 실질 필터 역할.
COMMUNITY_MIN_DAILY_MENTIONS = 1
COMMUNITY_CONSENSUS_MIN_RATIO = 1.5
# funnel-fix 2026-06-13: 0.75→0.90 — router/snapshot의 중립 하드블록을 극단 노이즈 전용으로 완화.
# 토론량 많은 종목일수록 FinBERT 중립비율이 올라가 정보가 많은 종목을 역차별하던 문제 해소.
COMMUNITY_NEUTRAL_RATIO_MAX = 0.90

# --- v1 Ticker Ambiguity Filter (Design Ref: §3.6) ---
COMMUNITY_TICKER_AMBIGUITY_BLACKLIST = [
    "ALL", "IT", "DD", "NOW", "ARE", "SO", "ON",
    "LOW", "COST", "KEY", "FOR", "CEO", "AI",
]
COMMUNITY_SINGLE_LETTER_TICKER_REQUIRE_DOLLAR = True

# --- v1 Opinion factors — 기존 WSB_OPINION_* alias (D2: 단일 소스, 회귀 0) ---
COMMUNITY_OPINION_SCORE_HIGH = WSB_OPINION_SCORE_HIGH   # 80
COMMUNITY_OPINION_SCORE_MID = WSB_OPINION_SCORE_MID     # 70
COMMUNITY_OPINION_SCORE_LOW = WSB_OPINION_SCORE_LOW     # 60 (미만 진입 제외)
COMMUNITY_OPINION_FACTOR_HIGH = WSB_OPINION_FACTOR_HIGH # 1.2
COMMUNITY_OPINION_FACTOR_MID = WSB_OPINION_FACTOR_MID   # 1.0
COMMUNITY_OPINION_FACTOR_LOW = WSB_OPINION_FACTOR_LOW   # 0.7
COMMUNITY_OPINION_FACTOR_SKIP = 0.0

COMMUNITY_OPINION_TREND_LOOKBACK_DAYS = WSB_OPINION_TREND_LOOKBACK_DAYS  # 3
COMMUNITY_OPINION_TREND_UP_FACTOR = WSB_OPINION_TREND_UP_FACTOR          # 1.15
COMMUNITY_OPINION_TREND_FLAT_FACTOR = WSB_OPINION_TREND_FLAT_FACTOR      # 1.0
COMMUNITY_OPINION_TREND_DOWN_FACTOR = WSB_OPINION_TREND_DOWN_FACTOR      # 0.5

COMMUNITY_OPINION_PERSISTENCE_MIN_DAYS = WSB_OPINION_PERSISTENCE_MIN_DAYS        # 2
COMMUNITY_OPINION_PERSISTENCE_STRONG_DAYS = WSB_OPINION_PERSISTENCE_STRONG_DAYS  # 3
COMMUNITY_OPINION_PERSISTENCE_WEAK_FACTOR = WSB_OPINION_PERSISTENCE_WEAK_FACTOR      # 0.6
COMMUNITY_OPINION_PERSISTENCE_NORMAL_FACTOR = WSB_OPINION_PERSISTENCE_NORMAL_FACTOR  # 1.0
COMMUNITY_OPINION_PERSISTENCE_STRONG_FACTOR = WSB_OPINION_PERSISTENCE_STRONG_FACTOR  # 1.2

# neutral noise band factor (신규 — snapshot/router용)
COMMUNITY_NEUTRAL_FACTOR_HIGH_NOISE = 0.0
COMMUNITY_NEUTRAL_FACTOR_MID_NOISE = 0.7
COMMUNITY_NEUTRAL_FACTOR_LOW_NOISE = 1.0

COMMUNITY_NEW_SPIKE_FACTOR = WSB_OPINION_NEW_SPIKE_FACTOR            # 0.5 (단발 폭증 보수)
# 스펙 COMMUNITY_HIGH_ATTENTION_FACTOR=1.05 → 기존 WSB 1.1 alias로 회귀 보호(D2).
COMMUNITY_HIGH_ATTENTION_FACTOR = WSB_OPINION_HIGH_ATTENTION_FACTOR  # 1.1
COMMUNITY_DECLINING_FACTOR = WSB_OPINION_DECLINING_FACTOR            # 0.6

COMMUNITY_SIZE_FACTOR_MIN = WSB_OPINION_SIZE_FACTOR_MIN   # 0.0
COMMUNITY_SIZE_FACTOR_MAX = WSB_OPINION_SIZE_FACTOR_MAX   # 1.3

# --- v1 Daily Opinion Snapshot 저장 (Design Ref: §3.6 wsb_state) ---
COMMUNITY_DATA_DIR = "data/community"
COMMUNITY_DAILY_SNAPSHOT_FILE = "data/community/daily_opinion_snapshots.jsonl"

# --- v2 Community Memory (Design Ref: §3.3) ---
COMMUNITY_MEMORY_ENABLED = True
COMMUNITY_MEMORY_BACKEND = "jsonl"               # 향후 "chroma"|"faiss"
COMMUNITY_MEMORY_TOP_K = 5
COMMUNITY_MEMORY_DIR = "data/community/memory"

# --- v2 Reflection (Design Ref: §3.4) ---
COMMUNITY_REFLECTION_ENABLED = True
COMMUNITY_REFLECTION_FORWARD_RETURNS = [1, 3, 7, 14]
COMMUNITY_LOW_LEVEL_REFLECTION_ENABLED = True
COMMUNITY_HIGH_LEVEL_REFLECTION_ENABLED = True

# --- v3+ Persistent DecisionLog (판단 원본 jsonl) ---
COMMUNITY_DECISION_LOG_ENABLED = True
COMMUNITY_DECISIONS_DIR = "data/community/decisions"          # 기본(라이브외) 경로 루트
COMMUNITY_DECISIONS_FILE = "data/community/decisions/decision_logs.jsonl"
COMMUNITY_BACKTEST_DECISIONS_DIR = "data/community/backtests"  # {run_id}/decisions.jsonl
COMMUNITY_LIVE_DECISIONS_FILE = "data/community/live/decisions.jsonl"
COMMUNITY_LIVE_RUN_SUMMARIES_FILE = "data/community/live/run_summaries.jsonl"

# --- Daily Decision Report (daily-decision-report) ---
COMMUNITY_DECISION_REPORT_ENABLED = True             # run_live 종료 시 보고서 자동 생성
COMMUNITY_LIVE_REPORTS_DIR = "data/community/live/reports"  # YYYY-MM-DD.md 저장 루트

# run_live가 오늘 수집분이 없을 때 최근 수집일 캐시를 쓰는데, 이 일수 초과 시 경고(stale 가시화).
# 정상 운영(timing-fix): 당일 08:45 ET 수집 → 같은 날 09:35 ET 주문잡 사용 (지연 ~50분).
COMMUNITY_LIVE_MAX_POSTS_AGE_DAYS = 4

# --- Live Scheduler Deploy (live-scheduler-deploy §5.3) — 무인 실주문 안전장치 ---
TRADING_HALT_FILE = "data/TRADING_HALT"      # 이 파일 존재 시 주문만 스킵(키스위치). env TRADING_HALT=1도 동일
HEARTBEAT_FILE = "data/heartbeat.json"       # {job: last_success_utc} — 워치독 신선도 판단
MAX_DAILY_BUYS = 5                           # 하루 신규 매수 건수 상한
MAX_TOTAL_EXPOSURE_PCT = 60.0                # 총자산 대비 보유 평가액 상한 %
MAX_SYMBOL_WEIGHT_PCT = 20.0                 # 종목당 비중 상한 %
WATCHDOG_STALE_MINUTES = 90                  # alive heartbeat 한도 — 프로세스 hang 추정
WATCHDOG_ORDER_STALE_MINUTES = 5760          # order heartbeat 한도 4일 — 주말·휴장 감안
WATCHDOG_ORDER_GRACE_MINUTES = 90            # 09:35 ET 주문 잡 완료 유예시간
WATCHDOG_SIGNAL_GRACE_MINUTES = 180          # 16:30 ET 신호 잡 완료 유예시간
WATCHDOG_RESTART_STATE_FILE = "data/watchdog_restart_state.json"  # 일일 잡 재시작 중복 방지
HEARTBEAT_ALIVE_INTERVAL_MINUTES = 5         # alive heartbeat 갱신 주기
SLACK_WEBHOOK_URL = ""                       # 미설정 시 알림 no-op (env로 주입 권장)

# --- v3 Decision Router / LLM Router (Design Ref: §3.5) ---
# 2026-06-13 ON 전환 — rule 1차 판단의 승인/축소/보류만 가능(자율매매 불가, rule SKIP
# 못 뒤집음). 비용: gpt-5.4-mini, 라이브 일일 상한 COMMUNITY_LLM_LIVE_MAX_CALLS(50).
COMMUNITY_LLM_ROUTER_ENABLED = True
COMMUNITY_LLM_ROUTER_MODEL = "gpt4"              # 실호출은 config.GPT_MODEL로 매핑(D3)
COMMUNITY_LLM_ROUTER_REQUIRE_STRICT_JSON = True
COMMUNITY_LLM_ROUTER_FALLBACK_TO_RULE_BASED = True
COMMUNITY_LLM_ROUTER_MAX_TOKENS = 1200
COMMUNITY_LLM_ROUTER_TEMPERATURE = 0.0
COMMUNITY_LLM_ROUTER_MIN_CONFIDENCE = 0.5        # 미만 시 rule-based 우선

# --- Community Opinion Agent — Live (KIS 모의투자 배선, community-opinion-agent-live) ---
# 실매매 전략 스위치: "agent"=커뮤니티 여론 에이전트 / "news"=기존 뉴스-RSI (가역)
# scheduler가 이 값으로 분기 (module-5). 변경 전엔 동작 영향 없음.
LIVE_STRATEGY = "agent"
COMMUNITY_LIVE_UNIVERSE_MODE = "community_liquid"   # 라이브 universe 기본
COMMUNITY_LIVE_DRY_RUN_DEFAULT = True               # --agent-run-now 기본 dry-run (실모의주문 차단)
COMMUNITY_LLM_LIVE_MAX_CALLS = 50                   # 라이브 1회 구동당 LLM 호출 상한(초과→rule fallback)
COMMUNITY_LIVE_STRATEGY_KEY = "agent_live"          # 라이브 포트폴리오 state 파일 키

# --- API 요청 설정 ---
REQUEST_MAX_RETRIES = 3
REQUEST_RETRY_BASE_DELAY = 1.0     # 초 (지수 백오프 기반)
REQUEST_TIMEOUT = 10               # 초

# --- 라이브 OHLCV 수집 (Polygon 무료 429 회피, live-scheduler) ---
# 종목별 최근 스냅샷이 이 일수 내면 재사용(정확 범위 무관) → 매일 전종목 재요청 방지.
LIVE_OHLCV_CACHE_MAX_AGE_DAYS = 4   # 주말 포함 ~최근 거래일
# 실제 신규 수집이 필요한 종목 간 대기(초). 무료 플랜 분당 5회 → 13초. 0이면 throttle 끔.
POLYGON_REQUEST_DELAY = 13.0

# --- KIS (한국투자증권) Design Ref: §4.1 — 모의투자 OpenAPI 연동 ---
KIS_APP_KEY: str = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET: str = os.getenv("KIS_APP_SECRET", "")
KIS_ACCOUNT_NO: str = os.getenv("KIS_ACCOUNT_NO", "")            # "12345678-01" 형식
KIS_PAPER_TRADING: bool = os.getenv("KIS_PAPER_TRADING", "true").lower() == "true"
KIS_BASE_URL_PAPER = "https://openapivts.koreainvestment.com:29443"  # 모의 도메인
KIS_BASE_URL_REAL = "https://openapi.koreainvestment.com:9443"        # 실전 — FR-20으로 차단
KIS_TOKEN_CACHE_FILE = "data/kis_token.json"   # OAuth 24h 캐시
KIS_SYMBOLS_FILE = "data/kis_symbols.json"     # 매매 가능 종목 캐시
KIS_SYMBOLS_REFRESH_DAYS = 7                   # 종목 마스터 갱신 주기

# --- Signal Engine 선택 (Design Ref: §3.3 SignalProvider Protocol) ---
SIGNAL_ENGINE: str = os.getenv("SIGNAL_ENGINE", "finbert")  # "finbert" | "gpt5"
