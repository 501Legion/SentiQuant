# Design: Daily Decision Report

**Feature**: daily-decision-report
**작성일**: 2026-06-06
**선택 아키텍처**: **Option C — 실용 균형** (`ReportContext` dataclass + 순수 Markdown 포매터, `run_pipeline` 무수정)
**상위 Plan**: `docs/01-plan/features/daily-decision-report.plan.md`

---

## Context Anchor (Plan 승계)

| 항목 | 내용 |
|------|------|
| **WHY** | 매수 0건이 "정상(여론 약함)"인지 "버그"인지 매일 사람이 분석하는 비용 제거. 판단 funnel을 영속·가시화. |
| **WHO** | 운영자(본인) — 매일 보고서 1개로 의사결정 과정 리뷰. |
| **RISK** | run_live 본 로직 침습 → 보고서 실패가 매매를 막으면 안 됨(예외 격리). 기존 decision_log/회귀 0. |
| **SUCCESS** | run_live 끝에 보고서 자동 생성 / funnel 단계별 탈락 종목·사유 / 매수·매도 사유 / 콘솔 요약 / 실패해도 매매 무영향 / 테스트 통과. |
| **SCOPE** | 신규: `decision_report.py`·테스트. 수정: `community_live.py`(ReportContext 조립+호출). 불가침: 매매 판단 로직·`run_pipeline`·`reddit_backtester`·뉴스 경로. |

---

## 1. Overview

`run_live` 종료 직전(주문 실행·요약 완료 시점, `community_live.py:483` 직전), 메모리에 이미 존재하는
판단 데이터(`signal_details`·`decisions`·`orders`·`snap_by_key`·`summary`)를 **순수 포매터**에 넘겨
`data/community/live/reports/YYYY-MM-DD.md` 1개를 생성하고 콘솔에 한 줄 요약을 출력한다.

**핵심 통찰 (현 코드 분석)**:
1. `run_pipeline`의 `signal_details`가 **이미 funnel 플래그를 종목별로 노출** —
   `neutral_filtered`·`passed_consensus`·`signal`·`in_top_n`·`bullish`/`bearish`/`neutral`/`score`
   (`wsb_signal_engine.py:319-341`). → **run_pipeline 수정 불필요.**
2. `decisions`(action/size_factor/decision_id/router_mode)와 `orders`(side/executed)가
   `run_live` 스코프에 수집됨(`community_live.py:359·404·449`). `snap_by_key[(sym,date)]`에
   스냅샷 보유(`:386`).
3. `decision_log`가 종목별 최종 사유(`final_action`·`reason_codes`·`universe_reason_codes`·
   `cost_reason_codes`)를 영속(`decision_log.py:70-112`). 게이트 탈락 사유의 1차 출처.
4. 보고서는 **판단을 재계산하지 않고** 기존 결과만 포맷 → read-only, 비침습(NFR-03).

---

## 2. Selected Architecture — Option C

```
run_live (community_live.py)
  … 주문 실행 + summary 생성 (기존, 불변) …
  └─ [신규] ReportContext 조립(in-memory) → build_daily_report(ctx)  # try/except 격리
        ├─ _derive_funnel(signal_details, decisions, orders)   # 순수: 단계별 종목·사유
        ├─ _format_markdown(ctx, funnel) -> str                # 순수: MD 본문
        ├─ 파일 저장 reports/YYYY-MM-DD.md (덮어쓰기)
        └─ _console_summary(funnel) -> str (logger.info 1줄)
  └─ return dict + "report_path"                               # 반환 보강(하위호환)
```

**왜 C인가**: `decision_report`를 **입력 dataclass→문자열 순수 함수**로 만들어 테스트가 쉽고(SC-08),
funnel은 기존 in-memory 데이터로 채워 `run_pipeline` 무수정(NFR-02). FR-07(과거 재생성)은
`ReportContext`를 나중에 디스크(decision_log)에서 재구성하는 길만 열어두고 본 사이클은 deferred.
A(직접 결합)의 테스트 난이도와 B(signal_details 영속 신설)의 작업량을 모두 회피.

---

## 3. 모듈 분해 (Module Map)

| 모듈 | 파일 | 책임 | --scope 키 |
|------|------|------|-----------|
| **M1 Report core** | `decision_report.py` (신규) | `ReportContext` dataclass + `_derive_funnel` + `_format_markdown` + `_console_summary` + `build_daily_report`(저장). 순수·read-only | `module-1` |
| **M2 Live wiring** | `community_live.py` (수정) | `return` 직전 ReportContext 조립 + `build_daily_report` 호출(try/except) + 콘솔 1줄 + 반환 dict에 `report_path` | `module-2` |
| **M3 Tests** | `tests/test_decision_report.py` (신규) | funnel 도출·매수/매도/탈락 사유·비침습(mock raise)·콘솔 포맷·빈 입력 | `module-3` |
| **M4 Regen (선택)** | `main.py` (수정, deferred) | `--decision-report [YYYY-MM-DD]`: decision_log에서 ReportContext 재구성 (FR-07) | `module-4` |

