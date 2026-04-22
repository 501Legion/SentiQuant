# Plan: 뉴스+Reddit 5-Model 감성 분석 비교 전략

**Feature**: reddit-gpt4-quant
**Date**: 2026-04-17
**Status**: Plan (v10 — Gap Down 슬리피지 처리 + Market Cap 충돌 제거)

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 현재 뉴스+TextBlob/FinBERT 2모델만 검증됨. GPT-4 성능 미확인. Reddit WSB/investing/stocks 군중심리 활용법 없음. Reddit 밈주식의 급락 방어 로직 부재 |
| **Solution** | 뉴스 3종 백테스팅 + Reddit Forward Testing(2-4주 실시간 페이퍼 트레이딩). Stop-Loss(-7%)+Trailing Stop(-5%)+Gap Down 즉시 청산 포함. Universe는 Polygon OHLCV 조회 성공 여부로만 필터 |
| **Function UX Effect** | 뉴스: `--backtest --model gpt4` 로 3종 비교. Reddit: 크론탭 자동 실행 → 2-4주 후 전략별 수익률 비교 리포트 |
| **Core Value** | 수수료+손절매+슬리피지 포함 실전 조건에서 Reddit 신호 유효성 검증. 최적 전략 확정 후 실거래 적용 |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 뉴스 vs Reddit, TextBlob vs FinBERT vs GPT-4, Equal vs Sentiment vs Volatility 실증 비교 |
| **WHO** | news-rsi-trading 시스템 운영자 |
| **RISK** | GPT-4 비용 / PRAW rate limit / Reddit 과거 데이터 접근 / ATR 계산 OHLCV 필요 |
| **SUCCESS** | 15가지 전략 각각 수익률 비교 출력, 캐시 재활용, 기존 뉴스 로직 동작 유지 |
| **SCOPE** | 신규 3개(reddit_collector, wsb_signal_engine, position_sizer) + 수정 4개(sentiment_provider, backtester, config, main) |

---

## 1. 전체 전략 비교 Matrix

### 1.1 뉴스 기반 (기존 + GPT-4 신규)

| # | 소스 | 모델 | 상태 |
|---|------|------|------|
| N1 | 뉴스 (Finnhub) | TextBlob | 기존 |
| N2 | 뉴스 (Finnhub) | FinBERT | 기존 |
| N3 | 뉴스 (Finnhub) | GPT-4o | **신규** |

뉴스 백테스팅: 기존 POSITION_SIZE_PCT(20%) 그대로. 종목은 `config.SYMBOLS` 고정. **수수료 포함 P&L.**

### 1.2 Reddit 기반 — Forward Testing (전략 12가지)

| 모델 | Ranking | Position Sizing | 조합 수 |
|------|---------|----------------|---------|
| FinBERT | 총언급량(mentions) | Equal / Sentiment / Volatility | 3 |
| FinBERT | 감성비율(ratio) | Equal / Sentiment / Volatility | 3 |
| GPT-4o | 총언급량(mentions) | Equal / Sentiment / Volatility | 3 |
| GPT-4o | 감성비율(ratio) | Equal / Sentiment / Volatility | 3 |
| **합계** | | | **12** |

> **Reddit 전략은 과거 백테스팅 대신 Forward Testing으로 검증:**
> 코드 완성 후 오늘부터 크론탭으로 실시간 데이터 수집 → 2-4주 후 전략별 수익률 비교.
> 이유: PRAW 과거 데이터 한계 + 실제 시장 조건으로 검증하는 것이 더 신뢰성 높음.

---

## 2. 기존 뉴스 매매 로직 (변경 없이 유지)

> signals.py, trader.py, scheduler.py, portfolio.py 변경 없음.

### 2.1 신호 체계 (5단계, 변경 없음)

| 신호 | 감성 점수 | RSI 조건 |
|------|----------|---------|
| STRONG_BUY | > 70 | < 30 |
| BUY | > 50 | 30 ≤ RSI < 50 |
| NEUTRAL | 40 ~ 60 | 40 ~ 60 |
| SELL | < 50 | > 70 |
| STRONG_SELL | < 30 | > 70 |

### 2.2 진입/청산 (변경 없음)

진입: BUY/STRONG_BUY → 시가 매수 (가용 현금 20%, 추가매수 조건: 시가 < 평균매수가)

