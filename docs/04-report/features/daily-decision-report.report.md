# Report: Daily Decision Report

**Feature**: daily-decision-report
**완료일**: 2026-06-06
**최종 Match Rate**: 96% (Check) · iteration 1 (G1/G2 해소)
**아키텍처**: Option C — `ReportContext` dataclass + 순수 Markdown 포매터 (`run_pipeline` 무수정)
**PDCA 문서**: [Plan](../../01-plan/features/daily-decision-report.plan.md) · [Design](../../02-design/features/daily-decision-report.design.md) · [Analysis](../../03-analysis/daily-decision-report.analysis.md)

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 라이브 에이전트가 매일 돌지만 "왜 샀는지 / 왜 아무것도 안 샀는지(어느 관문 탈락)"를 매번 사람이 로그를 뒤져 수동 분석. |
| **Solution** | run_live 종료 시 funnel(입력→중립→컨센서스→게이트→매수/매도) + 종목별 사유를 Markdown 보고서로 자동 생성, 콘솔 한 줄 요약. 기존 `signal_details` 플래그로 도출(run_pipeline 무수정). |
| **Function UX Effect** | 매일 `data/community/live/reports/YYYY-MM-DD.md` 1개 → 운영자가 "오늘 왜 매매했/안했는지" 즉시 파악. |
| **Core Value** | 판단 과정을 투명·추적 가능하게 만들어 무행동이 결함인지 정상인지 매일 자동 판별. forward 운영 신뢰도 확보. |

### 1.3 Value Delivered (실제 결과)

| 관점 | 지표 | 결과 |
|------|------|------|
| **수동 분석 제거** | funnel 가시화 | ✅ 5단계(입력/중립/컨센/게이트/매수·매도) 종목·사유 자동 표기 |
| **비침습** | 매매 무영향 | ✅ try/except 격리 — 보고서 실패해도 run_live 정상(TC-05 + 통합 140 green) |
| **회귀 0** | run_pipeline | ✅ 무수정, 기존 signal_details 플래그 재사용 |
| **품질** | 테스트 | ✅ 신규 9 + 전체 **140 passed, 0 failed**, reports 디렉터리 오염 0 |

---

## 2. 구현 요약

| 모듈 | 파일 | 변경 |
|------|------|------|
| M1 Report core | `decision_report.py` (신규) | `ReportContext` + `_derive_funnel` + `_format_markdown` + `_console_summary` + `build_daily_report` (순수·read-only) |
| M2 Live wiring | `community_live.py:485` | run_live `return` 직전 try/except 호출 + 반환 dict `report_path` |
| config | `config.py:381` | `COMMUNITY_DECISION_REPORT_ENABLED` + `COMMUNITY_LIVE_REPORTS_DIR` |
| M3 Tests | `tests/test_decision_report.py` (신규) | TC-01~09 |
| Act(iter1) | `tests/test_community_live.py` | `_live_env` reports tmp 격리(G1) + `test_t1` SC-01 종단 assert(G2) |
| M4 (deferred) | `main.py --decision-report` | 과거 재생성 CLI — 선택/차기 (FR-07) |

**신규 2파일 + 수정 3파일.** run_pipeline·매매 판단 로직·reddit_backtester·뉴스 경로 불가침 준수.

---

## 3. Key Decisions & Outcomes

| 결정 | 출처 | 준수 | 결과 |
|------|------|:----:|------|
| D1 run_pipeline 무수정 | Design | ✅ | `signal_details` 기존 플래그로 funnel 도출 — 회귀 0 |
| D2 순수 포매터 | Design | ✅ | `_derive_funnel`/`_format_markdown` 부수효과 없음 → 문자열 단위테스트 |
| D3 비침습 try/except | Design | ✅ | community_live 격리, TC-05 잠금 |
| D5 보유-only 매도 | Design | ✅ | orders 기반 매도 — signal_details 없는 종목도 표기(TC-03) |
| Plan D5(개정) signal_details funnel 노출 | Plan | ✅ | run_pipeline 확장 불필요 확인 → 작업량·리스크 축소 |

---

## 4. Success Criteria Final Status

| SC | 기준 | 상태 | 근거 |
|----|------|:----:|------|
| SC-01 | run_live 종료 시 보고서 자동 생성 | ✅ | `test_t1` `report_path` 존재+`{date}.md` assert |
| SC-02 | funnel 4단계 종목·사유 | ✅ | TC-01 |
| SC-03 | 매수·매도 사유 | ✅ | TC-02/03 |
| SC-04 | 탈락 관문 표기 | ✅ | TC-01/04 |
| SC-05 | 콘솔 한 줄 요약 | ✅ | TC-06 + build_daily_report logger |
| SC-06 | 예외 격리(보고서 실패≠매매 실패) | ✅ | TC-05 + community_live try/except |
| SC-07 | run_pipeline 회귀 0 | ✅ | 무수정, 140 passed |
| SC-08 | 신규 + 전체 테스트 통과 | ✅ | 140 passed, 0 failed |

**성공률: 8/8 충족.**

---

## 5. 잔여 항목 (후속)

| # | 내용 | 처리 |
|---|------|------|
| G3 / FR-07 | `main.py --decision-report` 과거 날짜 재생성 CLI | deferred(선택) — `--scope module-4`로 차기 구현 가능 |
| 육안 확인 | 실제 데일리 모의 구동 1회로 보고서 가독성·funnel 정확도 검토 | 다음 run_live 시 reports/{date}.md 확인 |

---

## 6. 결론

run_live의 판단 funnel을 사람이 읽는 Markdown 보고서로 자동 집계하는 기능이 비침습(read-only, try/except)으로 완성됐다. `run_pipeline`의 기존 `signal_details` 플래그를 재사용해 엔진 수정 없이 funnel을 도출했고(회귀 0), Act 1회로 테스트 격리 결함까지 닫았다. SC 8/8 충족, 140개 테스트 무결. 잔여는 선택적 과거 재생성 CLI(FR-07)뿐이다.

**다음**: `/pdca archive daily-decision-report` (문서 아카이브) 또는 다음 데일리 구동으로 보고서 육안 확인.
