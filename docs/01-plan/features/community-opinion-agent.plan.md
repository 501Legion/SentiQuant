# Plan: Community Opinion Agent — 커뮤니티 여론 트렌드 기반 의사결정 에이전트 (v0~v3)

**Feature**: community-opinion-agent
**Date**: 2026-05-29
**Status**: Plan (plan-plus enhanced)
**Branch**: `community-opinion-trend-sizing`
**Base**: 기존 `community-opinion-trend-sizing`(design 단계, opinion_trend sizing/청산 일부 구현 완료)의 상위 확장
**Method**: `/pdca plan` + `/plan-plus` (Intent → Alternatives → YAGNI → Incremental Validation, 사용자 승인 완료)

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 기존 `community-opinion-trend-sizing`은 ① S&P500/유동성·비용을 고려하지 않아 저유동·고비용 종목까지 거래해 왕복 수수료가 수익을 갉아먹고, ② Reddit 글 품질(flair)·티커 오탐(ALL/IT/NOW 등 일반어)을 사이징에 반영하지 못하며, ③ 과거 유사 사례(memory)와 사후 학습(reflection)이 없어 같은 실수를 반복하고, ④ 모든 판단이 단일 rule 경로라 "왜 이 거래를 했는지"의 구조화된 근거가 남지 않는다. |
| **Solution** | WSB V3 rule-based 신호를 **1차 후보**로 유지하고, 그 위에 ⓪ `UniverseFilter`+`CostAwareTradeFilter`(거래 가능성·비용 대비 edge 게이팅), v1 `source quality`/`ticker ambiguity` 필터 + 정식 `DailyOpinionSnapshot`, v2 `CommunityMemoryStore`(jsonl) + `Low/HighLevelReflection`, v3 `DecisionRouter`(rule-based 기본, 선택적 LLM router)를 얹는다. **LLM은 자율 매매자가 아니라 도구 결과를 해석하고 승인/축소/보류/거절하는 보조 라우터/explainer**다. |
| **Function/UX Effect** | `python main.py --backtest --source reddit --model finbert-wsb --ranking sentiment --sizing opinion_trend --universe community_liquid --from … --to …` 실행 시 universe_mode별 **gross/net return 동시 출력**, 수수료·슬리피지·turnover·cost_to_gross_profit_ratio, 라우터 action 분포·평균 confidence·memory hit, universe/cost/ambiguity 별 skip 카운트가 비교표로 출력된다. `--llm-router` 명시 시에만 LLM 보정. `--sizing equal` + 신규 필터 OFF → 회귀 0. |
| **Core Value** | "유동성 있고 비용 대비 기대 움직임이 충분하며(universe+cost), 품질 높은 글에서 합의가 며칠 지속되고(source+persistence), 과거 유사 사례가 성공적이었던(memory+reflection)" 종목에만 비중을 싣고, 그 판단 근거를 구조화해 남기는 **여론 기반 의사결정 에이전트 인프라**. 급등추격이 아니라 의견 지속성·합의도·비용 효율에 베팅하고 학습한다. |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 커뮤니티 여론 전략의 실전 성과는 *어느 종목을 거래 가능 대상으로 볼지(universe)*, *비용 대비 기대 움직임이 충분한지(cost)*, *글 품질·티커 오탐을 걸러냈는지(source/ambiguity)*, *과거 유사 사례에서 배웠는지(memory/reflection)*에 크게 좌우된다. 현재는 이 차원들이 없어 좋은 여론도 비용·노이즈에 묻힌다. NEW_SPIKE은 강한 매수가 아니라 지속성·합의도·과거 성공 여부로 재평가해야 한다. |
| **WHO** | 미국주식 페이퍼 트레이딩 시스템 운영자(본인). 1차 = 전략 연구자(universe/cost/memory 가설을 백테스트로 검증, gross vs net 비교), 2차 = 운영자(검증된 sizing/router/exit를 KIS 모의투자로 전사). |
| **RISK** | (1) `check_exit()`·`process_day()`는 모든 sizing 모드 공유 → 신규 필터/라우터가 **equal 회귀를 깨뜨릴 위험 ★최상위** → 모든 신규 기능을 config flag + `opinion_mode`/router_mode로 격리, `scripts/regression_check_reddit.py`로 자동 검출. (2) universe 멤버십·시가총액 데이터 부재 → 정적 JSON 리스트 + OHLCV 유동성으로 확보(시총은 선택). (3) 백테스트 저장 데이터(`wsb_posts.json`)에 flair 없음 → source_quality_weight는 forward 수집 시 부여, 백테스트는 fallback weight. (4) `backtester.py`의 `TradeRecord`/`BacktestResult` 불가침 → reddit 전용 dataclass 확장. (5) LLM이 BUY 금지조건을 뒤집는 위험 → 8개 하드 안전장치 + confidence·schema 게이팅. (6) factor 수치는 임의 초기값 → grid search 별도. |
| **SUCCESS** | (1) `--universe {sp500_only,sp500_nasdaq100,community_liquid,liquid_us,nasdaq100_only,custom_watchlist}` 동작 (2) gross/net return 동시 출력 + 비용 metric (3) source quality/ambiguity 필터가 weight·skip 통계 생성 (4) DailyOpinionSnapshot jsonl 저장 (5) Memory 저장/유사검색 + Low/High Reflection 생성 (6) DecisionRouter rule-based 동작 + LLM 안전장치 (7) **모든 신규 필터 OFF + `--sizing equal` 회귀 0** (8) 신규 테스트 7종 통과. |
| **SCOPE** | 신규: `universe_filter.py`, `cost_aware_trade_filter.py`, `community_memory.py`, `opinion_reflection.py`, `decision_router.py`, `scripts/regression_check_reddit.py`, `data/universe/{sp500,nasdaq100}.json`, 테스트 7종. 수정: `config.py`(COMMUNITY_*), `reddit_collector.py`(flair/ambiguity), `wsb_signal_engine.py`(DailyOpinionSnapshot), `wsb_state.py`(snapshot jsonl), `position_sizer.py`(sizer factor 확장), `reddit_portfolio.py`(router/memory 배선), `reddit_backtester.py`(universe/cost metric·reflection), `main.py`(`--universe`/`--llm-router`). **Out of Scope**: `signals.py`·`backtester.py`·뉴스 모델/뉴스 포트폴리오 불가침, 네이버 종토방 수집기, vector DB(Chroma/Faiss) 실연동, factor grid search, KIS 실주문 적용, profit target(기본 OFF 유지). |