청산: SELL/STRONG_SELL → 무조건. NEUTRAL + 순수익 > 1% → 매도. NEUTRAL + 14일↑ + 0.25% → 매도.

---

## 3. Reddit 전용 파이프라인 (신규)

Reddit 시스템은 뉴스 시스템과 **완전히 분리된 별도 파이프라인**. 공유하는 것: FinBERT/GPT-4 Provider 인터페이스만.

**Reddit 소스: 3개 서브레딧 통합** (v7 변경)

| 서브레딧 | 수집 Flair | 특성 |
|---------|-----------|------|
| r/wallstreetbets | DD, Discussion | 모멘텀 신호, 노이즈 있음 |
| r/investing | Fundamentals, Discussion | 펀더멘털, 높은 DD 품질 |
| r/stocks | Daily Discussion, Earnings | 기술적 분석, 균형잡힌 토론 |

종목별 집계 시 3개 서브레딧 게시글 합산. 서브레딧 가중치는 균등(1:1:1).

### 3.1 종목 선정 파이프라인 (매일 16:30 ET)

```
Step 1: 3개 서브레딧 수집 (wsb + investing + stocks)
    → DD/Discussion Flair 게시글에서 언급된 미국 주식 티커 추출
    → 유효성 검사: Polygon.io OHLCV 조회 가능 여부 (NYSE/NASDAQ 상장 여부)
           조회 성공 = 유효 종목. 실패(동전주/비상장) = 자동 제외

Step 2: Consensus 필터
    → FinBERT 또는 GPT-4로 각 게시글 감성 분류 (Bullish/Bearish/Neutral)
    → 종목별 집계:
        bullish_count / bearish_count >= 1.5 → 1차 통과
        (bearish_count = 0이면 bullish_count >= 2로 대체)

Step 3: 30MA 필터
    → 1차 통과 종목 중 전일 종가 < 30MA → 2차 통과
    → 종가 > 30MA → 제외 (이미 급등, 추격 매수 방지)

Step 4: Ranking → Top N 선정
    기준 A (mentions): 총 언급 게시글 수 기준 내림차순 → 상위 TOP_N개
    기준 B (ratio):    bullish/(bullish+bearish) 비율 기준 내림차순 → 상위 TOP_N개

Step 5: 포지션 슬롯 확인
    → 현재 보유 포지션 수 < MAX_POSITIONS이면 빈 슬롯만큼 신규 매수
    → 이미 보유 중인 종목은 중복 매수 안 함
```

**Universe 설계:** 사전 정의 리스트 없음. WSB 언급 종목 + Polygon OHLCV 조회 성공 = 유효 종목. 동전주/비상장 종목은 자동 제외.

---

### 3.2 기존 보유 포지션 관리 (Reddit 모드)

Reddit이 매일 새로운 종목을 찾아와도 기존 포지션을 강제 청산하지 않음.

```
매일 16:30 ET Reddit 스캔:

[09:35 ET 주문 처리 시 — 당일 시가 확인 먼저]
  0. Gap Down 즉시 청산 (슬리피지 예외 처리):
       gap_down = (today_open - prev_close) / prev_close * 100
       if gap_down <= STOP_LOSS_PCT:  → 당일 시가(today_open)에 즉시 청산
       # 예: prev_close=$100, today_open=$85 → gap=-15% → -7% Stop-Loss 발동 전
       #     시가 $85에 시장가 청산. -15% 손실로 기록.
       # 이유: Stop-Loss -7%는 장 중 감시 기준. Gap Down은 이미 stop을 초과하여 출발.

[16:30 ET 장 마감 신호 계산 — 우선순위 순서로 검사]
  1. Stop-Loss:       pnl <= -7.0%                           → 즉시 SELL (필수)
  2. Trailing Stop:   최고점 대비 하락 <= -5.0% AND pnl > 0  → 즉시 SELL (익절)
  3. 컨센서스 반전:   bearish > bullish × 1.5               → SELL
  4. 30MA 하향 돌파:  종가 < 30MA AND 보유 5일↑              → SELL
  5. 수익 조건:       NEUTRAL + 순수익 > 1%                  → SELL
  → 1~5 모두 해당 없으면 → 보유 유지 (Top N에 없어도 무관)

[Stop-Loss / Trailing Stop / Gap Down 공통 로직]
  - highest_price: 보유 이후 매일 종가 최고가 추적
  - drawdown_from_high = (current_close - highest_price) / highest_price * 100
  - Gap Down: (today_open - prev_close) / prev_close * 100 <= STOP_LOSS_PCT → 시가 청산
  - Trailing Stop 발동: drawdown_from_high <= TRAILING_STOP_PCT AND 현재 수익 > 0%

[신규 매수]
  → 빈 슬롯 수 = MAX_POSITIONS - 현재 보유 수
  → 오늘 Top N 중 미보유 종목 순서대로 매수 (내일 09:35 ET 시가)
```

