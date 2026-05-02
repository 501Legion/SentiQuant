# auto_stock — Gap Analysis (ARCHITECTURE.md as Design)

> **Date**: 2026-05-02
> **Design Source**: `ARCHITECTURE.md` (v2026-04-22, branch `rsi_finBERT_combine`)
> **Implementation Scope**: 핵심 11 + 백테스팅 4 + 실거래/스케줄러 3 = 18개 모듈
> **Note**: 정식 `auto_stock.plan.md` / `auto_stock.design.md` 부재. 사용자 동의하에 Living Document인 ARCHITECTURE.md를 임시 Design으로 간주.

## Executive Summary

| 차원 | Match Rate | 평가 |
|------|-----------|------|
| Structural | **100%** | 18개 명시 모듈 모두 존재, 진입점도 모두 정의됨 |
| Functional | **96%** | §3 7단계 + §5 V3 5단계 청산 + Velocity 5종 모두 충실 구현, TODO 0건 |
| Contract | **88%** | config 상수 21/21 일치. CLI `--ranking` 어휘 불일치 1건 |
| **Overall (정적 only)** | **94%** | `0.2·100 + 0.4·96 + 0.4·88 = 93.6 → 94%` |

ARCHITECTURE.md를 정식 Design으로 간주했을 때 구현은 매우 성숙. Critical 결함은 1건뿐 (CLI `--ranking sentiment` 옵션 불일치 — 실제는 `ratio`).

---

## 1. Structural Match (100%)

### 1.1 핵심 모듈 (11/11)

| ARCHITECTURE.md §2 | 파일 존재 | 명시 진입점 정의 | 위치 |
|------|:--:|:--:|------|
| `main.py :: main()` | ✅ | ✅ | main.py:175 |
| `config.py` (52개+ 상수) | ✅ | — | config.py:1-201 (실제 70+ 상수) |
| `signals.py :: generate_signals_for_all()` | ✅ | ✅ | signals.py:117 |
| `sentiment_provider.py :: get_provider(name)` | ✅ | ✅ | sentiment_provider.py:380 |
| `wsb_preprocessor.py :: WSBPreprocessor.preprocess()` | ✅ | ⚠️ | 클래스 L6, 실제 메서드명 `preprocess_post()` |
| `collector.py :: get_ohlcv(), get_news()` | ✅ | ✅ | collector.py:27, 160 |
| `reddit_collector.py :: collect_wsb_posts()` | ✅ | ⚠️ | 실제 `RedditCollector.collect()` (L95) |
| `market_filter.py :: apply_market_filter()` | ✅ | ✅ | market_filter.py:51 |
| `indicators.py :: get_latest_rsi/get_ma/calculate_atr` | ✅ | ✅ | indicators.py:185, 122, 175 |
| `position_sizer.py :: get_sizer(name)` | ✅ | ✅ | position_sizer.py:126 |
| `wsb_state.py :: load_mention_history/load_position_scores` | ✅ | ✅ | wsb_state.py:16, 64 |

### 1.2 백테스팅/실거래 (7/7)

| 파일 | 존재 | 핵심 클래스/함수 |
|------|:--:|------|
| `backtester.py` | ✅ | `BacktestEngine` (L54), `run_all_models` (L295) |
| `reddit_backtester.py` | ✅ | `RedditReplayBacktester` (L19), `run_all_reddit_strategies` (L244) |
| `wsb_signal_engine.py` | ✅ | `WSBSignalEngine.run_pipeline` (L37), `check_exit` (L322) |
| `reddit_portfolio.py` | ✅ | `RedditPortfolio` (L29), `Position` (L20) |
| `portfolio.py` | ✅ | `Portfolio`, `apply_buy/apply_sell` |
| `trader.py` | ✅ | `process_orders` (L15) |
| `scheduler.py` | ✅ | `start_scheduler` (L120), `signal_calculation_job` (L42) |

### 1.3 Drift