---

## A. User Intent Discovery (plan-plus Phase 1)

> 사용자 상세 스펙에서 의도가 명확 → 추가 질문 없이 정리.

- **핵심 문제 (Q1)**: 여론 전략의 실전 성과를 좌우하는 4차원(거래 대상 universe / 비용 대비 edge / 글 품질·티커 오탐 / 과거 사례 학습)이 현재 시스템에 없어, 좋은 여론도 비용·노이즈에 묻히고 같은 실수를 반복하며 판단 근거가 남지 않는다. NEW_SPIKE은 강한 매수가 아니라 지속성·합의도·과거 성공 여부로 재평가.
- **1차 사용자 (Q2)**: 전략 연구자(universe/cost/memory 가설을 백테스트로 검증, gross vs net 비교) → 2차 운영자(검증된 sizing/router/exit를 KIS 모의투자로 전사).
- **성공 기준 (Q3)**: `--universe` 6모드 동작 + gross/net 동시 출력 + 필터/메모리/라우터 동작 + LLM 8 안전장치 + **모든 신규 필터 OFF + `--sizing equal` 회귀 0** + 신규 테스트 7종 통과.
- **제약 (Q4)**: 급등추격 금지·NEW_SPIKE 보수·LLM은 자율매매자 아닌 보조 라우터·5단계 청산 순서 유지·profit target 기본 OFF·`signals.py`/`backtester.py`/뉴스 경로 불가침.

## B. Alternatives Explored (plan-plus Phase 2)

> 신규 구성요소(universe/cost 필터·memory·reflection·router)를 기존 파이프라인과 묶는 3안 비교 → **A 선택**.

| 접근 | 메커니즘 | Pros | Cons |
|------|----------|------|------|
| **A: backtester 오케스트레이션 + 독립 모듈 (선택)** | 신규 5모듈은 순수 함수/클래스로 분리, reddit_backtester가 호출해 process_day에 주입 | 기존 trend-sizing 패턴과 동일, **equal 회귀 안전**, blast radius 최소 | reddit_backtester 비대 |
| B: 전용 community_agent.py 오케스트레이터 | universe→cost→memory→router 파이프라인을 신규 오케스트레이터가 소유, backtester·portfolio 공용 | 관심사 분리 깔끔, 라이브 전사 용이 | 파일·배선 추가, 현 범위엔 과설계 가능성 |
| C: portfolio.process_day 내장 | 파이프라인을 reddit_portfolio 내부에 직접 구현 | 신규 모듈 최소 | portfolio 비대·equal 경로 오염으로 **회귀 위험 최상위**, 재사용 어려움 |

**A 선택 근거**: equal 회귀 보호(최상위 제약)와 기존 trend-sizing 패턴 정합. 모듈은 독립 파일로 분리해 단위테스트·향후 라이브/오케스트레이터(B) 리팩터링 여지 확보. **게이팅 위치 = 후보 선정 후 매수 직전(process_day)** — WSB V3 신호 엔진 불변 + skip 통계 정확 집계.

## C. YAGNI Review (plan-plus Phase 3)

> 범위(v0~v3)·핵심 결정 확정 상태에서 과설계 위험 항목만 검증 → **4개 선택 항목 전부 1차 포함**.

**v1 포함 (코어)**: UniverseFilter+CostAwareTradeFilter, source quality·ticker ambiguity 필터, DailyOpinionSnapshot 정식화, Sizer +3 factor, CommunityMemoryStore(jsonl), Low/HighLevelReflection, DecisionRouter(rule-based), LLM router(인터페이스+스키마+안전장치+fallback, 기본 OFF), gross/net 비용 metric, regression_check, 테스트 7종.

**선택 항목 (사용자 multiSelect — 전부 ✅ 포함)**:
- ✅ turnover/holding/cooldown soft 규칙 + 백테스트 metric (FR-00.6)
- ✅ market_cap(JSON) 기반 시총 게이팅 (FR-00.1, 파일에 있으면 사용)
- ✅ community_memory vector-DB 교체용 MemoryBackend 추상 인터페이스 (FR-2.1)
- ✅ snapshot top_reasons/top_keywords 자동 추출 (FR-1.3)

**Out of Scope (연기)**: 네이버 종토방 수집기, vector DB(Chroma/Faiss) 실연동, factor grid search, KIS 실주문 적용, profit target 활성화, Polygon 실시간 시총/index 조회.

## 1. 핵심 결정 (Checkpoint 1·2 + 4개 질문 확정)

| # | 결정 | 내용 |
|---|------|------|
| D1 | **Universe 데이터 소스** | 정적 JSON 리스트(`data/universe/sp500.json`, `nasdaq100.json`) + 이미 prefetch된 OHLCV로 평균 거래대금·가격 계산. **시가총액은 선택**(파일에 있으면 사용, 없으면 유동성으로 대체). Polygon 실시간 조회 미사용 → 백테스트 결정성·무료 플랜 안전. `custom_watchlist`는 `config.COMMUNITY_CUSTOM_WATCHLIST` 사용. |
| D2 | **config 네임스페이스** | 스펙대로 `COMMUNITY_*` 신규 추가. 단 기존 `WSB_OPINION_*`와 값이 겹치는 의견 파라미터는 **기존 상수를 참조하는 alias**(`COMMUNITY_OPINION_SCORE_HIGH = WSB_OPINION_SCORE_HIGH`)로 두어 단일 소스 유지. universe/cost/flair/ambiguity/memory/router 등 진짜 신규만 독립 값. |
| D3 | **LLM Router 구현 깊이** | 인터페이스 + strict JSON 스키마 검증 + 8개 하드 안전장치 + rule-based fallback **완전 구현**. 실제 LLM 호출은 프로젝트 기존 OpenAI(`config.GPT_MODEL`=gpt-5.4-mini, `COMMUNITY_LLM_ROUTER_MODEL`은 alias)를 재사용. **기본 OFF**, `--llm-router` 플래그 + `COMMUNITY_LLM_ROUTER_ENABLED`로만 활성. API 키 없거나 호출 실패 시 자동 rule-based fallback. |
| D4 | **설계 범위** | v0+v1+v2+v3 **단일 통합 설계**, 구현은 Step 0~11 순서. design 문서의 `11.3 Session Guide`로 모듈별 세션 분할 → `/pdca do --scope module-N` 점진 구현. |
| D5 | **OpinionTrendSizer 확장 방식** | 기존 `CommunityOpinionTrendSizer`(position_sizer.py:126)를 **in-place 확장**. 공식에 `source_quality_factor`, `universe_size_multiplier`, `cost_risk_factor` 3개 곱 추가. **데이터 미제공 시 각 factor=1.0** → 기존 테스트 T1~T12 결과 불변(회귀 0). |
| D6 | **DailyOpinionSnapshot 정식화** | 기존 `OpinionMetrics`(reddit_backtester.py:33)를 흡수·확장한 `DailyOpinionSnapshot` dataclass를 `wsb_signal_engine.py`에 정식 정의. summary(사람용)와 query_*(검색용) 분리(FinAgent식). 기존 `OpinionMetrics` 소비처(Sizer duck-typing)는 호환 유지. |
| D7 | **회귀 격리 원칙** | 신규 필터 전부 config flag 기본값으로 켜지되, **`--sizing equal` 경로에서는 Sizer가 opinion kwargs를 무시**하고 router도 우회. `scripts/regression_check_reddit.py`가 "모든 신규 필터 OFF + equal" baseline과 비교해 trade/equity/total_trades 차이 시 exit 1. |
| D8 | **NEW_SPIKE 보수 평가** | NEW_SPIKE 단독(persistence 부족)은 attention_factor 0.5 + 라우터 BUY 축소. 강한 매수는 *지속성·합의도·낮은 노이즈·과거 유사 성공*이 동반될 때만. (급등추격 금지 원칙) |

