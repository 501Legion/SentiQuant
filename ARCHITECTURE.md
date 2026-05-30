# System Architecture — auto_stock

> 이 문서는 현재 시스템의 전체 구조를 한눈에 파악하기 위한 Living Document입니다.
> 기능 추가/변경 시 반드시 업데이트하세요.
>
> **마지막 업데이트**: 2026-05-17
> **브랜치**: rsi_finBERT_combine

---

## 1. 시스템 개요

뉴스 감성 분석(TextBlob/FinBERT/GPT-5.4 Mini)과 Reddit 군중심리를 결합한 미국주 페이퍼 트레이딩 시스템.
RSI + 감성 점수 → 매매 신호 → 포지션 관리 → 백테스팅/포워드 테스팅.

```
[뉴스 파이프라인]                     [Reddit 파이프라인]
collector.get_news()                  reddit_collector.py (6 subreddits)
    ↓                                     ↓
sentiment_provider.py                 wsb_signal_engine.py  [V3]
(TextBlob / FinBERT / GPT-5.4 Mini)   (Velocity 보정 → 중립필터 → TopN)
    ↓                                     ↓
signals.generate_signals_for_all()    reddit_portfolio.py
(SIGNAL_ENGINE 디스패처               (포지션 추적 / Stop-Loss / Trailing)
 → RSI + Sentiment → Signal)
    ↓
market_filter.apply_market_filter()
(QQQ RSI → 시장 과열 시 다운그레이드)
    ↓
portfolio.py / trader.py
(포지션 관리 / 주문 실행 → kis_broker 위임)
    ↓
kis_broker.py
(KIS OpenAPI 모의투자 — OAuth / 주문 / 계좌 / 시세)
```

> **신호 엔진 추상화** (kis-paper-trading): `signals.generate_signals_for_all()`은
> `config.SIGNAL_ENGINE` 값으로 Provider를 선택하는 디스패처다. 기본값 `"finbert"`는
> `signal_provider.FinbertProvider` → `signals._generate_signals_finbert()`로 위임하며
> 기존 동작과 100% 동일하다. 디스패처는 신호 생성 전 `_filter_tradable_symbols()`로
> KIS 매매 가능 종목과 교집합을 취한다.
>
> **주문 실행 위임** (kis-paper-trading): `trader.process_orders()`는 자체 시뮬레이션
> 대신 `Broker` Protocol(`kis_broker.KISBroker`)의 `place_order()`에 위임한다.

---

## 2. 파일별 역할

### 핵심 모듈