**왜 Top N 미포함이 청산 이유가 안 되는가:** WSB에서 하루 언급 없다고 펀더멘털이 바뀐 게 아님. Reddit 컨센서스 반전이나 30MA 붕괴가 실제 방향 전환 신호.

---

### 3.3 3가지 Position Sizing

모두 백테스팅에서 비교 실행. 실시간 모드에서는 config로 선택.

**방법 A: Equal Weighting**
```python
SLOT = total_cash / MAX_POSITIONS  # 기본 MAX_POSITIONS = 10
shares = floor(SLOT / open_price)
# → 항상 10% 고정. 슬롯 꽉 차면 신규 매수 없음
```

**방법 B: Sentiment-Weighted**
```python
ratio = bullish_count / (bullish_count + bearish_count)
if ratio >= 0.80:  pct = 0.15   # 강한 확신
elif ratio >= 0.65: pct = 0.10  # 보통 확신
else:              pct = 0.05   # 약한 확신
shares = floor(total_cash * pct / open_price)
# → 최소 5%, 최대 15%. 총합이 100% 초과 방지: 현금 잔여 확인 후 매수
```

**방법 C: Volatility-Weighted (ATR 기반)**
```python
atr = indicators.get_atr(ohlcv_df, period=14)
atr_pct = atr / prev_close              # 일평균 변동률
TARGET_RISK = 0.01                       # 포지션당 1% 리스크
raw_size = TARGET_RISK / atr_pct         # 변동성 역수 비례
size_pct = clamp(raw_size, 0.05, 0.15)  # 5~15% 범위 제한
shares = floor(total_cash * size_pct / open_price)
# 고변동(TSLA ATR 5%) → 작은 비중, 저변동(KO ATR 0.8%) → 큰 비중
```

---

### 3.4 날짜별 데이터 저장 구조 (Forward Testing → 추후 백테스팅 replay)

**핵심 원칙**: 매일 수집·계산된 Reddit 데이터를 날짜별 폴더에 저장한다.
데이터가 충분히 쌓이면 저장된 파일을 재생(replay)하여 백테스팅 가능.

```
data/reddit/
  YYYY-MM-DD/
    wsb_posts.json          ← 당일 3개 서브레딧 수집 게시글 (종목별 분류)
    wsb_signals.json        ← 당일 Top N 종목, 청산 신호, 신호 사유
    portfolio_state.json    ← 당일 가상 포트폴리오 상태 (포지션, 현금, P&L)
  ...
```

**파일별 구조:**

```json
// wsb_posts.json
{
  "date": "2026-04-17",
  "NVDA": [
    {"title": "...", "body_excerpt": "...", "top_comments": ["..."],
     "subreddit": "wallstreetbets", "bullish": true, "created_utc": 1234567890}
  ]
}

// wsb_signals.json
{
  "date": "2026-04-17",
  "model": "finbert", "ranking": "mentions", "sizing": "equal",
  "top_n": ["NVDA", "AMD", "PLTR"],
  "sell_signals": [{"symbol": "BBBY", "reason": "stop_loss", "pnl_pct": -7.2}]
}

// portfolio_state.json
{
  "date": "2026-04-17",
  "cash": 85000.0,
  "positions": {
    "NVDA": {"entry_date": "2026-04-10", "entry_price": 820.0,
             "shares": 12, "highest_price": 870.0}
  },
  "total_value": 95400.0
}
```

**추후 백테스팅 replay:**
```bash
# 저장된 데이터 기반 백테스팅 (데이터가 충분히 쌓인 후)
python main.py --backtest --source reddit --from 2026-04-17 --to 2026-05-17
# → data/reddit/YYYY-MM-DD/ 를 순서대로 읽어 거래 시뮬레이션
# → 실시간 API 호출 없이 재생 가능
```

---

## 4. 기능 요구사항

### FR-01~03: GPT-4 Provider (신규)