---

## 2. 현재 코드 자산 (재사용/확장 기반)

| 자산 | 위치 | 본 피처에서의 처리 |
|------|------|--------------------|
| `CommunityOpinionTrendSizer` (7-factor) | position_sizer.py:126 | **확장** (+3 factor, D5) |
| `WSB_OPINION_*` 상수 | config.py:201-235 | `COMMUNITY_*` alias로 재사용 (D2) |
| `OpinionMetrics` dataclass | reddit_backtester.py:33 | `DailyOpinionSnapshot`로 확장 (D6) |
| `opinion_reversal` 청산 | wsb_signal_engine.py:435 | 유지 + router REDUCE/SELL/EXIT 연동 |
| score_history + trend/persistence/consensus helper | wsb_state.py:156-244 | 재사용 + snapshot jsonl I/O 추가 |
| 진입 스냅샷(`upsert_position_score`) | wsb_state.py:94 | 필드 확장(universe_tier/tradeability/summary/query/router) |
| `RedditTradeRecord`/`RedditBacktestResult` | reddit_backtester.py:47/76 | 비용·router·universe 필드 확장 |
| `_validate_polygon` 티커 검증 | reddit_collector.py:375 | 유지 + ambiguity 필터 선행 |
| 수수료 `_calc_commission` | reddit_portfolio.py:289 | 재사용(net pnl 이미 수수료 반영) |

---

## 3. 기능 요구사항

### ⓪ Universe Filter + Cost-aware Trade Filter

| ID | 요구사항 |
|----|----------|
| FR-00.1 | `universe_filter.py` 신규: `UniverseFilter` + `UniverseDecision`. `decide(symbol, *, ohlcv=None, price=None, market_cap=None, ambiguity_risk=False) -> UniverseDecision`. `COMMUNITY_UNIVERSE_MODE`에 따라 거래 가능성 판단. 정적 index 리스트(D1) + OHLCV 평균 거래대금·가격으로 tier 산정. |
| FR-00.2 | universe_tier: **CORE**(S&P500∪Nasdaq100), **EXPANDED**(인덱스 외 대형/중형·유동성 충분·시총 조건 통과), **COMMUNITY_LIQUID**(인덱스 외지만 유동성 조건 통과, `COMMUNITY_ALLOW_NON_INDEX_IF_LIQUID`), **BLOCKED**(저유동/OTC/penny/ambiguity/비용부족). mode별 allowed 판정: `sp500_only`→CORE∩S&P500만, `community_liquid`→CORE∪EXPANDED∪COMMUNITY_LIQUID 등. |
| FR-00.3 | `size_multiplier`: CORE=1.0, EXPANDED/COMMUNITY_LIQUID=`COMMUNITY_NON_INDEX_SIZE_MULTIPLIER`(0.5). `liquidity_score`(평균 거래대금 정규화), `tradeability_score`(가격·유동성·ambiguity 종합) 0~1. `reason_codes` 리스트(`INDEX_CORE`,`LOW_DOLLAR_VOLUME`,`PENNY_STOCK`,`OTC`,`TICKER_AMBIGUOUS`,`NON_INDEX_LIQUID` 등). |
| FR-00.4 | `cost_aware_trade_filter.py` 신규: `CostAwareTradeFilter` + `CostAwareTradeDecision`. `round_trip_cost_pct = COMMISSION_RATE*2 + COMMUNITY_ESTIMATED_SLIPPAGE_PCT + COMMUNITY_ESTIMATED_SPREAD_PCT`. `expected_edge_proxy`는 우선순위: ① ATR pct ② 최근 평균 변동폭 ③ opinion conviction 기반 expected_move_proxy. |
| FR-00.5 | `expected_edge_proxy < round_trip_cost_pct * COMMUNITY_MIN_EDGE_TO_COST_MULTIPLIER(2.0)` → 신규 진입 SKIP(또는 size 축소). `ATR pct < COMMUNITY_MIN_ATR_PCT_FOR_TRADE` → SKIP. `CostAwareTradeDecision{allowed, reason_codes, round_trip_cost_pct, expected_edge_proxy, edge_to_cost_ratio, cost_risk_factor, recommended_action}`. |
| FR-00.6 | turnover/holding/cooldown soft 규칙: `COMMUNITY_MAX_TURNOVER_PER_DAY`, `COMMUNITY_MIN_HOLDING_DAYS_SOFT`, `COMMUNITY_COOLDOWN_DAYS_AFTER_EXIT`. 백테스트 metric으로 측정·라우터 입력. |
| FR-00.7 | 두 필터 모두 `COMMUNITY_ENABLE_UNIVERSE_FILTER`/`COMMUNITY_ENABLE_COST_AWARE_FILTER`로 토글. **OFF면 allowed=True·size_multiplier=1.0·cost_risk_factor=1.0** → 회귀 0. |

