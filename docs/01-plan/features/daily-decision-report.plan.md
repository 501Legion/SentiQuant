# Plan: Daily Decision Report — 매일 매수/매도·미체결 사유 자동 보고서

**Feature**: daily-decision-report
**작성일**: 2026-06-05 · **개정**: 2026-06-06 (comment-aware-sentiment 반영: MIN_MENTIONS 1, signal_details funnel 플래그 기존 노출 확인)
**상위 피처**: community-opinion-agent-live (라이브 에이전트)

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 라이브 에이전트가 매일 돌지만 "왜 이 종목을 샀는지 / 왜 아무것도 안 샀는지(어느 관문에서 탈락했는지)"를 매번 사람이 로그를 뒤져 수동 분석해야 함. |
| **Solution** | run_live 종료 시 **퍼널(funnel) 추적 + 종목별 매수/매도/탈락 사유**를 Markdown 보고서로 자동 생성하고 콘솔에 한 줄 요약 출력. |
| **Function UX Effect** | 매일 `data/community/live/reports/YYYY-MM-DD.md` 1개 생성 → 운영자가 열어보면 "오늘 왜 매매했/안했는지" 즉시 파악. |
| **Core Value** | 전략의 판단 과정을 투명·추적 가능하게 만들어, 무행동이 결함인지 정상인지 매일 자동 판별. forward 운영 신뢰도 확보. |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 매수 0건이 "정상(여론 약함)"인지 "버그"인지 매일 사람이 분석하는 비용 제거. 판단 funnel을 영속·가시화. |
| **WHO** | 운영자(본인) — 매일 보고서 1개로 의사결정 과정 리뷰. |
| **RISK** | run_live 본 로직 침습 → 보고서 생성 실패가 매매를 막으면 안 됨(예외 격리). 기존 decision_log/회귀 0. |
| **SUCCESS** | run_live 끝에 보고서 자동 생성 / funnel 단계별 탈락 종목·사유 기록 / 매수·매도 사유 명시 / 콘솔 요약 / 실패해도 매매 무영향 / 테스트 통과. |
| **SCOPE** | 신규: `decision_report.py`·테스트. 수정: `community_live.py`(funnel 수집 + 보고서 호출), `main.py`(`--decision-report` 선택), `wsb_signal_engine`(funnel 단계 노출, 최소). 불가침: 매매 판단 로직·`reddit_backtester`·뉴스 경로. |

---

## 1. 핵심 결정 (Checkpoint 확정)

| ID | 결정 | 내용 |
|----|------|------|
| D1 | **보고 범위** | 매수/매도 사유 **+** 안 산 종목의 **탈락 관문**(funnel) 모두. (사용자 확정: "맞음 — 그대로 진행") |
| D2 | **출력 형식** | **Markdown 파일 + 콘솔 한 줄 요약**. (JSON은 차기) |
| D3 | **실행 방식** | **run_live 종료 시 자동 생성**. `--decision-report [날짜]` 재생성 명령은 선택(차기 가능). |
| D4 | **비침습** | 보고서 생성은 매매 후 read-only 단계. 예외 발생해도 run_live 결과·주문에 영향 0 (try/except 격리). |
| D5 | **funnel 출처** | `run_pipeline`의 `signal_details`가 **이미** 단계별 플래그(`neutral_filtered`·`passed_consensus`·`signal`·`in_top_n`·`labeled_posts`)를 노출(2026-06-06 확인) → `community_live`가 기존 출력 + `decision_log`만으로 funnel 도출. **run_pipeline 추가 확장 불필요 가능**(design에서 확정). |

---

## 2. 데이터 흐름 (설계 골격)

```
run_live
  ├─ run_pipeline → (scored, neutral_overrides, consensus_pass, top_n)   # funnel 단계 결과
  ├─ 후보 평가 → DecisionResult/OrderIntent (+ decision_log 기록, 기존)
  ├─ 주문 실행 (기존)
  └─ build_daily_report(funnel, decisions, orders, snapshots) → reports/YYYY-MM-DD.md  # 신규, read-only
        └─ 콘솔 요약 1줄 출력
```

**Funnel 단계 (보고서 핵심)**:
```
입력 N종목
 → ① 중립필터 통과 (중립비율 ≤ 70%)        [탈락: 종목·중립%]
 → ② 컨센서스 통과 (상승≥하락×1.5, 언급≥1*)  [탈락: 종목·상승/하락/사유]
       * COMMUNITY_MIN_DAILY_MENTIONS=1 (comment-aware-sentiment Act 튜닝, 2026-06-06). 댓글이 N≥10 게이트 충족.
 → ③ universe/cost/router 게이트            [탈락: 종목·reason_code]
 → ④ 최종 BUY/SELL                          [체결: 종목·shares·size·점수]
```

---

## 3. 기능 요구사항

