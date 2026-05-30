# Report: Community Opinion Agent — 커뮤니티 여론 의사결정 에이전트 (v0~v3)

**Feature**: community-opinion-agent
**Date**: 2026-05-30
**Phase**: Report (완료)
**Match Rate**: 98% · **Success Criteria**: 11/11 · **Iteration**: 1
**Branch**: `community-opinion-trend-sizing`

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 기존 여론 전략은 유동성·비용·글품질·티커오탐을 거르지 못하고, 과거 사례 학습과 구조화된 판단 근거가 없어 좋은 여론도 비용·노이즈에 묻혔다. |
| **Solution** | WSB V3 rule 신호를 1차 후보로 유지하고 ⓪Universe/Cost 게이팅 → v1 source quality·ambiguity·DailyOpinionSnapshot → v2 Memory·Reflection → v3 DecisionRouter(LLM 보조, 기본 OFF)를 독립 5모듈로 추가. backtester가 오케스트레이션. |
| **Function/UX Effect** | `--universe {6모드}` + `--llm-router`로 gross/net 동시 출력, 비용·turnover·skip 카테고리·router action 분포 비교표 제공. equal+필터 OFF → 회귀 0. |
| **Core Value** | 유동성·비용·합의 지속성·과거 성공에 베팅하고 판단을 학습·기록하는 여론 의사결정 인프라. 급등추격 아님. |

### Value Delivered (실측)
| 지표 | 결과 |
|------|------|
| Universe 모드별 차별화 | sp500_only 1 / community_liquid 4 / liquid_us 2 trades (오프라인 e2e) |
| gross vs net | 동시 출력 + commission/slippage/cost_to_gross_profit_ratio |
| 라우터 | rule-based + LLM(OFF) + 8 안전장치, avg_confidence ~0.82, avg_memory_hits 3.0 |
| 회귀 | equal 결정성 True, regression_check_reddit.py exit 0 |
| 테스트 | 신규 73 + 기존 13 = **86건 통과** |
| Match Rate | **98%** (Critical 0, Important 0) |

---

## 1. 구현 산출물

### 신규 파일 (8 + 테스트 6 + 데이터 2)
| 파일 | 역할 |
|------|------|
| `universe_filter.py` | UniverseFilter / UniverseDecision / load_universe_sets |
| `cost_aware_trade_filter.py` | CostAwareTradeFilter / CostAwareTradeDecision |
| `community_memory.py` | MemoryBackend(ABC)·JsonlMemoryStore·**InMemoryBackend**·CommunityMemoryStore |
| `opinion_reflection.py` | Low/HighLevelReflection + build_low/high_level |
| `decision_router.py` | DecisionRouter·DecisionResult·LLMRouter·LLMDecisionResult·8 안전장치 |
| `scripts/regression_check_reddit.py` | equal 회귀 검출 (exit 1) |
| `data/universe/{sp500,nasdaq100}.json` | 정적 index 시드 (105/71) |
| `tests/` ×6 | universe·cost·snapshot·sizer·memory·router·llm |

### 수정 파일 (8)
config(COMMUNITY_*) · reddit_collector(flair/ambiguity) · wsb_signal_engine(DailyOpinionSnapshot) · wsb_state(snapshot jsonl) · position_sizer(+3 factor) · reddit_portfolio(entry 보강) · reddit_backtester(오케스트레이션·metric) · main(--universe/--llm-router)

### 불가침 준수
`signals.py` · `backtester.py` · 뉴스 모델/포트폴리오 **무수정**

---

## 2. Key Decisions & Outcomes

| 결정 | 채택 | 결과 |
|------|------|------|
| Architecture | Approach A (backtester 오케스트레이션 + 독립 모듈) | ✅ equal 회귀 안전, 모듈 단위테스트 용이 |
| 구조 | Option C — Pragmatic (순수 5모듈) | ✅ 향후 라이브 오케스트레이터 추출 여지 |
| D1 Universe 데이터 | 정적 JSON + OHLCV 유동성, 시총 선택 | ✅ 오프라인 결정성 |
| D2 config | COMMUNITY_* + WSB_OPINION_* alias | ✅ 단일소스 (HIGH_ATTENTION 1.1 유지) |
| D3 LLM | 인터페이스+스키마+안전장치+fallback, 실호출 OpenAI, 기본 OFF | ✅ OFF시 호출 0 검증 |
| D5 Sizer | in-place +3 factor (기본 1.0) | ✅ 기존 T1~T12 회귀 0 |
| D7 회귀 격리 | 게이팅 opinion_trend 한정 | ✅ regression_check |
| Act: 메모리 결정성 | run-local InMemoryBackend (전역 미조회) | ✅ opinion_trend 결정성 + 조회 활성 |

---

## 3. Success Criteria Final Status

| SC | 기준 | 상태 |
|----|------|------|
| SC-01 | --universe 6모드 | ✅ Met |
| SC-02 | gross/net + 비용 metric | ✅ Met |
| SC-03 | source/ambiguity weight·skip | ✅ Met |
| SC-04 | snapshot jsonl | ✅ Met |
| SC-05 | memory + reflection | ✅ Met |
| SC-06 | router rule + 8 안전장치 + LLM fallback | ✅ Met |
| SC-07 | **신규 필터 OFF + equal 회귀 0** | ✅ Met |
| SC-08 | size ≤1.3 / NEW_SPIKE 축소 | ✅ Met |
| SC-09 | LLM OFF → 호출 0 | ✅ Met |
| SC-10 | universe별 비교 가능 | ✅ Met |
| SC-11 | 신규 7종 + 기존 pytest 통과 | ✅ Met (86건) |

**Overall: 11/11 (100%)**

---

## 4. 기존 WSB V3 / trend-sizing 대비 달라진 점
- 거래 대상: 전 종목 → universe tier 게이팅 + size_multiplier
- 비용: gross만 → gross/net 동시 + cost-aware SKIP
- 글품질/티커: denylist만 → flair weight + ambiguity `$` 강제
- 학습: 없음 → memory + low/high reflection (run-local 결정성)
- 의사결정: 단일 rule → DecisionRouter(rule 기본 + LLM explainer, 8 안전장치)
- 불변: 5단계 청산, profit target OFF, NEW_SPIKE 보수, equal 회귀 0

## 5. Universe 비교 방법
동일 기간·모델·ranking·sizing 고정 후 `--universe`만 바꿔 4회 실행 →
gross/net·turnover·cost_to_gross_profit_ratio·trades_skipped_by_* 비교표로
sp500_only(고유동·저오탐) vs community_liquid(관심종목 포함) vs liquid_us vs sp500_nasdaq100 trade-off 확인.

## 6. 잔여 한계 (문서화·수용)
- G3 HIGH_ATTENTION 1.1 alias (스펙 1.05) — D2 단일소스
- G4 replay ATR 부재 → cost filter 변동성 proxy (라이브는 ATR)
- G5 universe 시드 리스트(105/71) — 편집 가능
- G6 실데이터 FinBERT 백테스트는 OHLCV 캐시 기간 또는 API 키로 직접 실행 (세션은 stub로 오프라인 검증)

## 7. 후속 권장
- 실데이터 FinBERT 백테스트로 universe 모드별 net return 실측
- LLM router 모델: **gpt-5.4-mini 유지** 권장 (구조화 JSON 결정에 충분, full은 ROI 낮음)
- factor 수치 grid search, 네이버 종토방 수집기, vector DB(Chroma/Faiss) 실연동 (별도 사이클)