---

## 4. 핵심 설계 결정

| ID | 결정 | 내용 |
|----|------|------|
| **D1** | run_pipeline 무수정 | funnel은 기존 `signal_details` 플래그로 도출(Plan D5 갱신). 시그니처 불변 → 회귀 0(NFR-02). |
| **D2** | 순수 포매터 | `_derive_funnel`/`_format_markdown`은 부수효과 없는 순수 함수. 저장만 `build_daily_report`가 담당 → 테스트는 문자열 검증. |
| **D3** | 비침습 격리 | `community_live`가 `build_daily_report`를 **try/except**로 감싼다. 예외 시 `logger.warning`만, run_live 반환·주문 무영향(NFR-01/SC-06). |
| **D4** | funnel 3출처 결합 | ① 중립/컨센 탈락 = `signal_details` 플래그. ② 게이트 탈락 = `decisions`의 `final_action`(SKIP/HOLD) + `decision_log` `reason_codes`. ③ 체결 = `orders`. |
| **D5** | 보유-only 종목 처리 | 오늘 게시글 없는 보유 포지션(`scored`에 없음, `:368` continue)은 funnel "입력"에 없으나 매도/EXIT는 `orders`로 포착 → 보고서 "매도" 섹션에 별도 표기. |
| **D6** | 덮어쓰기 | 동일 날짜 재구동 시 `reports/YYYY-MM-DD.md` 덮어쓰기(FR-06). |
| **D7** | 한국어·표 | 사람이 읽는 한국어 MD 표. 참고 모델: `docs/04-report/community-opinion-agent-live.no-trade-diagnosis.md`. |

---

## 5. 데이터 구조

### 5.1 ReportContext (community_live → decision_report 입력)
```python
@dataclass
class ReportContext:
    date: str
    signal_details: list[dict]          # run_pipeline 출력 (funnel 플래그 보유)
    decisions: list[dict]               # {symbol, action, size_factor, decision_id, router_mode}
    orders: list[dict]                  # executor 결과 {symbol, side, shares, executed, ...}
    snapshots: dict                     # (sym,date)→DailyOpinionSnapshot (score/consensus 보강)
    summary: dict                       # run_live summary (candidates/buys/sells/...)
    decision_records: list[dict] = None # (선택) decision_log 레코드 — 게이트 사유 보강
```

### 5.2 Funnel (decision_report 내부 도출 결과)
```python
{
  "input_n": int,
  "neutral_dropped": [{"symbol", "neutral_ratio"}],         # neutral_filtered=True
  "consensus_dropped": [{"symbol", "bullish", "bearish", "reason"}],  # passed_consensus=False
  "gate_dropped": [{"symbol", "final_action", "reason_codes"}],       # SKIP/HOLD (universe/cost/router)
  "buys": [{"symbol", "score", "consensus_ratio", "size_factor", "shares", "decision_id"}],
  "sells": [{"symbol", "action", "reason", "shares"}],
}
```

---

## 6. 변경 상세 & Open Issues

### 6.1 M1 `decision_report.py` (신규)
- `_derive_funnel(signal_details, decisions, orders, snapshots, decision_records)`:
  - 입력 N = `len(signal_details)`.
  - 중립탈락 = `[d for d in signal_details if d["neutral_filtered"]]`.
  - 컨센탈락 = `[d for d in signal_details if not d["neutral_filtered"] and not d["passed_consensus"]]`.
  - 게이트탈락 = `decisions` 중 `action in {SKIP,HOLD,...}` 이면서 컨센 통과분 → `decision_records`에서
    `reason_codes`/`universe_reason_codes`/`cost_reason_codes` 조인(없으면 action만).
  - 매수 = `orders` side=BUY & executed → snapshot/decision로 score·consensus·size 보강.
  - 매도 = `orders` side=SELL → decisions action(SELL/EXIT/REDUCE) + reason.
- `_format_markdown(date, funnel, summary) -> str`: 한국어 표 섹션(① 입력 ② 중립 ③ 컨센 ④ 게이트 ⑤ 매수 ⑥ 매도).
- `_console_summary(funnel) -> str`: `"입력 N · 중립탈락 a · 컨센탈락 b · 게이트탈락 c · 매수 X · 매도 Y → reports/날짜.md"`.
- `build_daily_report(ctx) -> str`: funnel 도출 → MD 생성 → `data/community/live/reports/{date}.md` 저장 → 경로 반환. 디렉터리 자동 생성.