### FR
| ID | 내용 |
|----|------|
| FR-01 | `decision_report.py` 신규: `build_daily_report(date, funnel, decisions, orders, snapshots, summary) -> str(md path)`. Markdown 생성 + 저장. |
| FR-02 | **Funnel 추적**: 입력 종목 수, 중립필터 탈락(종목+중립%), 컨센서스 탈락(종목+상승/하락+사유), universe/cost/router 탈락(종목+reason_code), 최종 매수/매도. |
| FR-03 | **매수 사유**: 종목별 opinion_score·합의비율·size_factor·shares·decision_id. **매도 사유**: action(SELL/EXIT/REDUCE)·사유(check_exit reason 등). |
| FR-04 | `community_live.run_live` 종료 시 `build_daily_report` 자동 호출 (D4: try/except 격리 — 실패해도 run_live 정상 반환). |
| FR-05 | 콘솔 요약 1줄: "입력 N · 중립탈락 a · 컨센탈락 b · 게이트탈락 c · 매수 X · 매도 Y → reports/YYYY-MM-DD.md". |
| FR-06 | 보고서 저장 경로: `data/community/live/reports/YYYY-MM-DD.md`. 동일 날짜 재구동 시 덮어쓰기. |
| FR-07 | (선택) `main.py --decision-report [YYYY-MM-DD]`: 기존 decision_log + funnel 캐시로 과거 날짜 보고서 재생성. *차기 가능, 본 사이클은 자동 생성 우선.* |
| FR-08 | funnel 단계 데이터는 기존 `signal_details`(per-symbol: `neutral_filtered`·`passed_consensus`·`signal`·`in_top_n`) + `decision_log`에서 도출. `run_pipeline` 시그니처 변경 없이 우선 구현, 부족분만 옵셔널 확장(design 확정). |

### NFR
| ID | 내용 |
|----|------|
| NFR-01 | **비침습** — 보고서 생성 실패가 매매/주문/decision_log에 영향 0. |
| NFR-02 | **회귀 0** — 매매 판단 로직·`reddit_backtester`·뉴스 경로 무수정. run_pipeline 확장은 하위호환. |
| NFR-03 | 보고서는 read-only 집계 — 판단을 다시 계산하지 않고 기존 결과만 포맷. |
| NFR-04 | 한국어 보고서 + 사람이 읽기 쉬운 표 형식. |

---

## 4. 변경/신규 파일

| 파일 | 구분 | 변경 |
|------|------|------|
| `decision_report.py` | 신규 | `build_daily_report()` + Markdown 포매터 |
| `tests/test_decision_report.py` | 신규 | funnel→보고서, 매수/매도/탈락 사유, 비침습(예외 격리) |
| `community_live.py` | 수정 | funnel 수집 + run_live 종료 시 보고서 호출(try/except) + 콘솔 요약 |
| `wsb_signal_engine.py` | 수정 불필요 가능 | `signal_details`가 이미 funnel 플래그 노출(2026-06-06 확인). 부족분 있으면 옵셔널 확장만(design 확정). |
| `main.py` | 수정(선택) | `--decision-report [날짜]` (FR-07, 차기 가능) |

---

## 5. 성공 기준

| SC | 기준 | 검증 |
|----|------|------|
| SC-01 | run_live 종료 시 `reports/YYYY-MM-DD.md` 자동 생성 | 테스트 |
| SC-02 | 보고서에 funnel 4단계(입력→중립→컨센서스→게이트→매수/매도) 종목·사유 표기 | 테스트 |
| SC-03 | 매수 종목 사유(score·합의비율·size·shares) + 매도 사유(action·reason) 명시 | 테스트 |
| SC-04 | 안 산 종목이 **어느 관문에서 왜** 탈락했는지 종목별 표기 | 테스트 |
| SC-05 | 콘솔 한 줄 요약 출력 (FR-05 형식) | 테스트/CLI |
| SC-06 | 보고서 생성에서 예외 발생해도 run_live 결과·주문 정상 (NFR-01) | 테스트(보고서 함수 mock raise) |
| SC-07 | run_pipeline 확장이 기존 동작 회귀 0 | 기존 테스트 전체 통과 |
| SC-08 | 신규 테스트 + 기존 전체 통과 | pytest |

---

## 6. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 보고서 생성 오류가 매매 차단 | 높음 | run_live 종료 단계에서 try/except 격리, 실패 시 warning만 (NFR-01) |
| run_pipeline 시그니처 변경 → 기존 호출부 깨짐 | 중간 | 추가 반환값을 옵셔널/튜플 확장, 기존 호출 하위호환 유지 |
| funnel 데이터 누락(폴백 경로) | 낮음 | 누락 시 "데이터 없음"으로 안전 표기, 보고서는 생성 |
| 후보 0건일 때 빈 보고서 | 낮음 | 0건도 funnel 탈락 사유는 채워 의미 있는 보고서(현재 운영 상황이 바로 이 케이스) |

---

## 7. 구현 순서 (예정)

1. `signal_details` funnel 플래그(`neutral_filtered`/`passed_consensus`/`signal`/`in_top_n`)로 충분한지 검증 — 부족 시에만 옵셔널 확장(하위호환).
2. `decision_report.build_daily_report()` + Markdown 포매터 (신규).
3. `community_live.run_live` 종료 시 funnel 수집 + 보고서 호출(try/except) + 콘솔 요약.
4. `tests/test_decision_report.py` (funnel→보고서, 매수/매도/탈락 사유, 비침습).
5. (선택) `main.py --decision-report` 재생성 명령.
6. 전체 테스트 + 실제 run_live 1회로 보고서 육안 확인.

---

## 8. 가장 중요한 제약

- **비침습 불변**: 보고서는 매매 후 read-only 집계. 보고서 실패 ≠ 매매 실패 (NFR-01).
- **회귀 0**: 매매 판단 로직·`reddit_backtester`·뉴스 경로 무수정 (NFR-02).
- 이미 만든 수동 진단 리포트(`docs/04-report/community-opinion-agent-live.no-trade-diagnosis.md`)가 출력 포맷의 참고 모델.