| ID | 요구사항 |
|----|----------|
| FR-01 | `sentiment_provider.py`에 `GPTProvider(SentimentProvider)` 구현. OpenAI gpt-4o |
| FR-02 | 배치 처리 (10건/호출). `data/gpt_cache.json` 날짜+텍스트 해시 캐시 |
| FR-03 | `get_provider("gpt4")` 분기 추가 |

### FR-04~07: Reddit 수집 (신규)

| ID | 요구사항 |
|----|----------|
| FR-04 | `reddit_collector.py` — PRAW **3개 서브레딧** (wsb/investing/stocks) 수집 |
| FR-05 | Flair 필터: DD, Discussion, Fundamentals, Daily Discussion, Earnings. Gain/Loss, Meme, YOLO 제외 |
| FR-06 | 티커 추출: `$TICKER` 패턴 + 회사명 키워드 (config.COMPANY_NAMES). Polygon OHLCV 조회 성공 = 유효 (Market Cap 필터 없음) |
| FR-07 | **날짜별 저장**: `data/reddit/YYYY-MM-DD/wsb_posts.json` (추후 백테스팅 replay용) |

### FR-08~11: Reddit 신호 엔진 (신규)

| ID | 요구사항 |
|----|----------|
| FR-08 | `wsb_signal_engine.py` — Consensus 필터 (1.5배), 30MA 필터, Ranking(mentions/ratio), Top N 선정 |
| FR-09 | 기존 보유 포지션 청산 조건 체크: **우선순위순** — Stop-Loss(-7%) → Trailing Stop(-5%) → 컨센서스 반전 → 30MA 하향 |
| FR-10 | **날짜별 저장**: `data/reddit/YYYY-MM-DD/wsb_signals.json` + `portfolio_state.json` |
| FR-11 | `TOP_N = 3` (config), `MAX_POSITIONS = 10` (config) |

### FR-12~13: Position Sizer (신규)

| ID | 요구사항 |
|----|----------|
| FR-12 | `position_sizer.py` — equal / sentiment / volatility 3가지 방법 구현 |
| FR-13 | `indicators.py`에 `get_atr(ohlcv_df, period=14)` 추가 (Volatility 방법용) |

### FR-14~17: 뉴스 3종 백테스팅 + Reddit Forward Testing

| ID | 요구사항 |
|----|----------|
| FR-14 | `backtester.py` — 뉴스 전용. `--model [textblob\|finbert\|gpt4]` 3종 비교 |
| FR-15 | Reddit Forward Testing: scheduler에 Reddit 신호 잡 추가 (매일 16:30 ET) |
| FR-16 | `data/reddit_portfolio_{model}_{ranking}_{sizing}.json` — 전략별 별도 가상 포트폴리오 |
| FR-17 | `--report-reddit` 플래그: 전략별 가상 포트폴리오 수익률 비교 출력 |

**뉴스 백테스팅 실행 (기존 + gpt4 추가):**
```bash
python main.py --backtest --model textblob
python main.py --backtest --model finbert
python main.py --backtest --model gpt4
```

**Reddit Forward Testing: 크론탭 설정**
```
# 매일 16:30 ET (미국 동부시간) Reddit 신호 계산 + 페이퍼 트레이딩
30 16 * * 1-5  python main.py --reddit-run-now
```

**백테스팅 캐시 (뉴스 전용):**
```
OHLCV:       data/backtest_cache.json     (기존)
GPT 결과:    data/gpt_cache.json          (신규, 뉴스+Reddit 공용)
```

### FR-18~20: 신규 안전장치 + 최적화

| ID | 요구사항 |
|----|----------|
| FR-18 | `wsb_signal_engine.py` — Stop-Loss(-7%), Trailing Stop(최고점 대비 -5%), Gap Down 즉시 청산(시가가 stop-loss 범위 초과 하락 시) |
| FR-19 | `reddit_collector.py` — Universe: Polygon OHLCV 성공 여부만. Market Cap/Short Interest 필터 없음 |
| FR-20 | `reddit_collector.py` — GPT-4 텍스트 전처리: 제목 + 본문 앞 300자 + Top 댓글 3개 |
| FR-21 | `backtester.py` — `--source reddit` 플래그: `data/reddit/YYYY-MM-DD/` 날짜 폴더 순서대로 읽어 백테스팅 replay |

### 비기능 요구사항