- ARCHITECTURE.md §2/§5: Reddit **3 서브레딧**(wallstreetbets/stocks/investing) 명시 → 실제 `config.REDDIT_SUBREDDITS`는 **6개** (`wallstreetbets, investing, stocks, options, StockMarket, thetagang`, config.py:26-29). → **Important drift**
- ARCHITECTURE.md §8 미반영 PDCA: `wsb-daily-comments` (plan + analysis), `kis-paper-trading` (신규 plan, untracked)

---

## 2. Functional Depth (96%)

### 2.1 §3 신호 결정 파이프라인 7단계 (signals.py:117-234)

| 단계 | 위치 | 상태 |
|------|------|------|
| 1. OHLCV 수집 | signals.py:149 | ✅ |
| 2. RSI 계산 | signals.py:150 | ✅ |
| 3. 뉴스 수집 | signals.py:163 | ✅ |
| 4. Provider별 감성 평균 | signals.py:167-178 | ✅ |
| 5. determine_signal | signals.py:181 → L20-54 | ✅ 5단계 룰 정확 일치 |
| 6. Volume Spike override | signals.py:184-191 → L68 | ✅ |
| 7. Market Filter | signals.py:194 → market_filter.py:51 | ✅ |

### 2.2 §4 감성 Provider 5종

| 모델 | 클래스 | 위치 | 비고 |
|------|--------|------|------|
| `textblob` | `TextBlobProvider` | sentiment_provider.py:38 | ✅ |
| `finbert` | `FinBERTProvider(use_wsb=False)` | L84, 397 | ✅ |
| `finbert-wsb` | `FinBERTProvider(use_wsb=True)` | L399-400 | ✅ |
| `gpt4` | `GPTProvider` (sha256 캐시 + 배치10) | L241, 401 | ✅ |
| `combined` | `signals._get_active_providers` 평균 | signals.py:57-65, 178 | ⚠️ Reddit Backtest 차단 (main.py:81) |

**Neutral 필터**: §4 표현 "positive_ratio >= 0.80" 모호. 실제 구현은 `neutral_score >= 0.80`인 기사를 제외 (sentiment_provider.py:184). 의미적으로 동등하나 **문서 모호성** (confidence 90%).

### 2.3 §5 Reddit V3 파이프라인 (wsb_signal_engine.py)

| 단계 | 위치 | 상태 |
|------|------|------|
| `_score_posts` (bullish/bearish/neutral count) | L130-170 | ✅ |
| `_apply_neutral_filter` (>0.70 → NEUTRAL) | L172-195 | ✅ `WSB_NEUTRAL_RATIO_MAX` |
| `_apply_velocity` (5종 state) | L197-227 | ✅ HIGH/NORMAL/DECLINING/NEW_SPIKE/NEW_IGNORE |
| `_determine_signal_v3` Velocity 보정 | L229-273 | ✅ NORMAL>70/55, HIGH ±5, DECLINING +5, NEW_SPIKE 65/50 |
| `_filter_consensus` (≥1.5배) | L275-296 | ✅ + bearish=0 가드 |
| `_rank` | L298-320 | ⚠️ `ranking="ratio"` (§6 명세 "sentiment"와 어휘 차) |

**check_exit 5단계 우선순위** (wsb_signal_engine.py:322-428): 모두 정확 구현. TODO/FIXME/XXX 주석 0건.

| # | 명세 | 위치 |
|---|------|------|
| 1. sentiment_reversal | L370-381 | ✅ `0.60`, `yesterday_below` flag |
| 2. rsi_overbought + HIGH 1회 유예 | L384-400 | ✅ `rsi_held` flag |
| 3. gap_down ≤-5% | L402-410 | ✅ |
| 4. stop_loss pnl ≤-7% | L412-418 | ✅ |
| 5. trailing_stop drawdown ≤-5% & pnl>0 | L420-426 | ✅ |

---

## 3. Contract Match (88%)

### 3.1 §7 config 상수 검증

