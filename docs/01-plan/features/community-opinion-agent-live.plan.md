# Plan: Community Opinion Agent — Live (KIS 모의투자 배선)

**Feature**: community-opinion-agent-live
**Date**: 2026-06-01
**Status**: Plan
**Branch**: `SentiQuant_Final`
**Base**: `community-opinion-agent`(백테스트 검증 완료, Match Rate 98%)의 라이브 전환

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | community-opinion-agent(universe/cost/memory/router/opinion_trend)는 **백테스트(`RedditReplayBacktester`)에만** 배선돼 있다. 실매매 경로(`scheduler→signals.py(뉴스-RSI)→trader→kis_broker`)는 NVDA/TSLA 2종목을 뉴스 감성으로만 매매하며, 에이전트는 단 한 줄도 호출되지 않는다. |
| **Solution** | 매일 1회 에이전트 파이프라인을 라이브로 구동하는 **`community_live.py` 드라이버**를 추가한다: 오늘 Reddit 수집 → snapshot → Universe/Cost/Memory/**LLM Router(ON)** → DecisionResult → **KIS 모의투자 주문**(dry-run 기본). 실매매 신호 소스를 뉴스-RSI에서 **에이전트로 교체**(config 스위치로 가역). 모든 판단은 `data/community/live/decisions.jsonl`에 영속 저장. |
| **Function/UX Effect** | `python main.py --agent-run-now [--dry-run] [--llm-router]` → 오늘 후보별 판단·주문의도 출력 + decision log 저장. 크론(16:30 신호 / 09:35 주문)에 연결. dry-run 기본이라 실제 모의주문은 명시적으로 켤 때만. |
| **Core Value** | 백테스트에서 검증한 여론 에이전트를 **KIS 모의투자에서 실제로 돌려** 데이터를 쌓고(forward), 판단→결과(reflection)를 누적해 전략을 검증·개선하는 라이브 루프 확보. 실자금은 FR-20으로 차단(모의 전용). |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 백테스트는 8~17일 소표본·청산 0건으로 성과 미검증. 라이브(모의) 루프를 돌려야 forward 데이터·reflection이 쌓이고 전략을 실증할 수 있다. 에이전트가 실제 주문 경로에 연결돼야 "검증된 전략"으로 승격 가능. |
| **WHO** | 운영자(본인) — KIS 모의투자로 에이전트를 매일 구동, decision log·reflection 축적. |
| **RISK** | ① 전략 미검증 상태 자동매매 → **dry-run 기본 + KIS_PAPER_TRADING(실자금 차단, FR-20)** 으로 격리. ② 라이브는 인메모리 아닌 **영속 상태**(score_history·memory·position) → 결정성 대신 정확성. ③ LLM ON → 비용·지연·실패 → fallback + 일일 호출 상한. ④ 뉴스 경로 교체 → **config 스위치로 가역**(되돌리기 가능). ⑤ KIS 해외주식 모의 주문 가능 종목·시간(미국장)·체결 지연. |
| **SUCCESS** | (1) `--agent-run-now` 동작(후보→판단→주문의도) (2) decision log 영속 저장 (3) dry-run에서 실주문 0 (4) 모의주문 모드에서 KIS place_order(paper) 호출 (5) LLM ON/OFF 토글 (6) 뉴스↔에이전트 스위치 가역 (7) 기존 뉴스/백테스트 회귀 0 (8) 테스트 통과. |
| **SCOPE** | 신규: `community_live.py`(라이브 드라이버) + 테스트. 수정: `main.py`(`--agent-run-now`), `scheduler.py`(잡 연결), `config.py`(LIVE_STRATEGY 스위치·일일 LLM 상한). **불가침**: `signals.py`·`backtester.py`·`reddit_backtester.py`(백테스트 로직)·뉴스 포트폴리오. 에이전트 5모듈은 **재사용**(수정 최소). |

---

## 1. 핵심 결정 (Checkpoint 확정)

| # | 결정 | 내용 |
|---|------|------|
| D1 | **자동화 수준** | KIS **모의투자 자동주문**, 단 **`--dry-run` 기본 ON**. 실주문(모의)은 `--no-dry-run` 또는 명시 플래그로만. 실자금은 FR-20(KIS_PAPER_TRADING)로 원천 차단. |
| D2 | **신호 소스 교체** | 실매매 전략을 뉴스-RSI → 에이전트로 교체. `config.LIVE_STRATEGY = "agent" | "news"`(기본 agent, 가역). order 잡이 이 스위치로 분기. |
| D3 | **LLM 라우터** | 라이브 **ON**(사용자 선택). `--llm-router` 또는 config flag. 일일 LLM 호출 상한(`COMMUNITY_LLM_LIVE_MAX_CALLS`)으로 비용 가드 + 실패 시 rule fallback. |
| D4 | **시작 시점** | 지금 배선. **dry-run 기본**으로 매일 수집+판단을 쌓고, 검증 후 모의 실주문 전환. |
| D5 | **상태 영속성** | 라이브는 백테스트의 run-local/인메모리 대신 **영속 상태** 사용: `score_history.json`(기존), `data/community/memory/*.jsonl`(누적), `data/community/live/decisions.jsonl`, RedditPortfolio state. 결정성보다 실제 누적 우선. |
| D6 | **드라이버 분리** | 백테스트 `RedditReplayBacktester`는 불가침. 라이브는 **신규 `community_live.py`** 가 동일 5모듈을 오케스트레이션(코드 중복 최소화 위해 게이팅 로직은 공용 helper로 추출 검토). |
| D7 | **주문 연결** | 에이전트 DecisionResult(action/size_factor) → 주문 의도 → `trader`/`kis_broker.place_order`(해외주식 모의). 사이징은 KIS 계좌 equity × opinion_trend size_factor. |

---

## 2. 라이브 데이터 흐름 (설계 골격)

```
community_live.run_live(date=today, dry_run=True, llm_router=True)
  1. RedditCollector.collect(today)            오늘 Reddit 수집 (또는 --reddit-run-now 결과 로드)
  2. score_history(영속) 로드 + 누적           opinion_history = wsb_state.load_score_history()
  3. WSBSignalEngine.run_pipeline()            top_n + signal_details (신호 엔진 불변)
  4. 후보별:
       build_daily_snapshot()                  + append_daily_snapshot(영속)
       UniverseFilter.decide(price/거래대금=collector 실시간/캐시)
       CostAwareTradeFilter.evaluate(ATR=indicators)
       CommunityMemoryStore.retrieve_*(영속 jsonl)   라이브는 과거 누적 조회
       DecisionRouter.decide(llm_router=ON)
       append_decision_log(live 경로)
  5. action==BUY:
       KIS 계좌 equity 조회(get_account) → opinion_trend size_factor로 shares
       dry_run? 주문의도 로그 : kis_broker.place_order(paper)
     action==SELL/EXIT/REDUCE: 보유분 청산/축소 (check_exit 5단계 유지)
  6. 포지션/상태 저장 (RedditPortfolio live state)
  7. 익일 이후: forward return 확정분에 LowLevelReflection, 청산분에 HighLevelReflection (decision_id 연결)
```

**기존 경로(불변)**: `LIVE_STRATEGY="news"`면 기존 `signals.py` 뉴스-RSI 그대로. 백테스트도 그대로.

---

## 3. 기능 요구사항

| ID | 요구사항 |
|----|----------|
| FR-01 | `community_live.py` 신규: `run_live(date=None, dry_run=True, llm_router=None) -> dict`. 위 §2 흐름. 에이전트 5모듈 재사용. |
| FR-02 | `main.py --agent-run-now` 추가 (`--dry-run` 기본 True, `--no-dry-run`으로 실모의주문, `--llm-router` 토글, `--universe` 재사용). |
| FR-03 | `scheduler.py`: `LIVE_STRATEGY=="agent"`이면 신호/주문 잡이 `community_live.run_live`로 분기. `"news"`면 기존 경로(회귀 0). |
| FR-04 | KIS 모의 주문 연결: DecisionResult.action/size_factor → `trader`/`kis_broker.place_order`(해외주식 paper). dry-run이면 place_order 미호출(의도만 로그). |
| FR-05 | decision log 영속: `append_decision_log(record, path=decision_log_path(live=True))`. 모든 후보(BUY/SKIP/HOLD/...) 저장. |
| FR-06 | LLM 라이브 가드: 일일 LLM 호출 상한(`COMMUNITY_LLM_LIVE_MAX_CALLS`) 초과 시 rule fallback. API 실패 시 fallback(기존). |
| FR-07 | config: `LIVE_STRATEGY`(기본 "agent"), `COMMUNITY_LLM_LIVE_MAX_CALLS`, 라이브 universe 기본값. |
| FR-08 | 사이징: KIS `get_account` equity × `EQUAL_POSITION_PCT` × opinion size_factor / open_price. 현금 부족·종목 불가 시 skip. |
| FR-09 | reflection 라이브: forward return 확정(N일 경과) snapshot·청산 trade에만 생성, decision_id로 join. |

### NFR
| ID | 요구사항 |
|----|----------|
| NFR-01 | **dry-run 기본** — 명시적으로 끄기 전엔 실모의주문 0. |
| NFR-02 | **실자금 차단** — KIS_PAPER_TRADING=true, 실전 도메인 FR-20 차단 유지. |
| NFR-03 | 기존 뉴스-RSI(`signals.py`)·백테스트(`reddit_backtester.py`) **회귀 0** (LIVE_STRATEGY 스위치·신규 드라이버로 격리). |
| NFR-04 | 급등추격 금지·LLM 보조 라우터·5단계 청산·profit target OFF 유지. |

---

## 4. 변경/신규 파일

| 파일 | 유형 | 변경 |
|------|------|------|
| `community_live.py` | 신규 | 라이브 드라이버 `run_live()` |
| `main.py` | 수정 | `--agent-run-now` / `--dry-run` 기본 / `--no-dry-run` |
| `scheduler.py` | 수정 | `LIVE_STRATEGY` 분기 (agent ↔ news) |
| `config.py` | 수정 | `LIVE_STRATEGY`, `COMMUNITY_LLM_LIVE_MAX_CALLS` |
| `tests/test_community_live.py` | 신규 | dry-run 실주문 0·decision log 저장·스위치 가역·회귀 |

**불가침**: `signals.py`·`backtester.py`·`reddit_backtester.py`·뉴스 포트폴리오.

---

## 5. 성공 기준

| SC | 기준 | 검증 |
|----|------|------|
| SC-01 | `--agent-run-now` 동작 (후보→판단→주문의도) | CLI |
| SC-02 | dry-run에서 `place_order` 호출 0 | 테스트(mock broker) |
| SC-03 | `--no-dry-run` 모의주문 모드에서 place_order(paper) 호출 | 테스트(mock broker) |
| SC-04 | decision log(live) 영속 저장 — BUY/SKIP/HOLD 모두 | 테스트 |
| SC-05 | LLM ON/OFF 토글 + 일일 상한 작동 | 테스트 |
| SC-06 | `LIVE_STRATEGY="news"` → 기존 뉴스 경로 회귀 0 | 테스트 |
| SC-07 | 실자금 차단 유지 (실전 도메인 connect 차단) | 기존 FR-20 |
| SC-08 | 신규 테스트 + 기존 전체 통과 | pytest |

---

## 6. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 미검증 전략 자동매매 | **높음** | dry-run 기본 + 모의 전용 + 검증 게이트 |
| 실자금 사고 | 치명 | FR-20(KIS_PAPER_TRADING) 실전 차단 — 불변 |
| LLM 비용/지연/실패 | 중 | 일일 호출 상한 + rule fallback + dry-run시 LLM도 선택적 |
| 뉴스 경로 교체로 기존 동작 깨짐 | 중 | LIVE_STRATEGY 스위치(가역), 기본 외 분기 격리 |
| 라이브 영속상태 오염 | 중 | live 전용 경로(data/community/live/), 백테스트와 분리 |
| 미국장 시간/체결 | 낮음 | 기존 scheduler ET 스케줄 재사용 |

---

## 7. 구현 순서 (예정)

1. config: LIVE_STRATEGY·LLM 상한
2. community_live.py: run_live (dry-run 기본, decision log, 에이전트 재사용)
3. main.py: --agent-run-now
4. scheduler.py: LIVE_STRATEGY 분기
5. KIS 주문 연결 (trader/kis_broker, mock으로 테스트)
6. tests/test_community_live.py (dry-run 실주문 0·스위치 회귀)
7. 검증: dry-run 실행 + 회귀 + (선택) 모의주문 1회

---

## 8. 가장 중요한 제약

- **dry-run 기본** — 명시 전엔 실모의주문 없음.
- **실자금 차단 불변** (FR-20).
- 기존 뉴스-RSI·백테스트 **회귀 0** (LIVE_STRATEGY 스위치).
- 급등추격 금지·LLM 보조 라우터·5단계 청산·profit target OFF.
- 에이전트 5모듈은 재사용(수정 최소), 백테스트 로직 불가침.