| ID | 요구사항 |
|----|----------|
| NFR-01 | `--source` 미지정 시 기존 동작(뉴스 기반) 유지. 하위 호환성 |
| NFR-02 | PRAW 수집 실패 시 중립(50.0) 폴백. 실시간 뉴스 신호에 영향 없음 |
| NFR-03 | 동일 날짜 재실행 시 캐시 HIT → API/모델 재호출 없음 |
| NFR-04 | Volatility sizing: ATR은 기존 OHLCV 재사용 (추가 API 없음) |
| NFR-05 | Reddit 모드 실시간 사용 시 `REDDIT_MODE = true` config로 활성화 |
| NFR-06 | Reddit 백테스팅: 실제 수집 가능 기간 자동 감지. 최소 14거래일 미만 시 경고 출력 |
| NFR-07 | 수수료 적용: 모든 백테스팅(뉴스 + Reddit)에 동일하게 적용. gross_pnl - commission |

---

## 5. 변경 대상 파일

| 파일 | 유형 | 주요 내용 |
|------|------|-----------|
| `reddit_collector.py` | **신규** | PRAW 수집, Flair/티커 필터, wsb_posts.json |
| `wsb_signal_engine.py` | **신규** | Consensus/30MA/Ranking 파이프라인, 보유 청산 체크 |
| `position_sizer.py` | **신규** | Equal / Sentiment / Volatility 3가지 방법 |
| `sentiment_provider.py` | 수정 | GPTProvider 추가, get_provider("gpt4") |
| `indicators.py` | 수정 | get_ma(30/90), get_atr(14) 추가 |
| `backtester.py` | 수정 | 뉴스 전용: --model gpt4 추가, 수수료 포함 P&L |
| `config.py` | 수정 | OPENAI_*, REDDIT_*, TOP_N, MAX_POSITIONS, REDDIT_MODE 등 |
| `main.py` | 수정 | --source/--ranking/--sizing 플래그 파싱 |

> **변경 없음**: signals.py, trader.py, portfolio.py, scheduler.py, market_filter.py

---

## 6. 새 config 상수

```python
# --- OpenAI ---
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
GPT_MODEL = "gpt-4o"
GPT_BATCH_SIZE = 10
GPT_CACHE_FILE = "data/gpt_cache.json"

# --- Reddit ---
REDDIT_CLIENT_ID: str = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET: str = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = "trading-bot/1.0"
# v7: 3개 서브레딧 통합 수집
REDDIT_SUBREDDITS = ["wallstreetbets", "investing", "stocks"]
REDDIT_ALLOWED_FLAIRS = ["DD", "Discussion", "Fundamentals", "Daily Discussion", "Earnings"]
REDDIT_LOOKBACK_HOURS = 24
WSB_POSTS_FILE = "data/wsb_posts.json"
WSB_SIGNALS_FILE = "data/wsb_signals.json"
WSB_BACKTEST_CACHE_FILE = "data/wsb_backtest_cache.json"
REDDIT_BACKTEST_MIN_DAYS = 14   # 최소 2주 데이터 있어야 백테스팅 실행

# --- Reddit 신호 파라미터 ---
WSB_CONSENSUS_RATIO = 1.5       # bullish/bearish 진입 기준
WSB_SELL_RATIO = 1.5            # bearish/bullish 청산 기준
TOP_N = 3                       # 매일 선정 최대 종목 수
MAX_POSITIONS = 10              # 최대 동시 보유 포지션 수
MA_ENTRY_PERIOD = 30            # 진입 30MA 기준
MA_BREAKDOWN_GRACE_DAYS = 5     # 30MA 청산 유예 기간

# --- 손절매 / 익절 ---
STOP_LOSS_PCT = -7.0            # 손절: 진입가 대비 -7% → 즉시 SELL
TRAILING_STOP_PCT = -5.0        # 트레일링 익절: 최고점 대비 -5% (수익 중일 때만)

# --- Reddit 날짜별 저장 경로 ---
REDDIT_DATA_DIR = "data/reddit"                    # data/reddit/YYYY-MM-DD/ 루트
# 날짜별 파일: wsb_posts.json / wsb_signals.json / portfolio_state.json

# --- GPT-4 텍스트 최적화 ---
GPT_POST_TITLE_MAX = 200        # 제목 최대 200자
GPT_POST_BODY_MAX = 300         # 본문 최대 300자
GPT_TOP_COMMENTS = 3            # Top 댓글 수
GPT_COMMENT_MAX = 100           # 댓글당 최대 100자

# --- Position Sizing ---
POSITION_SIZING = "equal"       # "equal" | "sentiment" | "volatility"
EQUAL_POSITION_PCT = 0.10       # Equal: 10% 고정
SENTIMENT_SIZE_HIGH = 0.15      # Sentiment: 비율 >= 80%
SENTIMENT_SIZE_MID = 0.10       # Sentiment: 비율 >= 65%
SENTIMENT_SIZE_LOW = 0.05       # Sentiment: 나머지
VOLATILITY_TARGET_RISK = 0.01   # Volatility: 포지션당 1% 리스크
VOLATILITY_MIN_PCT = 0.05       # Volatility: 최소 5%
VOLATILITY_MAX_PCT = 0.15       # Volatility: 최대 15%
ATR_PERIOD = 14

# --- 백테스팅 ---
BACKTEST_START = "2026-02-01"
BACKTEST_END   = "2026-04-01"
REDDIT_MODE = False             # True: Reddit 실시간 모드 활성화

# --- 수수료 (한국투자증권 미국주식 위탁매매 기준) ---
COMMISSION_RATE = 0.0025        # 0.25% (매수/매도 각각)
COMMISSION_MIN_USD = 2.0        # 최소 수수료 $2.0 per leg
# net_pnl = gross_pnl - max(buy_value×0.25%, $2) - max(sell_value×0.25%, $2)
```