뉴스/공통 상수 10개 (`RSI_OVERSOLD=30, RSI_OVERBOUGHT=70, SENTIMENT_BUY=50, SENTIMENT_STRONG_BUY=70, MA_ENTRY_PERIOD=30, COMMISSION_RATE=0.0025, COMMISSION_MIN_USD=2.0, VOLUME_SPIKE_MULTIPLIER=2.0, NEWS_MAX_ARTICLES=100, NEUTRAL_FILTER_MIN_ARTICLES=10`) 및 WSB V3 상수 11개 (`WSB_STRONG_BUY_SCORE=70, WSB_BUY_SCORE=55, WSB_NEUTRAL_RATIO_MAX=0.70, WSB_VELOCITY_LOOKBACK_DAYS=7, WSB_VELOCITY_HIGH_THRESHOLD=2.0, WSB_VELOCITY_LOW_THRESHOLD=0.5, WSB_VELOCITY_SCORE_ADJUST=5.0, WSB_NEW_SPIKE_MIN_MENTIONS=20, WSB_SENTIMENT_REVERSAL_RATIO=0.60, WSB_RSI_EXIT_OVERBOUGHT=70.0, WSB_GAP_DOWN_PCT=-5.0`) — **21/21 완전 일치** (config.py:115-190).

### 3.2 §6 CLI 옵션 (main.py argparse)

| ARCHITECTURE.md §6 | 실제 main.py | 상태 |
|------|------|------|
| `--backtest` | L197 | ✅ |
| `--source reddit` | L208 | ✅ |
| `--model [textblob|finbert|finbert-wsb|gpt4|combined]` | L201-206 | ✅ |
| `--ranking [mentions|sentiment]` | L214 (`choices=["mentions","ratio"]`) | ❌ **불일치** |
| `--sizing [equal|sentiment|volatility]` | L220 | ✅ |
| `--from`, `--to` | L224, L230 | ✅ |
| `--run-now` | L182 | ✅ |
| `--reddit-run-now` | L237 | ✅ |

---

## 4. Gap List (Severity 정렬)

| ID | Severity | Category | Description | Evidence | Suggested Fix |
|----|----------|----------|-------------|----------|---------------|
| G1 | **Critical** | Contract / CLI | `--ranking` 명세("sentiment") ≠ 실제("ratio"). ARCHITECTURE.md §6 예제 그대로 호출 시 argparse 거부. confidence 100% | ARCHITECTURE.md:163 vs main.py:214 | ARCHITECTURE.md를 `ratio`로 수정하거나 argparse `choices`에 `sentiment` 별칭 추가 |
| G2 | **Important** | Structural drift | Reddit subreddit 명세(3) ≠ 실제(6). `options/StockMarket/thetagang` 추가됨 | ARCHITECTURE.md:21,117 vs config.py:26-29 | §2/§5 다이어그램·텍스트 갱신 |
| G3 | **Important** | Documentation 모호성 | §4 "FinBERT Neutral 필터: positive_ratio >= 0.80" 표현 모호. 실제 구현은 `neutral_score >= 0.80` 기사 제외. confidence 90% | ARCHITECTURE.md:110 vs sentiment_provider.py:184 | "기사별 neutral_score ≥ 0.80인 기사 제외"로 명확화 |
| G4 | **Important** | Doc/Status drift | §8 `wsb-signal-v3` archive 경로 표기됐으나 plan/design은 `01-plan`/`02-design`에 잔존, archive 폴더 없음 | ARCHITECTURE.md:219 vs Glob 결과 | `/pdca archive wsb-signal-v3` 실행 |
| G5 | **Important** | Documentation drift | wsb-daily-comments PDCA(plan + analysis 2건) §8 누락 | docs/01-plan/features/wsb-daily-comments.plan.md 등 | §8에 행 추가 |
| G6 | Minor | Naming | §2 `wsb_preprocessor.preprocess()` → 실제 `preprocess_post()` | ARCHITECTURE.md:45 vs sentiment_provider.py:145 | §2 진입점명 정정 |
| G7 | Minor | Naming | §2 `reddit_collector.collect_wsb_posts()` → 실제 `RedditCollector.collect()` | ARCHITECTURE.md:47 | §2 정정 |
| G8 | Minor | Functional | `combined` Provider Reddit Backtest 차단(main.py:81). 의도된 설계라면 §4에 명시 권장 | main.py:81 | §4 `combined` 행에 "뉴스 모드 전용" 주석 |
| G9 | Minor | Test coverage | `tests/` 디렉토리 또는 `test_*.py` 파일 부재 — 회귀 검증 자동화 불가 | Glob 결과 | 핵심 모듈에 unit test 도입 |

