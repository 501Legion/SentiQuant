# Design: Community Opinion Agent — 커뮤니티 여론 의사결정 에이전트 (v0~v3)

**Feature**: community-opinion-agent
**Date**: 2026-05-29
**Status**: Design
**Branch**: `community-opinion-trend-sizing`
**Architecture**: Option C — Pragmatic (독립 5모듈 + reddit_backtester 오케스트레이션, plan-plus Approach A)
**Plan**: `docs/01-plan/features/community-opinion-agent.plan.md`

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 여론 전략의 실전 성과는 universe(거래 대상)·cost(비용 대비 edge)·source/ambiguity(글 품질·티커 오탐)·memory/reflection(과거 학습)에 좌우된다. NEW_SPIKE은 강한 매수가 아니라 지속성·합의도·과거 성공 여부로 재평가한다. |
| **WHO** | 1차 전략 연구자(gross vs net 검증) → 2차 운영자(KIS 모의투자 전사). |
| **RISK** | equal 회귀 파괴 ★최상위 → flag + 모듈 격리 + regression_check. universe 데이터 부재 → 정적 JSON. flair 부재(백테스트) → fallback weight 1.0. LLM BUY 금지조건 위반 → 8 안전장치. backtester.py 불가침 → reddit 전용 dataclass. |
| **SUCCESS** | `--universe` 6모드 + gross/net 동시 출력 + 필터/메모리/라우터 동작 + LLM 안전장치 + **신규 필터 OFF + equal 회귀 0** + 신규 테스트 7종 통과. |
| **SCOPE** | 신규: universe_filter / cost_aware_trade_filter / community_memory / opinion_reflection / decision_router / scripts/regression_check_reddit + data/universe/*.json + 테스트 7종. 수정: config / reddit_collector / wsb_signal_engine / wsb_state / position_sizer / reddit_portfolio / reddit_backtester / main. **불가침**: signals.py · backtester.py · 뉴스 모델/포트폴리오. |

---

## 1. Overview

WSB V3 rule-based 신호를 1차 후보로 유지하고, 매수 직전 단계에 ⓪Universe/Cost 게이팅 → v1 source quality·ambiguity·DailyOpinionSnapshot → v2 Memory/Reflection → v3 DecisionRouter를 끼워 넣는다. 5개 신규 모듈은 **순수 클래스/함수**로 독립 파일에 두고, `reddit_backtester.RedditReplayBacktester.run()`이 날짜 루프에서 이들을 호출해 결과를 `process_day`에 주입한다. 모든 신규 기능은 config flag로 토글되며, OFF 또는 `--sizing equal` 시 기존 동작과 byte 동일(회귀 0).

**설계 원칙**
- 신호 엔진(`run_pipeline`의 BUY/STRONG_BUY/consensus/rank)·5단계 청산 순서 불변
- 게이팅은 후보 선정 **후** 매수 직전(process_day) → skip 통계 정확 집계
- LLM은 자율 매매자 아님 — rule-based 1차 판단을 승인/축소/보류/거절하는 explainer
- 데이터 미제공 시 모든 신규 factor=1.0, allowed=True → 회귀 0

---

## 2. Architecture (Option C)

```
┌──────────────────────── reddit_backtester.RedditReplayBacktester.run() ────────────────────────┐
│ (오케스트레이터 — 날짜 루프)                                                                    │
│                                                                                                 │
│  posts_by_symbol ← RedditCollector.load_posts(date)   (forward 수집 시 flair/source_quality 부착)│
│  top_n, signal_details ← WSBSignalEngine.run_pipeline(...)        [신호 엔진 불변]               │
│                                                                                                 │
│  for sym in 후보:                                                                                │
│    snapshot  ← WSBSignalEngine.build_daily_snapshot(sym, scored, history, univ_dec)  [v1]        │
│    univ_dec  ← UniverseFilter.decide(sym, ohlcv=…, market_cap=…)                     [v0]        │
│    cost_dec  ← CostAwareTradeFilter.evaluate(snapshot, atr, price)                   [v0]        │
│    mem_hits  ← CommunityMemoryStore.retrieve_*(sym, snapshot.query_*)                [v2]        │
│    decision  ← DecisionRouter.decide(sym, signal, snapshot, mem_hits, univ_dec,                  │
│                                       cost_dec, rsi, atr, market_filter, pos, cash, equity) [v3] │
│    opinion_metrics[sym] = snapshot (+univ/cost factor)                                            │
│    decisions[sym] = decision                                                                     │
│                                                                                                 │
│  portfolio.process_day(top_n, exit_signals, ohlcv, sizer, scored, atr_cache,                     │
│                        position_scores, opinion_metrics, decisions)        [매수 직전 게이팅]    │
│    └ action==BUY인 후보만 매수, shares = sizer.calc_shares(... opinion=snapshot),                │
│       size_factor = decision.size_factor                                                         │
│  engine.check_exit(...)   [5단계 순서 유지 + router REDUCE/SELL/EXIT 반영]                       │
│  trade closed → HighLevelReflection 생성, snapshot+reflection jsonl append   [v2]               │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘

독립 모듈 (순수, equal 경로 비침습):
  universe_filter.py          UniverseFilter, UniverseDecision, load_universe_sets()
  cost_aware_trade_filter.py  CostAwareTradeFilter, CostAwareTradeDecision
  community_memory.py         MemoryBackend(ABC), JsonlMemoryStore, CommunityMemoryStore
  opinion_reflection.py       LowLevelReflection, HighLevelReflection, build_low/high_level()
  decision_router.py          DecisionRouter, DecisionResult, LLMRouter, LLMDecisionResult
```

---

## 3. Module Specs (인터페이스)

### 3.1 universe_filter.py (신규)
```python
@dataclass
class UniverseDecision:
    symbol: str
    allowed: bool
    universe_tier: str            # "CORE"|"EXPANDED"|"COMMUNITY_LIQUID"|"BLOCKED"
    reason_codes: list[str]
    liquidity_score: float        # 0~1 (avg_dollar_volume 정규화)
    tradeability_score: float     # 0~1 (가격·유동성·ambiguity 종합)
    size_multiplier: float        # CORE 1.0 / EXPANDED·COMMUNITY_LIQUID 0.5

def load_universe_sets() -> tuple[set[str], set[str], dict[str, float]]:
    """data/universe/sp500.json, nasdaq100.json 로드 → (sp500, nasdaq100, market_caps).
       파일 없으면 빈 set/{}; market_caps는 {symbol: cap} (선택)."""

class UniverseFilter:
    def __init__(self, mode: str = None):  # None → config.COMMUNITY_UNIVERSE_MODE
    def decide(self, symbol: str, *, ohlcv=None, price: float = None,
               avg_dollar_volume: float = None, market_cap: float = None,
               ambiguity_risk: bool = False) -> UniverseDecision
```
- **tier 판정**: symbol∈(sp500∪nasdaq100)→CORE / 인덱스 외 & 유동성·가격·(시총 있으면)시총 통과 → EXPANDED(우량) 또는 COMMUNITY_LIQUID / OTC·penny·저유동·ambiguity → BLOCKED.
- **mode→allowed**: `sp500_only`(CORE∩sp500), `nasdaq100_only`(CORE∩nasdaq100), `sp500_nasdaq100`(CORE), `liquid_us`(CORE∪EXPANDED), `community_liquid`(CORE∪EXPANDED∪COMMUNITY_LIQUID), `custom_watchlist`(config.COMMUNITY_CUSTOM_WATCHLIST).
- `avg_dollar_volume`/`price`는 ohlcv(prefetch DataFrame)에서 계산: `avg_dollar_volume = mean(close*volume, 최근 20일)`.
- **`COMMUNITY_ENABLE_UNIVERSE_FILTER=False` → allowed=True, tier="CORE", size_multiplier=1.0** (회귀 0).

### 3.2 cost_aware_trade_filter.py (신규)
```python
@dataclass
class CostAwareTradeDecision:
    allowed: bool
    reason_codes: list[str]
    round_trip_cost_pct: float
    expected_edge_proxy: float
    edge_to_cost_ratio: float
    cost_risk_factor: float       # edge 여유 작으면 <1.0, 충분하면 1.0
    recommended_action: str       # "ENTER"|"DOWNSIZE"|"SKIP"

class CostAwareTradeFilter:
    def evaluate(self, *, atr_pct: float = None, recent_volatility_pct: float = None,
                 opinion_conviction: float = None, commission_rate: float = None
                 ) -> CostAwareTradeDecision
```
- `round_trip_cost_pct = COMMISSION_RATE*2 + COMMUNITY_ESTIMATED_SLIPPAGE_PCT + COMMUNITY_ESTIMATED_SPREAD_PCT`.
- `expected_edge_proxy` 우선순위: ① atr_pct ② recent_volatility_pct ③ opinion_conviction 기반 expected_move_proxy.
- `edge < round_trip_cost_pct × COMMUNITY_MIN_EDGE_TO_COST_MULTIPLIER(2.0)` 또는 `atr_pct < COMMUNITY_MIN_ATR_PCT_FOR_TRADE` → recommended_action=SKIP, allowed=False.
- edge_to_cost가 경계 근처면 DOWNSIZE + cost_risk_factor 0.5~0.8.
- **`COMMUNITY_ENABLE_COST_AWARE_FILTER=False` → allowed=True, cost_risk_factor=1.0** (회귀 0).

### 3.3 community_memory.py (신규)
```python
class MemoryBackend(ABC):                     # 향후 Chroma/Faiss 교체 지점
    @abstractmethod
    def append(self, kind: str, record: dict) -> None: ...
    @abstractmethod
    def read_all(self, kind: str) -> list[dict]: ...

class JsonlMemoryStore(MemoryBackend):        # data/community/memory/{kind}.jsonl
    def __init__(self, base_dir: str = "data/community/memory"): ...

class CommunityMemoryStore:
    def __init__(self, backend: MemoryBackend = None, top_k: int = None): ...
    def add_opinion_snapshot(self, snapshot) -> None
    def add_low_level_reflection(self, reflection) -> None
    def add_high_level_reflection(self, reflection) -> None
    def retrieve_similar_opinions(self, symbol, query: dict, top_k=None) -> list[dict]
    def retrieve_low_level_reflections(self, symbol, query: dict, top_k=None) -> list[dict]
    def retrieve_high_level_reflections(self, symbol, query: dict, top_k=None) -> list[dict]
```
- **retrieve scoring(휴리스틱)**: `same_symbol(+0.4) + same_universe_tier(+0.1) + 1-|Δopinion_score|/100 + 1-|Δconsensus|/norm + 1-|Δneutral| + same_velocity(+0.1) + same_trend(+0.1) + query_keyword_overlap(jaccard) + result_bonus(success +0.1/failed -0.1)`. 상위 top_k 반환.
- `kind ∈ {opinion_snapshots, low_level_reflections, high_level_reflections}`.
- **`COMMUNITY_MEMORY_ENABLED=False` → add/retrieve no-op([] 반환)** (회귀 0).

### 3.4 opinion_reflection.py (신규)
```python
@dataclass
class LowLevelReflection:   # 의견 신호 → 이후 가격 변화
    date; symbol; opinion_score; consensus_ratio; neutral_ratio; velocity_state
    opinion_trend; persistence_days; universe_tier
    next_1d_return; next_3d_return; next_7d_return; next_14d_return
    result_label: str       # success_1d|success_3d|success_7d|failed|noisy|delayed|cost_inefficient
    reasoning: str; lesson: str; query: dict

@dataclass
class HighLevelReflection:  # 실제 매매 entry/exit 분석
    symbol; entry_date; exit_date; entry_price; exit_price
    pnl_pct; dollar_pnl; net_pnl_after_cost; total_commission_paid
    entry_opinion_score; exit_opinion_score; entry_consensus_ratio; exit_consensus_ratio
    entry_neutral_ratio; exit_neutral_ratio; entry_velocity_state; exit_velocity_state
    entry_universe_tier; exit_reason
    decision_quality: str   # good_entry_good_exit|good_entry_bad_exit|bad_entry|late_entry|early_exit|overtraded_cost_drag|risk_management_success|risk_management_failure
    mistake_type: str; improvement: str; lesson: str; query: dict

def build_low_level(snapshot, forward_prices: dict[int, float], entry_price: float,
                    universe_tier: str) -> LowLevelReflection
def build_high_level(entry_snap: dict, exit_snap: dict, trade: dict) -> HighLevelReflection
```
- **result_label 규칙**: next_1d≥+임계 success_1d, 3d/7d 유사, 수익이지만 변동 큼 noisy, 늦게 상승 delayed, edge<cost cost_inefficient, 하락 failed.
- **decision_quality 규칙**: entry 의견 양호+이익 good_entry_good_exit, 양호하나 손실 good_entry_bad_exit, entry 의견 약함 bad_entry, 늦은 진입 late_entry, 조기 청산 early_exit, 비용 과다 overtraded_cost_drag, stop/trailing 보호 성공·실패.
- **forward returns**: 백테스트만 next_1d/3d/7d/14d 계산(미래 가격 확정). 실시간은 `forward_returns_ready` 게이트로 확정 snapshot에만 생성.
- **`COMMUNITY_*_REFLECTION_ENABLED=False` → 생성 skip**.

### 3.5 decision_router.py (신규)
```python
@dataclass
class DecisionResult:
    action: str            # BUY|HOLD|SELL|REDUCE|SKIP|EXIT
    confidence: float
    size_factor: float
    risk_modifier: float
    stop_loss_pct: float | None
    trailing_stop_pct: float | None
    reason_codes: list[str]
    reasoning: str
    tool_interpretation: dict   # opinion/consensus/noise/memory/reflection/technical/universe/cost/risk_signal
    memory_hits_used: list
    warnings: list
    router_mode: str       # "rule_based"|"llm_assisted"

class DecisionRouter:
    def __init__(self, llm_router: bool = False): ...
    def decide(self, *, symbol, current_signal, daily_opinion_snapshot,
               retrieved_similar_opinions, retrieved_low_level_reflections,
               retrieved_high_level_reflections, rsi, atr, market_filter_status,
               universe_decision, cost_filter_decision, current_position,
               cash, equity, risk_settings) -> DecisionResult
    def _rule_based(self, ctx) -> DecisionResult
    def _apply_llm(self, base: DecisionResult, ctx) -> DecisionResult   # llm_router=True일 때만
    def _enforce_safety(self, result, ctx) -> DecisionResult            # 8 안전장치

class LLMRouter:
    def query(self, ctx) -> "LLMDecisionResult | None"   # strict JSON, 실패 시 None
```
- **rule-based BUY 승인**: current_signal∈{BUY,STRONG_BUY} & opinion_score≥LOW & consensus≥MIN & neutral≤MAX & persistence≥MIN & universe.allowed & cost.allowed & bearish_risk낮음 & 과거 유사사례 대부분 실패 아님. size_factor = sizer factor × universe.size_multiplier × cost.cost_risk_factor (clamp ≤1.3).
- **BUY 축소**: NEW_SPIKE+persistence부족 / neutral 중간 / consensus 약함 / 과거 유사 손실 多 / ATR 높음 / COMMUNITY_LIQUID tier / cost_to_edge 작음 → action=BUY, size_factor 축소.
- **SKIP**: neutral>MAX / consensus<MIN / opinion_score 급락 / bearish 급증 / ambiguity / low quality 비중 高 / universe blocked / cost blocked / cash 부족.
- **SELL/REDUCE(보유 시)**: opinion_reversal / consensus 붕괴 / neutral 급증 / bearish 증가 / high-level reflection 유사 실패 / cost·turnover 리스크 증가.
- **LLM 8 안전장치(`_enforce_safety`)**: ①rule SKIP을 LLM BUY로 못 뒤집음 ②neutral>MAX BUY 금지 ③consensus<MIN BUY 금지 ④ambiguity BUY 금지 ⑤universe blocked BUY 금지 ⑥cost blocked BUY 금지 ⑦cash 부족 BUY 금지 ⑧포지션 없으면 SELL 금지. + confidence < 임계 → rule-based 우선, schema 위반/`LLMRouter.query`==None → fallback.
- LLM 실호출: `config.OPENAI_API_KEY` + `config.GPT_MODEL`(COMMUNITY_LLM_ROUTER_MODEL alias) 사용. **`COMMUNITY_LLM_ROUTER_ENABLED=False` & llm_router=False → LLMRouter 미인스턴스화·호출 0**.

### 3.6 수정 모듈
| 모듈 | 변경 |
|------|------|
| `reddit_collector.py` | `_fetch_subreddit`/`_fetch_daily_thread`에 flair 보존 + `_source_quality_weight(flair, source)` 부착; `_extract_tickers`에 ambiguity 필터(`_is_ambiguous(ticker, text)`: blacklist & `$`/단일문자 규칙) + title>body weight; skip 통계 dict 반환 |
| `wsb_signal_engine.py` | `DailyOpinionSnapshot` dataclass 정식 정의 + `build_daily_snapshot(symbol, scored, history, universe_decision)` 메서드 (weighted counts·query_*·summary·top_keywords). 기존 `OpinionMetrics` 소비 호환(snapshot이 동일 속성 보유) |
| `wsb_state.py` | `append_daily_snapshot(snapshot)`/`load_daily_snapshots()` (jsonl). `upsert_position_score`에 entry_universe_tier·entry_tradeability_score·entry_summary·entry_query_*·cost_filter_result·router 필드 추가 |
| `position_sizer.py` | `CommunityOpinionTrendSizer` 공식에 `_source_quality_factor`·`universe_size_multiplier`·`cost_risk_factor` 곱 추가(opinion 속성 없으면 1.0). clamp 상수 COMMUNITY_SIZE_FACTOR_* (= WSB_OPINION_* alias) |
| `reddit_portfolio.py` | `process_day(..., decisions=None)` 추가 — decision.action==BUY만 매수, size_factor=decision.size_factor; 진입 스냅샷에 universe/cost/router 필드 저장; 청산 시 HighLevelReflection 데이터 전달 |
| `reddit_backtester.py` | 오케스트레이션(§2) + `RedditTradeRecord`/`RedditBacktestResult` 비용·router·universe 필드 확장 + gross/net·skip 카운트 비교표 |
| `main.py` | `--universe`(choices 6, default community_liquid) + `--llm-router`(store_true) → `RedditReplayBacktester(universe_mode=…, llm_router=…)` |

---

## 4. Data Schemas

### DailyOpinionSnapshot (wsb_signal_engine.py)
```python
@dataclass
class DailyOpinionSnapshot:
    date: str; symbol: str
    bullish_count: int; bearish_count: int; neutral_count: int
    weighted_bullish_count: float; weighted_bearish_count: float; weighted_neutral_count: float
    total_mentions: int; source_quality_score: float
    consensus_ratio: float; neutral_ratio: float; opinion_score: float
    velocity_state: str; opinion_trend: str; persistence_days: int; attention_state: str
    universe_tier: str; tradeability_score: float
    is_consensus_buy: bool; is_consensus_sell: bool
    top_reasons: list[str]; top_keywords: list[str]
    summary: str                       # 사람용
    query_positive: str; query_negative: str; query_opinion_trend: str
    query_risk: str; query_attention: str; query_consensus: str
    # Sizer duck-typing 호환 alias 속성: sentiment_trend(=opinion_trend), atr, prev_close
```
- `consensus_ratio = weighted_bullish/max(weighted_bearish,1)`.
- `is_consensus_buy = weighted_bullish ≥ weighted_bearish×COMMUNITY_CONSENSUS_MIN_RATIO AND neutral_ratio ≤ COMMUNITY_NEUTRAL_RATIO_MAX AND total_mentions ≥ COMMUNITY_MIN_DAILY_MENTIONS`.
- `query_*`는 검색용 짧은 문자열(예: query_consensus="consensus 1.8 strong"); summary는 사람용 1~2문장.

(UniverseDecision/CostAwareTradeDecision/Low·HighLevelReflection/DecisionResult/LLMDecisionResult 스키마는 §3 참조)

---

## 5. Data Flow (매수 직전 게이팅)

```
1. run_pipeline → top_n, signal_details   (신호 엔진 불변)
2. for sym in top_n 후보:
   a. snapshot = build_daily_snapshot(sym, scored, opinion_history, univ_dec_pre)
   b. univ_dec = UniverseFilter.decide(sym, ohlcv, market_cap, ambiguity_risk)
   c. cost_dec = CostAwareTradeFilter.evaluate(atr_pct, recent_vol, conviction)
   d. mem = Memory.retrieve_similar_opinions/low/high(sym, snapshot.query_*)
   e. decision = DecisionRouter.decide(sym, signal, snapshot, mem, univ_dec, cost_dec, rsi, atr, ...)
   f. opinion_metrics[sym]=snapshot(+univ.size_multiplier,+cost.cost_risk_factor); decisions[sym]=decision
3. exit_signals = check_exit(...)   (5단계 순서; router REDUCE/SELL/EXIT 반영 가능)
4. process_day(top_n, exit_signals, ohlcv, sizer, scored, atr_cache, position_scores,
               opinion_metrics, decisions)
   └ decision.action==BUY 후보만 매수; shares=sizer.calc_shares(...opinion=snapshot)
5. trade closed → build_high_level(...) → Memory.add_high_level_reflection
   각 snapshot → Memory.add_opinion_snapshot + wsb_state.append_daily_snapshot
   forward 확정 → build_low_level(...) → Memory.add_low_level_reflection
```

**회귀 격리**: `--sizing equal` → decisions 미사용(top_n 그대로 매수), opinion_metrics 무시, univ/cost OFF면 allowed=True. → 기존 동작 byte 동일.

---

## 6. config 상수
Plan §5.1 전체. 핵심: `COMMUNITY_UNIVERSE_MODE`, `COMMUNITY_ENABLE_*`(universe/cost/source_quality/ambiguity/snapshot), 유동성/비용 임계, flair weight, ambiguity blacklist, memory/reflection flag, LLM router flag(기본 OFF). 의견 파라미터는 `WSB_OPINION_*` alias(D2).

---

## 7. Sizer 공식 (Plan §5.3)
```
final_size_factor = clamp(
   opinion_score_factor × trend_factor × persistence_factor × consensus_factor ×
   neutral_factor × attention_factor × source_quality_factor ×
   universe_size_multiplier × cost_risk_factor,
   COMMUNITY_SIZE_FACTOR_MIN(0.0), COMMUNITY_SIZE_FACTOR_MAX(1.3))
# 신규 3 factor: opinion 속성 없으면 1.0 → T1~T12 회귀 0
shares = floor(total_cash × EQUAL_POSITION_PCT × final_size_factor / open_price)
```

---

## 8. Test Plan
Plan §6의 7개 신규 테스트 + 기존 12개(test_opinion_trend_sizing.py) 회귀. 각 모듈은 외부 I/O 없이 순수 단위테스트 가능(파일은 tmp_path). 핵심 회귀: `test_decision_router.py`의 equal 경로 + `test_llm_decision_router_schema.py`의 `COMMUNITY_LLM_ROUTER_ENABLED=False`→호출 0 + `scripts/regression_check_reddit.py`.

---

## 9. Regression Protection
```
scripts/regression_check_reddit.py:
  baseline.json (필터 OFF + equal) vs current.json
  비교: 각 trade의 entry/exit/pnl, final_equity, total_trades
  차이 발견 → stderr 출력 + sys.exit(1)
생성: python main.py --backtest --source reddit --model finbert-wsb --ranking sentiment \
        --sizing equal --universe community_liquid --from … --to …  결과를 baseline으로 저장
```
신규 필터는 config flag로 OFF 가능 → 기존 결과 재현. source/universe/cost ON 시 변동 허용.

---

## 10. Risks (Plan §9 반영)
equal 회귀(최상위)→flag 격리+regression_check / universe 데이터 부재→정적 JSON / flair 부재→fallback 1.0 / LLM BUY 위반→8 안전장치 / backtester 불가침→reddit dataclass / 소표본→방향성 한정 / jsonl 비대→토글·append-only / factor 임의값→grid search 별도.

---

## 11. Implementation Guide

### 11.1 구현 순서
Plan §8 Step 0~11 그대로. 각 Step은 독립 모듈 또는 단일 파일 수정 → 단위테스트 동반.

### 11.2 핵심 파일
신규 5모듈 + scripts + data/universe + 테스트 7종, 수정 8파일(§3.6).

### 11.3 Session Guide (Module Map — `/pdca do --scope`)

| Module key | 파일 | 작업 | 의존 | Session |
|-----------|------|------|------|---------|
| **module-1** | config.py | COMMUNITY_* 상수 + WSB_OPINION_* alias (Step 1) | — | S1 |
| **module-2** | universe_filter.py, cost_aware_trade_filter.py, data/universe/*.json, test_universe_filter.py, test_cost_aware_trade_filter.py (Step 2) | module-1 | S1 |
| **module-3** | reddit_collector.py (source quality + ticker ambiguity) (Step 3) | module-1 | S2 |
| **module-4** | wsb_signal_engine.py(DailyOpinionSnapshot), wsb_state.py(jsonl), position_sizer.py(+3 factor), test_community_opinion_snapshot.py, test_opinion_trend_sizer.py (Step 4·5) | module-1,2,3 | S2 |
| **module-5** | community_memory.py, opinion_reflection.py, test_community_memory_reflection.py (Step 6) | module-1,4 | S3 |
| **module-6** | reddit_backtester.py + reddit_portfolio.py memory/reflection 배선 (Step 7) | module-2,4,5 | S3 |
| **module-7** | decision_router.py(rule-based) + test_decision_router.py (Step 8) | module-2,4,5 | S4 |
| **module-8** | decision_router.py(LLM, OFF) + test_llm_decision_router_schema.py (Step 9) | module-7 | S4 |
| **module-9** | reddit_backtester.py universe/cost/router metric + 비교표 (Step 10) | module-6,7 | S5 |
| **module-10** | main.py(`--universe`/`--llm-router`) + scripts/regression_check_reddit.py (Step 11) | module-9 | S5 |

**권장 세션 분할**: S1(module-1,2) → S2(module-3,4) → S3(module-5,6) → S4(module-7,8) → S5(module-9,10). 각 세션 끝에 pytest + equal 회귀 확인.

**검증 명령**: Plan §8 참조.