---

## 7. 백테스팅 출력 예시

```
=== 백테스팅 결과 (2026-02-01 ~ 2026-04-01) ===

[뉴스 기반 — Finnhub]
  TextBlob           | 수익률: +5.1% | 거래: 10회 | 승률: 60% | MDD: -2.8%
  FinBERT            | 수익률: +8.3% | 거래: 12회 | 승률: 67% | MDD: -3.1%
  GPT-4o             | 수익률: +9.1% | 거래: 11회 | 승률: 73% | MDD: -2.2%

[Reddit 기반 — WSB DD/Discussion]
  FinBERT/mentions/equal      | 수익률: +?.?% | 거래: ?회 | 승률: ?% | MDD: ?.?%
  FinBERT/mentions/sentiment  | 수익률: +?.?% | ...
  FinBERT/mentions/volatility | 수익률: +?.?% | ...
  FinBERT/ratio/equal         | 수익률: +?.?% | ...
  FinBERT/ratio/sentiment     | 수익률: +?.?% | ...
  FinBERT/ratio/volatility    | 수익률: +?.?% | ...
  GPT-4o/mentions/equal       | 수익률: +?.?% | ...
  GPT-4o/mentions/sentiment   | 수익률: +?.?% | ...
  GPT-4o/mentions/volatility  | 수익률: +?.?% | ...
  GPT-4o/ratio/equal          | 수익률: +?.?% | ...
  GPT-4o/ratio/sentiment      | 수익률: +?.?% | ...
  GPT-4o/ratio/volatility     | 수익률: +?.?% | ...

★ 최우수 전략: [자동 선정]
```

---

## 8. 성공 기준

| SC | 기준 |
|----|------|
| SC-01 | GPTProvider — gpt-4o 감성 분석 0~100 점수 반환, gpt_cache.json 생성 |
| SC-02 | reddit_collector — wsb_posts.json 생성, DD/Discussion만 포함, 티커별 분류 |
| SC-03 | wsb_signal_engine — Consensus 1.5배 필터 + 30MA 필터 + Top N 선정 동작 |
| SC-04 | 보유 포지션 Reddit 청산 — 컨센서스 반전 또는 30MA 하향 돌파 시 SELL 신호 |
| SC-05 | position_sizer — Equal/Sentiment/Volatility 각각 다른 주수 계산 결과 |
| SC-06 | 뉴스 3종 백테스팅 각각 수익률 출력 |
| SC-07 | Reddit 12종 백테스팅 각각 수익률 출력 |
| SC-08 | 동일 날짜 재실행 시 캐시 HIT 로그 출력 |
| SC-09 | `--source` 미지정 시 기존 뉴스 방식 동작 동일 유지 |
| SC-10 | 수수료 포함 P&L: 매수/매도 각각 `max(거래금액×0.25%, $2)` 차감 후 수익률 출력 |
| SC-11 | Reddit 수집: 3개 서브레딧(wsb/investing/stocks) 통합, 서브레딧별 게시글 수 로그 출력 |
| SC-12 | Forward Testing: 매일 `data/reddit/YYYY-MM-DD/` 3개 파일 생성 확인 |
| SC-13 | Stop-Loss: pnl <= -7.0% 시 `[Stop-Loss]` 로그 출력. Gap Down 시 `[Gap Down] {symbol}: 시가 {open} 즉시 청산` 로그 출력 |
| SC-14 | Trailing Stop: 최고점 대비 -5% 하락 + 수익 중 시 `[Trailing Stop]` 로그 출력 |
| SC-15 | Universe: Polygon OHLCV 조회 실패 종목 `[Universe] {symbol} 제외: OHLCV 조회 실패` 로그 출력 |
| SC-16 | GPT 텍스트 최적화: 게시글당 입력 토큰 ≤ 300토큰 (제목+본문300자+댓글3개) |
| SC-17 | Reddit 백테스팅 replay: `--source reddit --from YYYY-MM-DD --to YYYY-MM-DD` 로 저장 데이터 재생 가능 |