### v1. Source Quality + Ticker Ambiguity + DailyOpinionSnapshot + Sizer 확장

| ID | 요구사항 |
|----|----------|
| FR-1.1 | `reddit_collector.py`: post/comment 수집 시 **flair·source(title/body/comment) 보존**. 각 post에 `source_quality_weight` 계산해 부착(`COMMUNITY_FLAIR_WEIGHT_*`). low quality flair → weight 0(또는 제외). Daily Thread 댓글은 제외 대신 `COMMUNITY_FLAIR_WEIGHT_DAILY_THREAD`(0.5). **`source_quality_weight` 없는 기존 데이터 fallback**(=1.0). `COMMUNITY_ENABLE_SOURCE_QUALITY_FILTER` 토글. |
| FR-1.2 | ticker ambiguity 필터: Polygon 검증 유지 + `COMMUNITY_TICKER_AMBIGUITY_BLACKLIST`(ALL/IT/NOW/ARE 등) 종목은 `$ALL` dollar prefix나 명확한 context 있을 때만 인정. `COMMUNITY_SINGLE_LETTER_TICKER_REQUIRE_DOLLAR`→단일문자 티커는 `$F`만 인정. title mention > body mention weight(`COMMUNITY_TITLE/BODY/COMMENT_MENTION_WEIGHT`). 제외 건은 로그+통계(`trades_skipped_by_ambiguity`). `COMMUNITY_ENABLE_TICKER_AMBIGUITY_FILTER` 토글. |
| FR-1.3 | `wsb_signal_engine.py`: `DailyOpinionSnapshot` dataclass 정식 정의(§5 schema). bullish/bearish/neutral·weighted_*·source_quality_score·consensus_ratio·neutral_ratio·opinion_score·velocity_state·opinion_trend·persistence_days·attention_state·universe_tier·tradeability_score·is_consensus_buy/sell·top_reasons·top_keywords·summary·query_*. |
| FR-1.4 | daily buy consensus: `weighted_bullish ≥ weighted_bearish × COMMUNITY_CONSENSUS_MIN_RATIO(1.5)` AND `neutral_ratio ≤ COMMUNITY_NEUTRAL_RATIO_MAX(0.70)` AND `total_mentions ≥ COMMUNITY_MIN_DAILY_MENTIONS(3)`. consensus_ratio = `weighted_bullish/max(weighted_bearish,1)`. |
| FR-1.5 | `wsb_state.py`: `data/community/daily_opinion_snapshots.jsonl` append/read(line=snapshot 1개). 기존 mention_history/position_scores/score_history 구조 불변. `COMMUNITY_ENABLE_DAILY_OPINION_SNAPSHOT` 토글. |
| FR-1.6 | `position_sizer.py`: `CommunityOpinionTrendSizer` 공식에 `source_quality_factor × universe_size_multiplier × cost_risk_factor` 추가(D5). opinion에 해당 속성 없으면 1.0. clamp `[COMMUNITY_SIZE_FACTOR_MIN, COMMUNITY_SIZE_FACTOR_MAX]`(0.0~1.3). cost-aware SKIP이면 0. **기존 T1~T12 회귀 0.** |

### v2. Community Memory + Low/High-level Reflection

| ID | 요구사항 |
|----|----------|
| FR-2.1 | `community_memory.py`: `CommunityMemoryStore`(jsonl backend, interface 분리해 향후 Chroma/Faiss 교체 가능). `add_opinion_snapshot/add_low_level_reflection/add_high_level_reflection`, `retrieve_similar_opinions/low_level/high_level(symbol, query, top_k=COMMUNITY_MEMORY_TOP_K)`. 저장 경로 `data/community/memory/{opinion_snapshots,low_level_reflections,high_level_reflections}.jsonl`. |
| FR-2.2 | retrieve scoring(초기 휴리스틱): 같은 symbol·같은 universe_tier 우선 + opinion_score/consensus_ratio/neutral_ratio/persistence_days 유사도 + velocity_state·opinion_trend 동일 여부 + query keyword overlap + 과거 result(success/failed) 가중. |
| FR-2.3 | DailyOpinionSnapshot의 `summary`(사람용 trading summary)와 `query_positive/negative/opinion_trend/risk/attention/consensus`(검색용) 분리 생성(FinAgent식). |
| FR-2.4 | `opinion_reflection.py`: `LowLevelReflection`(의견 신호 → 이후 가격 변화. next_1d/3d/7d/14d_return·result_label) + `HighLevelReflection`(실제 매매 entry/exit 분석. pnl·net_pnl_after_cost·score_change·consensus_change·cost_drag·decision_quality·mistake_type·improvement·lesson). result_label/decision_quality enum은 §5. |
| FR-2.5 | `reddit_backtester.py`: 백테스트 루프에서 DailyOpinionSnapshot 저장 + (미래가격 확정 가능하므로) LowLevelReflection 생성 + trade closed 시 HighLevelReflection 생성. **실시간은 미래수익률 확정 snapshot에만 reflection 생성**하도록 구조 분리(`forward_returns_ready` 게이트). `COMMUNITY_REFLECTION_FORWARD_RETURNS=[1,3,7,14]`. |
| FR-2.6 | `reddit_portfolio.py`: 진입 시 entry opinion 상태 저장(entry_opinion_score·consensus·neutral·velocity·opinion_trend·persistence·universe_tier·tradeability·summary·query_opinion_trend·query_risk·size_factor·cost_filter_result). 청산 시 현재 opinion과 비교해 HighLevelReflection 데이터 전달. |
| FR-2.7 | 전체 v2는 `COMMUNITY_MEMORY_ENABLED`/`COMMUNITY_REFLECTION_ENABLED`/`COMMUNITY_LOW_LEVEL_REFLECTION_ENABLED`/`COMMUNITY_HIGH_LEVEL_REFLECTION_ENABLED` 토글. OFF면 저장/검색 no-op → 회귀 0. |

### v3. Decision Router + Optional LLM Router

