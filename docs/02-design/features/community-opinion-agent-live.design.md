# Design: Community Opinion Agent — Live (KIS 모의투자 배선)

**Feature**: community-opinion-agent-live
**Date**: 2026-06-01
**Status**: Design
**Branch**: `SentiQuant_Final`
**Architecture**: Option C — Pragmatic (community_live 드라이버 + 순수 helper, reddit_backtester 불가침)
**Plan**: `docs/01-plan/features/community-opinion-agent-live.plan.md`

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 백테스트 검증 에이전트를 KIS 모의투자로 실구동해 forward 데이터·reflection 축적, 전략 실증. |
| **WHO** | 운영자(본인) — 매일 에이전트 구동, decision log·reflection 누적. |
| **RISK** | 미검증 전략 자동매매 → **dry-run 기본 + 실자금 차단(FR-20)**. 뉴스 경로 교체 → **LIVE_STRATEGY 스위치 가역**. LLM 비용 → 일일 상한+fallback. 백테스트 회귀 → `reddit_backtester` 불가침. |
| **SUCCESS** | `--agent-run-now` 동작 / decision log 영속 / dry-run 실주문 0 / 모의주문 모드 place_order 호출 / LLM 토글 / 뉴스↔에이전트 가역 / 회귀 0 / 테스트 통과. |
| **SCOPE** | 신규: `community_live.py`·`agent_gate.py`·테스트. 수정: `main.py`·`scheduler.py`·`config.py`. 불가침: `signals.py`·`backtester.py`·`reddit_backtester.py`·뉴스 포트폴리오. |

---

## 1. Overview

매일 1회 에이전트를 라이브로 구동하는 **`community_live.run_live()`** 드라이버를 추가한다. 후보별 평가(snapshot→universe→cost→memory→router→DecisionResult)는 신규 **순수 helper `agent_gate.evaluate_candidate()`** 로 분리해 라이브가 호출한다. `reddit_backtester`는 수정하지 않아 **백테스트 회귀 0**이며, 동일 5모듈(universe/cost/memory/reflection/router)을 재사용한다. action==BUY/SELL/EXIT는 `kis_broker.place_order`(해외주식 모의)에 연결하되 **`--dry-run` 기본**으로 주문 의도만 로그한다. 실매매 전략은 `config.LIVE_STRATEGY`로 뉴스↔에이전트 가역 전환한다.

**설계 원칙**
- `reddit_backtester`·`signals.py`·`backtester.py` 불가침 → 회귀 0
- dry-run 기본, 실자금 차단(FR-20) 불변
- 라이브는 **영속 상태**(score_history·memory jsonl·portfolio state) — 백테스트의 run-local과 분리
- LLM은 보조 라우터(ON), 일일 호출 상한 + fallback

---

## 2. Architecture (Option C)

```
scheduler.order_processing_job(dry_run)
   └ if config.LIVE_STRATEGY == "agent":  community_live.run_live(dry_run, llm_router)
     else (=="news"):                     기존 signals.py 뉴스-RSI 경로 (불변)

community_live.run_live(date=today, dry_run=True, llm_router=None)
   1. posts = RedditCollector.load_posts(today)  (또는 collect)
   2. history = wsb_state.load_score_history()    [영속]
   3. top_n, signal_details = WSBSignalEngine.run_pipeline(...)
   4. for sym in 후보:
        decision, order_intent = agent_gate.evaluate_candidate(   ← 신규 공용 helper
            sym, scored, history, ohlcv, account_equity,
            universe_filter, cost_filter, memory, router, llm_router)
        decision_log.append_decision_log(record, path=live)        [영속]
   5. 주문 실행: OrderExecutor
        dry_run → 의도만 로그 / else → kis_broker.place_order(paper)
   6. RedditPortfolio(live) 상태 저장 + check_exit 5단계
   7. (익일) forward 확정분 → reflection (decision_id join)

agent_gate.evaluate_candidate(...)  [순수 — 백테스트도 채택 가능, 이번 범위는 live만]
   build_daily_snapshot → UniverseFilter.decide → CostAwareTradeFilter.evaluate
   → memory.retrieve_* → DecisionRouter.decide → (DecisionResult, OrderIntent)
```

**기존 경로**: `LIVE_STRATEGY="news"` → 한 줄도 안 바뀜. 백테스트(`reddit_backtester`) → 불변.

---

## 3. 모듈 명세

### 3.1 agent_gate.py (신규, 순수)
```python
@dataclass
class OrderIntent:
    symbol: str; side: str            # BUY | SELL | REDUCE | HOLD | SKIP
    shares: int; size_factor: float
    decision_id: str; reason: str

def evaluate_candidate(*, symbol, scored_entry, history, ohlcv, account_equity,
                       open_price, universe_filter, cost_filter, memory, router,
                       current_position=None, run_meta: dict) -> tuple[DecisionResult, OrderIntent]:
    """후보 1건 평가: snapshot→universe→cost→memory→router→DecisionResult + OrderIntent.
       reddit_backtester._agent_gate와 동일 의사결정(중복 최소·향후 공용화 여지)."""
```
- 사이징: `shares = floor(account_equity × EQUAL_POSITION_PCT × decision.size_factor / open_price)`.
- 순수 함수(부수효과 X) → 단위테스트 용이. decision_id = `make_decision_id(...)`.