---

## 5. Runtime Verification Plan

### L1 — Unit Test 후보 (pytest 권장)

| # | 대상 | 시나리오 | 기대 |
|---|------|---------|------|
| 1 | `signals.determine_signal` | (rsi=25, sent=75) | "STRONG_BUY" |
| 2 | 〃 | (rsi=75, sent=25) | "STRONG_SELL" |
| 3 | 〃 | (rsi=45, sent=55) | "NEUTRAL" |
| 4 | 〃 | (rsi=35, sent=60) | "BUY" |
| 5 | `market_filter.apply_market_filter` | ("STRONG_BUY", mkt_rsi=80) | "BUY" 다운그레이드 |
| 6 | 〃 | ("BUY", mkt_rsi=25) | "NEUTRAL" |
| 7 | `_determine_signal_v3` | (score=72, rsi=25, "NORMAL") | "STRONG_BUY" |
| 8 | 〃 | (score=72, rsi=25, "DECLINING") | "NEUTRAL" |
| 9 | 〃 | (score=66, rsi=25, "HIGH_MOMENTUM") | "STRONG_BUY" |
| 10 | `_apply_neutral_filter` | bullish=2,bearish=2,neutral=10 | NEUTRAL 강제 |
| 11 | `_apply_velocity` | 신규 mentions=25 | "NEW_SPIKE" |
| 12 | 〃 | 신규 mentions=10 | "NEW_IGNORE" |
| 13 | `check_exit` | gap=-6%, rsi=50, pnl=-2% | (True, "gap_down") |

### L2 — 통합 시나리오 (cached fixture / dry-run)

1. `python main.py --backtest --model finbert` → BacktestResult 생성
2. `python main.py --backtest --source reddit --model finbert --ranking mentions --sizing equal --from 2026-04-17 --to 2026-04-22` → 12전략 replay
3. `python main.py --run-now` → `data/signals.json` 생성

### L3 — E2E (외부 API mock 권장)

1. 스케줄러 → 신호생성 → 주문처리 전체 흐름 (collector mock)
2. Reddit Forward Testing 1일 사이클 (PRAW MockReddit)
3. check_exit 5단계 우선순위 회귀 테스트 (OHLCV fixture)

---

## 6. Recommended Next Action

**Overall 94% ≥ 90% — 동기화 양호.** 코드 측 결함은 사실상 없고, 모든 gap은 **문서 측(ARCHITECTURE.md) drift**입니다.

### 단기 권장 (문서 sync)

1. **G1 (Critical)** — ARCHITECTURE.md §6의 `--ranking` 옵션을 `[mentions|ratio]`로 수정
2. **G2 (Important)** — §2/§5 Reddit subreddit 6개로 갱신
3. **G3 (Important)** — §4 Neutral 필터 문구 명확화
4. **G4 (Important)** — `wsb-signal-v3` archive 정리 또는 §8 갱신
5. **G5 (Important)** — `wsb-daily-comments` §8 행 추가

### 중장기 권장

6. **G9** — `tests/` 디렉토리 신설, L1 단위 테스트 13건 도입
7. auto_stock 정식 PDCA 문서 부재 — Living Document 정책 유지하되, 큰 리팩토링 시 `/pdca plan auto_stock` 가이드 적용

### Flow

- `/pdca iterate` 불필요 (94% — 코드 결함 없음)
- ARCHITECTURE.md sync 후 `/pdca report` 형식 협의 (정식 plan/design 부재로 일반 report 템플릿 부적합. ARCHITECTURE.md + CHANGELOG가 더 적합할 수 있음)