| ID | 요구사항 |
|----|----------|
| FR-3.1 | `decision_router.py`: `DecisionRouter` 입력(symbol·current_signal·daily_opinion_snapshot·retrieved_*·RSI·ATR·market_filter_status·universe_decision·cost_filter_decision·current_position·cash·equity·risk settings) → `DecisionResult`(§5 schema, `router_mode∈{rule_based,llm_assisted}`). |
| FR-3.2 | **Rule-based router(기본)**: BUY 승인/축소/SKIP/SELL·REDUCE 규칙(스펙 §v3.2 전부). BUY 승인 = current_signal∈{BUY,STRONG_BUY} & opinion_score≥LOW & consensus≥MIN & neutral≤MAX & persistence≥MIN & universe.allowed & cost.allowed & bearish_risk낮음 & 과거 유사사례 대부분 실패 아님. action∈{BUY,HOLD,SELL,REDUCE,SKIP,EXIT}. |
| FR-3.3 | **LLM router(선택, 기본 OFF)**: `COMMUNITY_LLM_ROUTER_ENABLED`/`--llm-router`로만 활성. `LLMDecisionResult` strict JSON(§5). 도구 결과 해석 + rule-based 1차 판단 보정 + reasoning 기록. `COMMUNITY_LLM_ROUTER_REQUIRE_STRICT_JSON`/`_FALLBACK_TO_RULE_BASED`/`_MAX_TOKENS=1200`/`_TEMPERATURE=0.0`. |
| FR-3.4 | **LLM 8개 하드 안전장치**: ① rule SKIP을 LLM 단독 BUY 불가 ② neutral>MAX BUY 금지 ③ consensus<MIN BUY 금지 ④ ambiguity risk BUY 금지 ⑤ universe blocked BUY 금지 ⑥ cost blocked BUY 금지 ⑦ cash 부족 BUY 금지 ⑧ 포지션 없으면 SELL 금지. + LLM confidence 낮으면 rule-based 우선, schema 위반 시 무시·fallback. |
| FR-3.5 | `reddit_portfolio.py` 매수 전 파이프라인: WSB V3 signal → DailyOpinionSnapshot 로드 → UniverseFilter → CostAwareTradeFilter → Memory retrieval → DecisionRouter → **action==BUY일 때만 매수**, size_factor=DecisionResult.size_factor. 포지션에 decision_reason·reason_codes·memory_hits_used·router_mode·llm_*·size_factor·stop/trailing·entry_opinion_snapshot_id 저장. |
| FR-3.6 | 청산: **기존 check_exit 5단계 순서 유지**. sentiment_reversal→opinion_reversal 확장(이미 일부 구현). DecisionRouter가 REDUCE/SELL/EXIT 판단 시 exit_reason에 기록. profit target 기본 OFF 유지. |
| FR-3.7 | `reddit_backtester.py` 확장: `RedditTradeRecord` += decision_*·router_mode·memory_hits_used·historical_success_score·gross_pnl·net_pnl_after_cost·commission_paid·estimated_slippage_paid·cost_drag_pct·universe_mode·universe_tier. `RedditBacktestResult` += gross/net_return_pct·total_commission_paid·estimated_slippage_paid·cost_to_gross_profit_ratio·router_action_dist·avg_decision_confidence·avg_memory_hits_used·trades_skipped_by_{universe,cost,ambiguity,liquidity}·universe_mode. |
| FR-3.8 | `main.py`: `--universe {sp500_only,nasdaq100_only,sp500_nasdaq100,liquid_us,community_liquid,custom_watchlist}`(기본 community_liquid) + `--llm-router`(store_true, 기본 OFF). `RedditReplayBacktester(universe_mode=…, llm_router=…)` 전달. |

### NFR / 회귀 보호

| ID | 요구사항 |
|----|----------|
| NFR-01 | 급등추격 금지 — size_factor max 1.3, NEW_SPIKE 단독 축소, NEW_SPIKE을 강한 매수로 과대평가 금지. |
| NFR-02 | `signals.py`·`backtester.py`·뉴스 모델/뉴스 포트폴리오 무수정. |
| NFR-03 | **모든 신규 필터 OFF + `--sizing equal` → 기존 결과와 동일**(회귀 0). source/universe/cost ON 시 결과 변동은 허용. |
| NFR-04 | 백테스트 결정성 — universe 정적 리스트·인메모리 history, 전역 파일 미오염. memory/reflection jsonl은 백테스트 전용 경로 또는 append-only. |
| NFR-05 | 반드시 gross_return_pct·net_return_pct 둘 다 출력. net = 수수료+슬리피지 차감. |
| NFR-06 | `scripts/regression_check_reddit.py`: baseline json vs current json의 trade entry/exit/pnl·final_equity·total_trades 비교, equal sizing 차이 시 exit 1. |
| NFR-07 | LLM router 기본 OFF — `COMMUNITY_LLM_ROUTER_ENABLED=False`일 때 LLM 호출 0회(테스트로 검증). |

---

## 4. 변경/신규 파일

**신규 파일**
| 파일 | 역할 |
|------|------|
| `universe_filter.py` | UniverseFilter + UniverseDecision (FR-00.1~3) |
| `cost_aware_trade_filter.py` | CostAwareTradeFilter + CostAwareTradeDecision (FR-00.4~6) |
| `community_memory.py` | CommunityMemoryStore (jsonl) (FR-2.1~2) |
| `opinion_reflection.py` | Low/HighLevelReflection (FR-2.4) |
| `decision_router.py` | DecisionRouter + LLM router (FR-3.1~4) |
| `scripts/regression_check_reddit.py` | equal 회귀 검출 (NFR-06) |
| `data/universe/sp500.json`, `nasdaq100.json` | 정적 index 멤버십 (D1) |
| `tests/test_universe_filter.py` 등 7종 | §6 |

**수정 파일**
| 파일 | 주요 변경 |
|------|-----------|
| `config.py` | `COMMUNITY_*` 상수(+alias) (§5) |
| `reddit_collector.py` | flair/source 보존 + source_quality_weight + ticker ambiguity 필터 |
| `wsb_signal_engine.py` | `DailyOpinionSnapshot` 정식 정의 + 생성 |
| `wsb_state.py` | daily_opinion_snapshots.jsonl I/O + 스냅샷 필드 확장 |
| `position_sizer.py` | Sizer 공식 +3 factor (D5) |
| `reddit_portfolio.py` | 매수 전 router 파이프라인 + entry 스냅샷·reflection 데이터 |
| `reddit_backtester.py` | universe/cost metric + snapshot/reflection 생성 + TradeRecord/Result 확장 |
| `main.py` | `--universe` / `--llm-router` |