### 3.2 community_live.py (신규, 드라이버)
```python
def run_live(date: str = None, dry_run: bool = True, llm_router: bool = None,
             universe_mode: str = None) -> dict:
    """라이브 1일 구동 → {decisions, orders, decision_log_path, summary}."""
```
- 영속 상태: `wsb_state.load_score_history/append_daily_snapshot`, `CommunityMemoryStore()`(파일 backend), `RedditPortfolio(strategy_key="agent_live")`.
- LLM 일일 상한: `COMMUNITY_LLM_LIVE_MAX_CALLS` 초과 시 router를 rule-only로 강등.
- OrderExecutor: dry_run이면 `logger.info(주문의도)` + 반환만, 아니면 `kis_broker.place_order(symbol, side, shares)`.

### 3.3 수정 모듈
| 파일 | 변경 |
|------|------|
| `config.py` | `LIVE_STRATEGY="agent"`, `COMMUNITY_LLM_LIVE_MAX_CALLS=50`, `COMMUNITY_LIVE_UNIVERSE_MODE`(기본 community_liquid) |
| `main.py` | `--agent-run-now`(action), `--dry-run` 기본 True + `--no-dry-run`, `--llm-router`/`--universe` 재사용 → `community_live.run_live()` |
| `scheduler.py` | `order_processing_job`/신호 잡에서 `LIVE_STRATEGY` 분기 (agent→run_live, news→기존) |

---

## 4. 데이터 스키마 / 경로
- decision log(live): `data/community/live/decisions.jsonl` (`decision_log_path(live=True)`)
- snapshot(영속): `data/community/daily_opinion_snapshots.jsonl`
- memory(영속): `data/community/memory/*.jsonl`
- portfolio(live): `data/reddit/{date}/portfolio_state_agent_live.json`
- OrderIntent: §3.1. KIS 주문: 기존 `kis_broker.place_order(symbol, "BUY"|"SELL", shares)` 재사용.

---

## 5. 회귀 격리 (최상위)
```
LIVE_STRATEGY="news"   → scheduler가 기존 signals.py 호출 (byte 동일, 회귀 0)
reddit_backtester.py   → 미수정 (백테스트 회귀 0)
community_live/agent_gate → 신규 파일만 (기존 경로 비침습)
dry_run=True(기본)     → place_order 호출 0 (실모의주문 없음)
KIS_PAPER_TRADING=true → 실전 도메인 connect 차단 (FR-20, 불변)
```

---

## 6. Test Plan (tests/test_community_live.py)
- evaluate_candidate: BUY/SKIP/HOLD OrderIntent 생성, 사이징 정확
- dry_run=True → mock broker.place_order 호출 0
- dry_run=False → place_order(paper) 호출 (mock_broker)
- decision log(live) 영속 저장 (BUY/SKIP/HOLD)
- LLM 일일 상한 초과 → rule fallback
- `LIVE_STRATEGY="news"` → community_live 미호출 (회귀)
- 현금 부족·종목 불가 → skip
- 기존 전체 테스트 회귀 0

---

## 7. 리스크 (Plan §6) + 대응
미검증 자동매매→dry-run 기본/모의전용/검증게이트 · 실자금→FR-20 · LLM 비용→일일상한+fallback · 뉴스교체→LIVE_STRATEGY 가역 · 영속상태 오염→live 전용경로.

---

## 8. Implementation Guide

### 8.3 Session Guide (Module Map — `/pdca do --scope`)
| module key | 파일 | 작업 |
|-----------|------|------|
| **module-1** | config.py | LIVE_STRATEGY·LLM 상한·live universe |
| **module-2** | agent_gate.py + test | evaluate_candidate(순수) + OrderIntent |
| **module-3** | community_live.py | run_live 드라이버 + OrderExecutor(dry-run) + 영속상태 |
| **module-4** | main.py | --agent-run-now / --dry-run 기본 / --no-dry-run |
| **module-5** | scheduler.py | LIVE_STRATEGY 분기 (agent↔news 가역) |
| **module-6** | tests/test_community_live.py | dry-run 실주문0·로그·상한·회귀 |

**권장 순서**: module-1 → 2 → 3 → 4 → 5 → 6. 각 단계 후 pytest + (mock broker) dry-run 확인.

**핵심 제약**: dry-run 기본 · 실자금 차단 · 뉴스/백테스트 회귀 0 · reddit_backtester 불가침 · LLM 보조 라우터.