---

## 9. 리스크

| 리스크 | 영향도 | 대응 |
|--------|--------|------|
| 모든 WSB 언급 종목 OHLCV 조회 부하 | 중 | Polygon 조회 실패 시 스킵. 캐시로 재호출 방지. Market Cap API 없음으로 단순화 |
| GPT-4 토큰 비용 (3개 서브레딧 수백 게시글) | 높음 | **텍스트 잘라내기**: 제목+본문300자+댓글3개 → 게시글당 ~200토큰. 배치(10건) + gpt_cache.json |
| Reddit 밈주식 급락 (하루이틀에 반토막) | 높음 | **Stop-Loss -7% + Trailing Stop -5% 즉시 포함 (M6)** — 선택 아닌 필수 |
| Reddit Forward Testing 결과 대기 기간 | 중 | 2-4주 후 결과 수집. 그동안 뉴스 백테스팅 결과로 1차 검증 진행 |
| FinBERT 처리 시간 (종목당 수십 건) | 중 | wsb_posts.json 캐시로 재실행 시 단축 |
| WSB 노이즈 (초대형주 AAPL/MSFT 언급 많음) | 중 | Polygon OHLCV 조회 성공만으로 필터. 초대형주도 포함될 수 있으나 Reddit 컨센서스 1.5배 조건이 2차 필터 역할 |
| Gap Down 슬리피지 (오버나이트 악재 갭 하락) | 중 | **시가 즉시 청산 로직** — 전일 종가 대비 시가 하락폭 > STOP_LOSS_PCT 이면 Stop-Loss 발동 전 시가에 청산 |

---

## 10. 구현 순서 (Module Map)

| Module | 파일 | 작업 |
|--------|------|------|
| M1 | `config.py` | 전체 신규 상수 추가 (Stop-Loss, Trailing Stop, GPT truncation 포함) |
| M2 | `sentiment_provider.py` | GPTProvider 구현 (텍스트 잘라내기 포함) |
| M3 | `indicators.py` | get_ma(30), get_atr(14) 추가 |
| M4 | `position_sizer.py` | Equal / Sentiment / Volatility 구현 |
| M5 | `reddit_collector.py` | PRAW 3서브레딧 수집, OHLCV 유효성 검사, GPT용 텍스트 전처리, 날짜별 wsb_posts.json 저장 |
| M6 | `wsb_signal_engine.py` | Reddit 파이프라인 + Stop-Loss + Trailing Stop + 날짜별 portfolio_state.json 저장 |
| M7 | `backtester.py` | 뉴스 전용(`--model`) + Reddit replay(`--source reddit --from --to`) |
| M8 | `main.py` | --model gpt4, --reddit-run-now, --report-reddit, --source reddit --from --to 플래그 |

---

## 11. 다음 버전 확장 포인트

| 확장 | 방법 |
|------|------|
| 한국투자증권 실거래 연동 | broker_adapter.py + KISAdapter |
| Reddit 실시간 신호 (scheduler 통합) | REDDIT_MODE=True + 16:30 ET Reddit 스캔 잡 추가 |
| Stop-loss / Trailing-stop | risk_manager.py (별도 피처) |
| 최우수 전략 자동 선정 | --backtest --auto-select 플래그 |