**무수정(불가침)**: `signals.py`, `backtester.py`, `sentiment_provider.py`(구조), `collector.py`, `kis_broker.py`, `trader.py`, `scheduler.py`, `app.py`, 뉴스 포트폴리오 로직.

---

## 5. config 상수 + 스키마

### 5.1 config (요지 — D2 alias 적용)
```python
# Universe / Cost (신규 독립값)
COMMUNITY_UNIVERSE_MODE = "community_liquid"   # sp500_only|nasdaq100_only|sp500_nasdaq100|liquid_us|community_liquid|custom_watchlist
COMMUNITY_ENABLE_UNIVERSE_FILTER = True
COMMUNITY_ENABLE_COST_AWARE_FILTER = True
COMMUNITY_MIN_PRICE_USD = 5.0
COMMUNITY_MIN_AVG_DOLLAR_VOLUME = 20_000_000
COMMUNITY_MIN_MARKET_CAP = 1_000_000_000
COMMUNITY_EXCLUDE_OTC = True
COMMUNITY_EXCLUDE_PENNY_STOCKS = True
COMMUNITY_ALLOW_NON_INDEX_IF_LIQUID = True
COMMUNITY_NON_INDEX_SIZE_MULTIPLIER = 0.5
COMMUNITY_NEW_SYMBOL_OBSERVATION_DAYS = 2
COMMUNITY_ESTIMATED_SLIPPAGE_PCT = 0.001
COMMUNITY_ESTIMATED_SPREAD_PCT = 0.001
COMMUNITY_MIN_EDGE_TO_COST_MULTIPLIER = 2.0
COMMUNITY_MIN_ATR_PCT_FOR_TRADE = 1.0
COMMUNITY_MAX_TURNOVER_PER_DAY = 0.25
COMMUNITY_MIN_HOLDING_DAYS_SOFT = 2
COMMUNITY_COOLDOWN_DAYS_AFTER_EXIT = 2
COMMUNITY_CUSTOM_WATCHLIST = []

# v1 source quality / ambiguity / snapshot
COMMUNITY_ENABLE_SOURCE_QUALITY_FILTER = True
COMMUNITY_ENABLE_TICKER_AMBIGUITY_FILTER = True
COMMUNITY_ENABLE_DAILY_OPINION_SNAPSHOT = True
COMMUNITY_HIGH_QUALITY_FLAIRS = ["DD","Discussion","News","Options","Technical Analysis","Technicals","Fundamentals","Stocks"]
COMMUNITY_LOW_QUALITY_FLAIRS = ["Meme","Gain","Loss","Shitpost","Satire","Storytime","Donation"]
COMMUNITY_FLAIR_WEIGHT_DD/DISCUSSION/NEWS/OPTIONS/TECHNICAL/FUNDAMENTALS/DAILY_THREAD/LOW_QUALITY = 1.5/1.0/1.2/0.9/1.0/1.2/0.5/0.0
COMMUNITY_TITLE/BODY/COMMENT_MENTION_WEIGHT = 2.0/1.0/0.5
COMMUNITY_MIN_DAILY_MENTIONS = 3
COMMUNITY_CONSENSUS_MIN_RATIO = 1.5
COMMUNITY_NEUTRAL_RATIO_MAX = 0.70
COMMUNITY_TICKER_AMBIGUITY_BLACKLIST = ["ALL","IT","DD","NOW","ARE","SO","ON","LOW","COST","KEY","FOR","CEO","AI"]
COMMUNITY_SINGLE_LETTER_TICKER_REQUIRE_DOLLAR = True
COMMUNITY_SIZE_FACTOR_MIN = 0.0
COMMUNITY_SIZE_FACTOR_MAX = 1.3

# 의견 파라미터 — 기존 WSB_OPINION_* alias (D2, 단일 소스)
COMMUNITY_OPINION_SCORE_HIGH/MID/LOW = WSB_OPINION_SCORE_HIGH/MID/LOW
COMMUNITY_OPINION_FACTOR_HIGH/MID/LOW = WSB_OPINION_FACTOR_HIGH/MID/LOW
COMMUNITY_OPINION_FACTOR_SKIP = 0.0
COMMUNITY_OPINION_TREND_* / PERSISTENCE_* / NEUTRAL_FACTOR_* / NEW_SPIKE/HIGH_ATTENTION/DECLINING = (alias 또는 신규)

# v2 memory / reflection
COMMUNITY_MEMORY_ENABLED = True
COMMUNITY_MEMORY_BACKEND = "jsonl"
COMMUNITY_MEMORY_TOP_K = 5
COMMUNITY_REFLECTION_ENABLED = True
COMMUNITY_REFLECTION_FORWARD_RETURNS = [1,3,7,14]
COMMUNITY_LOW_LEVEL_REFLECTION_ENABLED = True
COMMUNITY_HIGH_LEVEL_REFLECTION_ENABLED = True

# v3 LLM router (기본 OFF)
COMMUNITY_LLM_ROUTER_ENABLED = False
COMMUNITY_LLM_ROUTER_MODEL = "gpt4"      # 실호출은 config.GPT_MODEL로 매핑(D3)
COMMUNITY_LLM_ROUTER_REQUIRE_STRICT_JSON = True
COMMUNITY_LLM_ROUTER_FALLBACK_TO_RULE_BASED = True
COMMUNITY_LLM_ROUTER_MAX_TOKENS = 1200
COMMUNITY_LLM_ROUTER_TEMPERATURE = 0.0
```