| 파일 | 역할 | 주요 진입점 |
|------|------|------------|
| `main.py` | CLI 진입점, 모든 실행 모드 라우팅 | `main()` |
| `config.py` | 전체 상수 정의 (52개+) | — |
| `signals.py` | SIGNAL_ENGINE 디스패처 + 신호 결정 5단계 파이프라인 + KIS 매매가능 필터 | `generate_signals_for_all()`, `_generate_signals_finbert()` |
| `signal_provider.py` | `SignalProvider` Protocol + `SIGNAL_ENGINE` Provider 디스패처 | `get_provider(name)` |
| `kis_broker.py` | KIS OpenAPI 모의투자 브로커 어댑터 (OAuth 24h / 주문 / 계좌 / 시세 / 매매가능 종목) | `KISBroker`, `place_order()`, `get_account()` |
| `sentiment_provider.py` | TextBlob/FinBERT/GPT-5.4 Mini Provider ABC | `get_provider(name)` |
| `wsb_preprocessor.py` | WSB 슬랭/이모지/반어법 → FinBERT 친화적 변환 | `WSBPreprocessor.preprocess()` |
| `collector.py` | OHLCV(Polygon) + 뉴스(Finnhub) 수집 | `get_ohlcv()`, `get_news()` |
| `reddit_collector.py` | Reddit PRAW 6서브레딧 수집 + Daily Thread | `RedditCollector.collect()` |
| `market_filter.py` | QQQ RSI 기반 시장 상태 필터 | `apply_market_filter()` |
| `indicators.py` | RSI, MA, ATR, VolumeMA20 계산 | `get_latest_rsi()`, `get_ma()`, `calculate_atr()` |
| `position_sizer.py` | Equal/Sentiment/Volatility/**OpinionTrend(9-factor)** 사이징 ABC | `get_sizer(name)` |
| `wsb_state.py` | mention_history / position_scores / score_history JSON + **daily_opinion_snapshots.jsonl** I/O | `load_position_scores()`, `append_daily_snapshot()` |

### Community Opinion Agent 모듈 (community-opinion-agent)

| 파일 | 역할 | 주요 진입점 |
|------|------|------------|
| `universe_filter.py` | 거래 universe 판정 (CORE/EXPANDED/COMMUNITY_LIQUID/BLOCKED) — 정적 index JSON + OHLCV 유동성 | `UniverseFilter.decide()` |
| `cost_aware_trade_filter.py` | 왕복비용 vs 기대 edge 게이팅 (SKIP/DOWNSIZE/ENTER) | `CostAwareTradeFilter.evaluate()` |
| `community_memory.py` | 과거 snapshot/reflection 저장·유사검색 (`MemoryBackend` ABC → Jsonl/InMemory) | `CommunityMemoryStore` |
| `opinion_reflection.py` | Low/HighLevelReflection (의견→가격, 매매 entry/exit 분석) | `build_low_level()`, `build_high_level()` |
| `decision_router.py` | rule-based DecisionRouter + 선택적 LLMRouter (8 안전장치, 기본 OFF) | `DecisionRouter.decide()` |
| `data/universe/{sp500,nasdaq100}.json` | 정적 index 멤버십 시드 (편집 가능) | — |
| `scripts/regression_check_reddit.py` | equal 회귀 검출 (필터 OFF 강제 → diff 시 exit 1) | — |

### 백테스팅/포워드 테스팅

| 파일 | 역할 |
|------|------|
| `backtester.py` | 뉴스 모델 백테스팅 (TextBlob/FinBERT/GPT-5.4 Mini) — **불가침** |
| `reddit_backtester.py` | Reddit Replay 백테스팅 + **에이전트 오케스트레이션**(snapshot→universe→cost→memory→router 게이팅, opinion_trend 전용) + gross/net·비용·skip·router metric |
| `wsb_signal_engine.py` | Reddit 신호 생성 V3 + **DailyOpinionSnapshot 생성**(`build_daily_snapshot`) |
| `reddit_portfolio.py` | Reddit 포지션 관리 (Gap Down -5% / Stop-Loss -7% / Trailing Stop) + 진입 의견 스냅샷 저장 |

### 실거래/스케줄러

| 파일 | 역할 |
|------|------|
| `portfolio.py` | 뉴스 모델 포지션 관리 + `sync_from_kis()` KIS 잔고 동기화 |
| `trader.py` | 주문 실행 — `Broker` Protocol(`kis_broker`)에 `place_order` 위임 (`--dry-run` 지원) |
| `scheduler.py` | 크론탭 연동 스케줄러 + 신호 계산 전 KIS 매매가능 종목 갱신 |

---

## 3. 신호 결정 파이프라인 (뉴스 모델)

```python
# signals.generate_signals_for_all() = SIGNAL_ENGINE 디스패처
0a. 매매가능 필터      _filter_tradable_symbols() — config.SYMBOLS ∩ kis_symbols.json
0b. Provider 선택      signal_provider.get_provider(config.SIGNAL_ENGINE)
                       "finbert"(기본) → _generate_signals_finbert() 위임

# signals._generate_signals_finbert() 내부 흐름 (7단계 본체)
1. OHLCV 수집          collector.get_ohlcv(symbol)
2. RSI 계산             indicators.get_latest_rsi()
3. 뉴스 수집            collector.get_news(symbol)
4. 감성 분석            provider.score(articles) — 활성 Provider 평균
5. 신호 결정            determine_signal(rsi, sentiment)
6. Volume Spike 예외    _check_volume_spike() → BUY 오버라이드
7. Market Filter        market_filter.apply_market_filter(signal, mkt_rsi)
```

**신호 결정 규칙** (`determine_signal`):
| 신호 | 조건 |
|------|------|
| STRONG_BUY | sentiment > 70 AND rsi < 30 |
| STRONG_SELL | sentiment < 30 AND rsi > 70 |
| BUY | sentiment > 50 AND 30 ≤ rsi < 50 |
| SELL | sentiment < 50 AND rsi > 70 |
| NEUTRAL | 40 ≤ sentiment ≤ 60 AND 40 ≤ rsi ≤ 60 |

**Market Filter** (`market_filter.py`):
- QQQ RSI > 70 → BUY/STRONG_BUY → NEUTRAL (과열)
- QQQ RSI < 30 → SELL/STRONG_SELL → NEUTRAL (패닉 과매도)

---

## 4. 감성 분석 모델

| 모델 | Provider 클래스 | 특징 |
|------|----------------|------|
| `textblob` | `TextBlobProvider` | 빠름, 금융 도메인 정확도 낮음 |
| `finbert` | `FinBERTProvider` | Bloomberg/Reuters 학습, ONNX 캐시 (~3초) |
| `finbert-wsb` | `FinBERTProvider(use_wsb_preprocessor=True)` | WSB 슬랭/이모지 전처리 후 FinBERT |
| `gpt5` | `GPTProvider` | 실제 호출 모델: gpt-5.4-mini, 배치10건, sha256 캐시, 비용 발생 |
| `combined` | 평균 | config.SENTIMENT_PROVIDERS에 정의된 모델 평균 |

**FinBERT Neutral 필터**: 기사별 `neutral_score >= 0.80`인 기사를 분석 대상에서 제외 (강한 중립 노이즈 제거). 유효 기사가 `NEUTRAL_FILTER_MIN_ARTICLES`(기본 10) 미만이면 폴백으로 avg(positive - negative) 사용

---

## 5. Reddit 파이프라인

```
reddit_collector.collect_wsb_posts()
    → wallstreetbets / investing / stocks / options / StockMarket / thetagang (PRAW, 6개)
    → Daily Thread 댓글 (_fetch_daily_thread)
    → Polygon 티커 검증 (캐시 활용)
    ↓
wsb_signal_engine.run_pipeline()  [V3 — wsb-signal-v3]
    → _score_posts()          bullish/bearish/neutral 카운트
    → _apply_neutral_filter() neutral/total > 0.70 → NEUTRAL 강제 (노이즈 제거)
    → _apply_velocity()       7일 멘션 이력 → velocity_state
                              HIGH_MOMENTUM(×2↑) / NORMAL / DECLINING(×0.5↓)
                              NEW_SPIKE(첫등장 ≥20언급) / NEW_IGNORE(<20언급)
    → _determine_signal_v3()  Velocity 보정 매트릭스 → STRONG_BUY/BUY/NEUTRAL
                              NORMAL: STRONG_BUY>70, BUY>55
                              HIGH_MOMENTUM: 임계값 -5 완화
                              DECLINING: 임계값 +5 강화
    → _filter_consensus()     bullish/bearish ≥ 1.5배
    → 랭킹 → TopN
    → wsb_state.save_mention_history()  (7일 FIFO)
    ↓
check_exit() V3 — 5단계 우선순위:
    1. sentiment_reversal   2일 연속 점수 < entry_score × 0.60
    2. rsi_overbought       RSI > 70 (HIGH_MOMENTUM: rsi_held 1회 유예)
    3. gap_down             open/prev_close ≤ -5% (WSB_GAP_DOWN_PCT)
    4. stop_loss            pnl ≤ -7% (STOP_LOSS_PCT)
    5. trailing_stop        pnl > 0% AND drawdown ≤ -5%
    ↓
reddit_portfolio.py
    → Equal / Sentiment / Volatility / OpinionTrend 사이징
    → Gap Down 즉시 청산 (전일 종가 대비 -5%)
    → Stop-Loss -7%, Trailing Stop -5%
    → 매수 시 entry_score → wsb_state.upsert_position_score()
    → 수수료 0.25% 양방 공제
```

### 5.1 Community Opinion Agent 게이팅 (opinion_trend 전용, community-opinion-agent)

> WSB V3 신호(BUY/STRONG_BUY)를 **1차 후보**로 유지하고, 매수 직전에 에이전트 게이팅을 적용.
> `--sizing equal` 등 기존 경로는 게이팅 미적용 → **회귀 0** (regression_check_reddit.py로 검증).

```
reddit_backtester.run()  [opinion_trend일 때만 _agent_gate]
  후보(top_n)별:
    build_daily_snapshot()      weighted bull/bear·consensus·neutral·trend·persistence
                                + summary(사람용) / query_*(검색용) 분리, top_keywords
    UniverseFilter.decide()     tier 판정 → mode별 allowed + size_multiplier
                                (sp500_only ⊂ liquid_us ⊂ community_liquid)
    CostAwareTradeFilter.evaluate()  round_trip(0.7%) vs edge(ATR/변동성/conviction)
                                edge < cost×2 → SKIP / 경계 → DOWNSIZE
    CommunityMemoryStore.retrieve_*()  run-local 유사 과거 사례 (결정성 보장)
    DecisionRouter.decide()     rule-based(+선택 LLM) → BUY/HOLD/SELL/REDUCE/SKIP/EXIT
                                8 안전장치(neutral/consensus/ambiguity/universe/cost/cash/
                                no-position/rule-SKIP은 LLM이 BUY로 못 뒤집음)
    action==BUY만 매수, 사이징은 9-factor OpinionTrendSizer
                                (universe_size_multiplier·cost_risk_factor·source_quality 반영)
  청산: 기존 5단계 유지 + opinion_reversal(neutral 급증/consensus 붕괴/score 역전/trend↓/bearish 급증)
  종료 후: snapshot+reflection을 jsonl(영속) + run-local(검색)에 저장
```

**핵심 원칙**: 급등추격 금지(size_factor ≤ 1.3, NEW_SPIKE 축소) · LLM은 보조 라우터(자율매매 ❌, 기본 OFF) · `signals.py`/`backtester.py`/뉴스 경로 불가침.

---

## 6. CLI 주요 명령어

```bash
# 뉴스 모델 백테스팅
python main.py --backtest --model [textblob|finbert|finbert-wsb|gpt5|combined]

# Reddit 백테스팅
python main.py --backtest --source reddit \
  --model [finbert|finbert-wsb|gpt5] \
  --ranking [mentions|ratio|sentiment] \
  --sizing [equal|sentiment|volatility|opinion_trend] \
  --universe [sp500_only|nasdaq100_only|sp500_nasdaq100|liquid_us|community_liquid|custom_watchlist] \
  [--llm-router] \
  --from YYYY-MM-DD --to YYYY-MM-DD

# Community Opinion Agent — universe 모드 비교 (sizing/ranking 고정, --universe만 변경)
python main.py --backtest --source reddit --model finbert-wsb --ranking sentiment \
  --sizing opinion_trend --universe community_liquid --from … --to …
python main.py --backtest --source reddit --model finbert-wsb --ranking sentiment \
  --sizing equal --universe community_liquid --from … --to …   # equal 회귀 baseline
python scripts/regression_check_reddit.py --from … --to … [--update]   # equal 회귀 검출

# 실시간 신호 생성
python main.py --run-now

# Reddit Forward Testing (스케줄러)
python main.py --reddit-run-now

# KIS 모의투자 주문 처리 (kis-paper-trading)
python main.py --order-now              # 신호 기반 KIS 모의투자 실주문 처리
python main.py --order-now --dry-run    # KIS 주문 직전까지 시뮬레이션 (실주문 없음)
python main.py --run-now --source kis   # KIS 잔고 동기화 후 신호 생성
```

> `--source`는 `[news|reddit|kis]`, `--ranking`은 `[mentions|ratio|sentiment]`,
> `--sizing`은 `[equal|sentiment|volatility|opinion_trend]`, `--universe`는 6모드(기본 `community_liquid`),
> `--llm-router`는 store_true(기본 OFF) (argparse 실제 값 기준).

---

## 7. 주요 상수 (config.py)

| 상수 | 기본값 | 설명 |
|------|--------|------|
| `RSI_OVERSOLD` | 30 | RSI 과매도 기준 |
| `RSI_OVERBOUGHT` | 70 | RSI 과매수 기준 |
| `SENTIMENT_BUY` | 50 | BUY 신호 감성 하한 |
| `SENTIMENT_STRONG_BUY` | 70 | STRONG_BUY 감성 하한 |
| `MA_ENTRY_PERIOD` | 30 | 진입 MA 기간 |
| `COMMISSION_RATE` | 0.0025 | 수수료율 (0.25%) |
| `COMMISSION_MIN_USD` | 2.0 | 최소 수수료 |
| `VOLUME_SPIKE_MULTIPLIER` | 2.0 | 거래량 급증 배수 |
| `NEWS_MAX_ARTICLES` | 100 | Finnhub 기사 수집 상한 |
| `NEUTRAL_FILTER_MIN_ARTICLES` | 10 | FinBERT 유효 기사 최소 수 |

**WSB V3 상수** (wsb-signal-v3):

| 상수 | 기본값 | 설명 |
|------|--------|------|
| `WSB_STRONG_BUY_SCORE` | 70.0 | NORMAL STRONG_BUY 기준 |
| `WSB_BUY_SCORE` | 55.0 | NORMAL BUY 기준 (구 50→55 강화) |
| `WSB_NEUTRAL_RATIO_MAX` | 0.70 | 중립 비율 상한 (초과 시 NEUTRAL 강제) |
| `WSB_VELOCITY_LOOKBACK_DAYS` | 7 | Velocity 계산 이력 일수 |
| `WSB_VELOCITY_HIGH_THRESHOLD` | 2.0 | HIGH_MOMENTUM 판정 배수 |
| `WSB_VELOCITY_LOW_THRESHOLD` | 0.5 | DECLINING 판정 배수 |
| `WSB_VELOCITY_SCORE_ADJUST` | 5.0 | 임계값 보정 폭 (±5) |
| `WSB_NEW_SPIKE_MIN_MENTIONS` | 20 | NEW_SPIKE 최소 언급 수 |
| `WSB_SENTIMENT_REVERSAL_RATIO` | 0.60 | 감성 역전 기준 (entry_score × 0.60) |
| `WSB_RSI_EXIT_OVERBOUGHT` | 70.0 | RSI 과매수 청산 기준 |
| `WSB_GAP_DOWN_PCT` | -5.0 | Gap Down 청산 기준 (%) |

**Community Opinion Agent 상수** (community-opinion-agent, `COMMUNITY_*`):

| 상수 | 기본값 | 설명 |
|------|--------|------|
| `COMMUNITY_UNIVERSE_MODE` | `"community_liquid"` | 거래 universe 모드 (6종) |
| `COMMUNITY_ENABLE_UNIVERSE_FILTER` | `True` | OFF → 무조건 allowed (회귀 0) |
| `COMMUNITY_ENABLE_COST_AWARE_FILTER` | `True` | OFF → 무조건 allowed (회귀 0) |
| `COMMUNITY_MIN_PRICE_USD` / `MIN_AVG_DOLLAR_VOLUME` | 5.0 / 20M | penny·저유동 차단 |
| `COMMUNITY_NON_INDEX_SIZE_MULTIPLIER` | 0.5 | 인덱스 외 종목 사이즈 배수 |
| `COMMUNITY_MIN_EDGE_TO_COST_MULTIPLIER` | 2.0 | edge < cost×2 → SKIP |
| `COMMUNITY_CONSENSUS_MIN_RATIO` / `NEUTRAL_RATIO_MAX` | 1.5 / 0.70 | 합의/노이즈 게이팅 |
| `COMMUNITY_FLAIR_WEIGHT_*` | DD 1.5 … low 0.0 | 글 품질 가중 |
| `COMMUNITY_TICKER_AMBIGUITY_BLACKLIST` | ALL/IT/NOW… | `$` 없으면 제외 |
| `COMMUNITY_SIZE_FACTOR_MIN/MAX` | 0.0 / 1.3 | 사이징 clamp (= `WSB_OPINION_*` alias) |
| `COMMUNITY_MEMORY_ENABLED` / `REFLECTION_ENABLED` | `True` | memory/reflection 토글 |
| `COMMUNITY_LLM_ROUTER_ENABLED` | `False` | LLM 라우터 (기본 OFF) |
| `COMMUNITY_LLM_ROUTER_MODEL` | `"gpt4"`→`GPT_MODEL`(gpt-5.4-mini) | 실호출 모델 매핑 |

> 의견 파라미터(`COMMUNITY_OPINION_*`)는 기존 `WSB_OPINION_*` 값을 alias (단일 소스, 회귀 보호).

**KIS / Signal Engine 상수** (kis-paper-trading):

| 상수 | 기본값 | 설명 |
|------|--------|------|
| `SIGNAL_ENGINE` | `"finbert"` | 신호 엔진 선택 (`finbert` \| `gpt5`). `gpt5`는 `NotImplementedError` |
| `KIS_APP_KEY` / `KIS_APP_SECRET` | `""` (env) | KIS OpenAPI 인증 키 |
| `KIS_ACCOUNT_NO` | `""` (env) | 계좌번호 `"12345678-01"` 형식 |
| `KIS_PAPER_TRADING` | `true` | 모의투자 강제 플래그 — `false` 시 `connect()` 차단 (FR-20) |
| `KIS_BASE_URL_PAPER` | `openapivts…:29443` | 모의투자 API 도메인 |
| `KIS_BASE_URL_REAL` | `openapi…:9443` | 실전 도메인 — FR-20으로 차단 |
| `KIS_TOKEN_CACHE_FILE` | `data/kis_token.json` | OAuth 토큰 24h 캐시 |
| `KIS_SYMBOLS_FILE` | `data/kis_symbols.json` | 매매 가능 종목 캐시 |
| `KIS_SYMBOLS_REFRESH_DAYS` | 7 | 종목 마스터 갱신 주기 (일) |

---

## 8. 기능 이력 (완료된 PDCA)

| 기능 | 완료일 | 핵심 변경 | 아카이브 |
|------|--------|-----------|---------|
| `news-rsi-trading` | 2026-03 | 기반 시스템 구축 | `docs/archive/2026-04/news-rsi-trading/` |
| `polygon-massive-migration` | 2026-04-01 | Polygon SDK 마이그레이션 | `docs/archive/2026-04/polygon-massive-migration/` |
| `market-filter-finbert` | 2026-04-02 | QQQ Market Filter + FinBERT | `docs/archive/2026-04/market-filter-finbert/` |
| `signal-v2` | 2026-04-05 | FinBERT neutral 필터 + Volume Spike + 백테스팅 | `docs/archive/2026-04/signal-v2/` |
| `reddit-gpt-5.4-mini-quant` | 2026-04-17 | Reddit 파이프라인 + GPT-5.4 Mini + 12전략 | `docs/archive/2026-04/` |
| `daily-thread-collector` | 2026-04-18 | Daily Thread 댓글 수집 | `docs/archive/2026-04/daily-thread-collector/` |
| `wsb-finbert-preprocessor` | 2026-04-18 | WSB 전처리 + finbert-wsb 옵션 | `docs/archive/2026-04/wsb-finbert-preprocessor/` |
| `wsb-signal-v3` | 2026-04-22 | 30MA 제거 + Velocity 보정 매트릭스 + 5단계 청산 | `docs/archive/2026-04/wsb-signal-v3/` |
| `wsb-daily-comments` | 2026-05 | Daily Thread 댓글 수집 보강 | `docs/archive/2026-05/wsb-daily-comments/` |
| `kis-paper-trading` | 2026-05-16 | KIS OpenAPI 모의투자 연동 (`kis_broker.py`·`signal_provider.py` 신규) + SIGNAL_ENGINE 추상화 + 매매가능 종목 필터 | `docs/01-plan/`·`docs/02-design/`·`docs/03-analysis/`·`docs/04-report/` (미아카이브) |
| `community-opinion-trend-sizing` | 2026-05-29 | OpinionTrendSizer(7-factor) + opinion_reversal 청산 + ranking sentiment + score_history | `docs/0*/features/community-opinion-trend-sizing.*` (미아카이브) |
| `community-opinion-agent` | 2026-05-30 | universe/cost 필터 + source quality/ticker ambiguity + DailyOpinionSnapshot + community memory/reflection + DecisionRouter(rule+LLM OFF) + gross/net·skip metric. 신규 5모듈. **equal 회귀 0**, Match Rate 98% | `docs/0*/features/community-opinion-agent.*` (미아카이브) |

---

## 9. 변경 시 가이드

### 새 기능 추가 시
1. `/pdca plan {feature}` → `/pdca design` → `/pdca do` → `/pdca analyze` → `/pdca report`
2. 완료 후 이 문서 **§2, §3, §4, §5** 해당 섹션 업데이트
3. **§8 기능 이력**에 한 줄 추가
4. `/pdca archive {feature}`로 PDCA 문서 정리

### 기존 기능 변경 시
- **설정값(config.py)만 바꾸는 경우**: ARCHITECTURE.md §7 업데이트만
- **로직 변경 (signals.py, market_filter.py 등)**: §3~5 해당 섹션 업데이트
- **새 모델/Provider 추가**: §4 테이블에 행 추가
- **대규모 리팩토링**: PDCA 사이클 돌리고 §8에 기록