### 6.2 M2 `community_live.py` (수정, `:483` 직전)
```python
report_path = None
try:
    from decision_report import build_daily_report, ReportContext
    report_path = build_daily_report(ReportContext(
        date=date, signal_details=signal_details, decisions=decisions,
        orders=orders, snapshots=snap_by_key, summary=summary,
    ))
    logger.info(_console_summary_line)   # build_daily_report 내부 logger 또는 반환문자열
except Exception as e:  # noqa: BLE001 — 비침습(D3/NFR-01)
    logger.warning(f"decision report 생성 실패(무시): {e}")
return {..., "report_path": report_path}
```

### 6.3 Open Issues (구현 중 확정)
- **Open-1**: 게이트 탈락 사유를 in-memory `decisions`만으로 충분히 표기 가능한지, 아니면
  `decision_records`(decision_log) 조인이 필요한지. 우선 `decisions`의 action + (가능 시) reason_codes로
  시도, 부족하면 `load_decision_logs(date)` 조인(읽기 전용).
- **Open-2**: 보고서 enable 플래그 — `decision_log`처럼 `config.COMMUNITY_DECISION_REPORT_ENABLED`
  추가 여부(기본 True). dry-run/백테스트에서 비활성 옵션.
- **Open-3**: `data/community/live/reports/` 경로 상수를 config에 둘지(`COMMUNITY_LIVE_REPORTS_DIR`).

---

## 7. Test Plan (M3)

| TC | 시나리오 | 검증 |
|----|----------|------|
| TC-01 | funnel 도출 | signal_details(중립/컨센/통과 혼합) → neutral_dropped/consensus_dropped/gate 정확 분류 |
| TC-02 | 매수 사유 | orders BUY + snapshot → score·consensus·size·shares 표기 |
| TC-03 | 매도 사유 | orders SELL + decision action/reason → 매도 섹션 표기 |
| TC-04 | 게이트 탈락 | 컨센 통과했으나 SKIP/HOLD → gate_dropped에 reason_codes |
| TC-05 | 비침습 | `_format_markdown` mock raise → `build_daily_report` 예외 전파하되, **community_live 호출부 try/except로 run_live 정상**(호출부 단위 테스트 or 함수 계약 명시) |
| TC-06 | 콘솔 요약 | `_console_summary` 형식(입력 N · 중립 a · 컨센 b · 게이트 c · 매수 X · 매도 Y) |
| TC-07 | 빈 입력 | 후보 0/매수 0 → 빈 보고서 아닌 "funnel 탈락 사유 채운" 의미 있는 MD (현 운영 케이스) |
| TC-08 | 파일 저장 | tmp 경로에 MD 생성·재구동 덮어쓰기 |
| TC-09 | 회귀 | 기존 전체 테스트 통과(특히 community_live 10, decision_log 9) |

검증 실행: `python tests/test_decision_report.py` (pytest 미설치 환경 — 단독 러너).

---

## 8. Implementation Guide

### 8.1 구현 순서
1. **M1** `decision_report.py` — ReportContext + _derive_funnel + _format_markdown + _console_summary + build_daily_report.
2. **M2** `community_live.py` `:483` 직전 try/except 호출 + 반환 dict 보강.
3. **M3** `tests/test_decision_report.py` (TC-01~09) + 회귀.
4. **M4(선택, deferred)** `main.py --decision-report` 과거 재생성.

### 8.2 코드 주석 규약
- 파일/모듈: `# Design Ref: §{n} — {결정}`
- 핵심 로직: `# Plan SC: {SC-id}`

### 8.3 Session Guide (--scope)

| 세션 | scope | 모듈 | 선행 |
|------|-------|------|------|
| S1 | `module-1` | M1 report core (순수) | — |
| S2 | `module-2,module-3` | M2 wiring + M3 tests + 회귀 | M1 |
| S3 (선택) | `module-4` | M4 과거 재생성 CLI | M1~M3 |

권장: `/pdca do daily-decision-report --scope module-1` (순수 코어 + 단위테스트 일부) → `--scope module-2,module-3` (배선+통합+회귀).

---

## 9. 영향 범위 요약

- **신규 2파일**: `decision_report.py` / `tests/test_decision_report.py`
- **수정 1파일**: `community_live.py` (`:483` 직전 ~10 LOC, try/except 격리)
- **불가침**: `wsb_signal_engine.run_pipeline`(funnel 기존 노출), 매매 판단 로직, `reddit_backtester`, 뉴스 경로
- **예상 변경량**: ~180–240 LOC (대부분 신규 포매터)
- **config 추가(Open-2/3)**: `COMMUNITY_DECISION_REPORT_ENABLED`, `COMMUNITY_LIVE_REPORTS_DIR` (구현 중 확정)