### 5.2 핵심 스키마 (요약)
- **DailyOpinionSnapshot**: date·symbol·bullish/bearish/neutral_count·weighted_*·total_mentions·source_quality_score·consensus_ratio·neutral_ratio·opinion_score·velocity_state·opinion_trend·persistence_days·attention_state·universe_tier·tradeability_score·is_consensus_buy/sell·top_reasons·top_keywords·summary·query_positive/negative/opinion_trend/risk/attention/consensus
- **UniverseDecision**: {symbol, allowed, universe_tier(CORE|EXPANDED|COMMUNITY_LIQUID|BLOCKED), reason_codes[], liquidity_score, tradeability_score, size_multiplier}
- **CostAwareTradeDecision**: {allowed, reason_codes[], round_trip_cost_pct, expected_edge_proxy, edge_to_cost_ratio, cost_risk_factor, recommended_action}
- **CommunityMemoryStore**: add_opinion_snapshot/add_low_level_reflection/add_high_level_reflection + retrieve_similar_opinions/low_level/high_level(symbol, query, top_k)
- **LowLevelReflection**: date·symbol·opinion_score·consensus_ratio·neutral_ratio·velocity_state·opinion_trend·persistence_days·universe_tier·next_1d/3d/7d/14d_return·result_label(success_1d|success_3d|success_7d|failed|noisy|delayed|cost_inefficient)·reasoning·lesson·query
- **HighLevelReflection**: symbol·entry/exit_date·entry/exit_price·pnl_pct·dollar_pnl·net_pnl_after_cost·total_commission_paid·entry/exit_opinion_score·entry/exit_consensus_ratio·entry/exit_neutral_ratio·entry/exit_velocity_state·entry_universe_tier·exit_reason·decision_quality(good_entry_good_exit|good_entry_bad_exit|bad_entry|late_entry|early_exit|overtraded_cost_drag|risk_management_success|risk_management_failure)·mistake_type·improvement·lesson·query
- **DecisionResult**: {action(BUY|HOLD|SELL|REDUCE|SKIP|EXIT), confidence, size_factor, risk_modifier, stop_loss_pct, trailing_stop_pct, reason_codes[], reasoning, tool_interpretation{opinion/consensus/noise/memory/reflection/technical/universe/cost/risk_signal}, memory_hits_used[], warnings[], router_mode}
- **LLMDecisionResult**: DecisionResult와 동일 + `size_factor_modifier`(size_factor 대신), router_mode 없음

### 5.3 OpinionTrendSizer 공식 (D5 확장)
```
final_size_factor = clamp(
    opinion_score_factor × trend_factor × persistence_factor × consensus_factor ×
    neutral_factor × attention_factor × source_quality_factor ×
    universe_size_multiplier × cost_risk_factor,
    COMMUNITY_SIZE_FACTOR_MIN, COMMUNITY_SIZE_FACTOR_MAX)   # 0.0 ~ 1.3
# 신규 3 factor는 opinion에 속성 없으면 1.0 → 기존 T1~T12 회귀 0
# opinion_score<LOW | neutral>MAX | consensus<MIN | cost SKIP → size_factor 0
shares = floor(total_cash × EQUAL_POSITION_PCT × final_size_factor / open_price)
```

---

## 6. 테스트

| 파일 | 핵심 케이스 |
|------|------------|
| `tests/test_community_opinion_snapshot.py` | low quality flair weight 0 / DD 높은 weight / title>body weight / ambiguity 일반어 제외 / `$`+단일문자 인정 / neutral>0.70 consensus_buy False / ratio≥1.5 consensus_buy True / snapshot jsonl 저장 |
| `tests/test_opinion_trend_sizer.py` | score≥80 high / <60 0 / 3일↑ trend↑ / 3일↓ trend↓ / consensus 약 축소 / neutral 높음 제외 / NEW_SPIKE 단독 축소 / factor≤1.3 / universe_size_multiplier / cost edge 부족 skip |
| `tests/test_universe_filter.py` | mode별 allowed / CORE·EXPANDED·COMMUNITY_LIQUID·BLOCKED tier / 저유동·OTC·penny BLOCKED / 필터 OFF→allowed True |
| `tests/test_cost_aware_trade_filter.py` | round_trip_cost 계산 / edge<cost×2 SKIP / ATR pct 부족 SKIP / 필터 OFF→allowed True |
| `tests/test_community_memory_reflection.py` | snapshot 저장/로드 / query field 생성 / 유사검색 / low-level next_1d/3d/7d/14d / high-level pnl·score_change·consensus_change·cost_drag / closed trade reflection / jsonl append/read |
| `tests/test_decision_router.py` | strong consensus+low neutral+persistence→BUY / NEW_SPIKE 단독 축소·SKIP / neutral 높음 SKIP / consensus 붕괴 SELL·REDUCE / 과거 실패 多 축소 / universe blocked BUY 금지 / cost blocked BUY 금지 / factor≤1.3 / **equal 회귀** / DecisionResult schema |
| `tests/test_llm_decision_router_schema.py` | invalid JSON→rule fallback / BUY 금지조건서 LLM BUY→SKIP 보정 / `COMMUNITY_LLM_ROUTER_ENABLED=False`→호출 0회 / strict schema 검증 |
| (기존) `tests/test_opinion_trend_sizing.py` | T1~T12 **회귀 0 유지** |

---

## 7. 성공 기준

| SC | 기준 | 검증 |
|----|------|------|
| SC-01 | `--universe {6모드}` 동작 | CLI |
| SC-02 | gross/net return 동시 출력 + 비용 metric(commission/slippage/turnover/cost_to_gross_profit_ratio) | 백테스트 출력 |
| SC-03 | source quality·ambiguity 필터 weight/skip 통계 | pytest + 출력 |
| SC-04 | DailyOpinionSnapshot jsonl 저장 | pytest |
| SC-05 | Memory 저장/유사검색 + Low/High Reflection 생성 | pytest |
| SC-06 | DecisionRouter rule-based 동작 + 8 안전장치 + LLM fallback | pytest |
| SC-07 | **모든 신규 필터 OFF + `--sizing equal` 회귀 0** | `scripts/regression_check_reddit.py` exit 0 |
| SC-08 | final_size_factor ≤ 1.3 / NEW_SPIKE 단독 축소 | pytest |
| SC-09 | `COMMUNITY_LLM_ROUTER_ENABLED=False`→LLM 호출 0 | pytest |
| SC-10 | universe별 성과 비교 가능(sp500_only / community_liquid / liquid_us / sp500_nasdaq100) | 백테스트 출력 |
| SC-11 | 신규 테스트 7종 + 기존 12종 pytest 통과 | pytest |

---

## 8. 구현 순서 (Module Map / Session Guide)

| Step/Module | 파일 | 작업 | Session |
|-------------|------|------|---------|
| Step 0 | (분석) | 본 §2 코드 자산 정리 (완료) | S1 |
| Step 1 / M-config | config.py | COMMUNITY_* 상수 + alias (§5.1) | S1 (module-1) |
| Step 2 / M-universe | universe_filter.py, cost_aware_trade_filter.py, data/universe/*.json + 테스트 2종 | FR-00.* | S1 (module-2) |
| Step 3 / M-collector | reddit_collector.py | source quality + ticker ambiguity (FR-1.1~2) | S2 (module-3) |
| Step 4 / M-snapshot | wsb_signal_engine.py, wsb_state.py | DailyOpinionSnapshot + jsonl (FR-1.3~5) | S2 (module-4) |
| Step 5 / M-sizer | position_sizer.py + test_opinion_trend_sizer.py | +3 factor (FR-1.6, D5) | S2 (module-4) |
| Step 6 / M-memory | community_memory.py, opinion_reflection.py + test | FR-2.1~4 | S3 (module-5) |
| Step 7 / M-wiring | reddit_backtester.py, reddit_portfolio.py | memory/reflection 배선 (FR-2.5~6) | S3 (module-5) |
| Step 8 / M-router | decision_router.py (rule-based) + test_decision_router.py | FR-3.1~2 | S4 (module-6) |
| Step 9 / M-llm | decision_router.py (LLM, OFF) + test_llm_decision_router_schema.py | FR-3.3~4 | S4 (module-6) |
| Step 10 / M-metrics | reddit_backtester.py | universe/cost/router metric + 비교표 (FR-3.7) | S5 (module-7) |
| Step 11 / M-cli+regr | main.py, scripts/regression_check_reddit.py | `--universe`/`--llm-router` + 회귀 (FR-3.8, NFR-06) | S5 (module-7) |

**검증 명령**
```
pytest tests/test_community_opinion_snapshot.py tests/test_opinion_trend_sizer.py \
       tests/test_community_memory_reflection.py tests/test_universe_filter.py \
       tests/test_cost_aware_trade_filter.py tests/test_decision_router.py \
       tests/test_llm_decision_router_schema.py tests/test_opinion_trend_sizing.py

python main.py --backtest --source reddit --model finbert-wsb --ranking sentiment --sizing opinion_trend --universe community_liquid --from 2026-02-01 --to 2026-04-01
python main.py --backtest --source reddit --model finbert-wsb --ranking sentiment --sizing opinion_trend --universe sp500_only      --from 2026-02-01 --to 2026-04-01
python main.py --backtest --source reddit --model finbert-wsb --ranking sentiment --sizing equal         --universe community_liquid --from 2026-02-01 --to 2026-04-01
python scripts/regression_check_reddit.py    # equal 회귀 검출
```

---

## 9. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 신규 필터/라우터가 equal 회귀 파괴 | **최상위** | config flag 격리 + equal 경로 router 우회 + regression_check exit 1 |
| universe 멤버십/시총 데이터 부재 | 높음 | 정적 JSON + OHLCV 유동성, 시총 선택 (D1) |
| 백테스트 데이터에 flair 없음 | 중 | forward 수집 시 부여, 백테스트 fallback weight 1.0 |
| LLM이 BUY 금지조건 위반 | 높음 | 8개 하드 안전장치 + confidence/schema 게이팅 + 기본 OFF |
| backtester.py 불가침 vs 지표 확장 | 중 | reddit 전용 dataclass 확장(D6) |
| 데이터 소표본(9~17일) | 중 | 방향성 확인용 한정, 14일 경고 유지 |
| memory/reflection jsonl 비대·오염 | 중 | 백테스트 전용 경로 또는 append-only, 토글 OFF 시 no-op |
| factor 임의 초기값 | 중 | 초기값, grid search 별도 |

---

## 10. 기존 WSB V3 / community-opinion-trend-sizing 대비 달라진 점

- **거래 대상**: 전 종목 → universe_mode 게이팅(CORE/EXPANDED/COMMUNITY_LIQUID/BLOCKED) + size_multiplier
- **비용**: gross만 → **gross/net 동시** + cost-aware SKIP(edge<cost×2)
- **글 품질**: flair denylist만 → source_quality_weight(DD 1.5 … low 0.0) + title>body>comment weight
- **티커**: Polygon 검증만 → ambiguity blacklist + `$` 강제 + skip 통계
- **학습**: 없음 → memory(유사 사례) + low/high reflection(사후 분석)
- **의사결정**: 단일 rule → DecisionRouter(rule 기본 + 선택 LLM explainer, 8 안전장치)
- **출력**: 기본 지표 → +비용/turnover/router action 분포/memory hit/skip 카운트
- **불변**: 5단계 청산 순서, profit target OFF, NEW_SPIKE 보수, equal 회귀 0

**universe 비교 방법**: 동일 기간·모델·ranking·sizing 고정 후 `--universe` 만 바꿔 4회 실행 → gross/net return·turnover·cost_to_gross_profit_ratio·trades_skipped_by_* 비교표로 sp500_only(고유동·저오탐·저변동) vs community_liquid(관심 종목 포함·고변동) vs liquid_us vs sp500_nasdaq100 trade-off 확인.

---

## 11. Brainstorming Log (plan-plus 결정 기록)

| Phase | 결정 | 이유 |
|-------|------|------|
| 0 (Context) | 코드베이스 전체 검토: v1 핵심 구현 완료, equal 회귀가 최상위 제약 | 본 피처는 기존 자산 위 확장 |
| 1 (Intent) | 의도 = 여론 의사결정 에이전트(universe/cost/quality/learning/reasoning), 급등추격 X | 사용자 상세 스펙에서 명확 |
| 2 (Alternatives) | **Approach A**(backtester 오케스트레이션 + 독립 모듈) + **process_day 게이팅** | equal 회귀 안전 + 기존 trend-sizing 패턴 정합 + skip 통계 정확 |
| 3 (YAGNI) | 선택 4항목(turnover/cooldown·시총 게이팅·memory 추상 인터페이스·top_keywords) **전부 1차 포함** / 네이버·vector DB 실연동·grid search 연기 | 사용자 multiSelect 전체 선택, 핵심 가설 검증에 필요 |
| 4 (Incremental) | 아키텍처/데이터흐름/회귀격리 3섹션 승인 | 사용자 "승인 — Plan 문서 최종화" |
| 결정 D1~D8 | universe 정적 JSON·config alias·LLM 인터페이스+실호출·통합설계·sizer in-place 확장·snapshot 정식화·회귀 격리·NEW_SPIKE 보수 | Checkpoint + 4개 질문 확정 |
