# Analysis: Daily Decision Report (Check)

**Feature**: daily-decision-report
**분석일**: 2026-06-06
**단계**: Check (Gap Analysis) · iteration 0 · scope module-1/2/3 (module-4 deferred)
**검증 방식**: 정적 3축 + 런타임(단위/회귀). 웹 L1/L2/L3 비해당(Python).

---

## Context Anchor (Design 승계)

| 항목 | 내용 |
|------|------|
| **WHY** | 매수 0건이 정상/버그인지 매일 수동 분석 비용 제거. 판단 funnel 영속·가시화. |
| **WHO** | 운영자(본인). |
| **RISK** | 보고서 실패가 매매를 막으면 안 됨(예외 격리). 회귀 0. |
| **SUCCESS** | funnel 단계별 탈락 사유 + 매수/매도 사유 + 콘솔 요약 + 비침습. |
| **SCOPE** | 신규 `decision_report.py`·테스트, `community_live` 배선. run_pipeline·매매 로직 불가침. |

---

## 1. Strategic Alignment Check

| 질문 | 판정 | 근거 |
|------|:----:|------|
| Plan 핵심 문제(funnel 가시화) 해결? | ✅ | `_derive_funnel`이 입력→중립→컨센→게이트→매수/매도 5단계 종목·사유 산출 (TC-01) |
| 비침습(보고서 실패≠매매 실패)? | ✅ | `community_live.py:485` try/except 격리 + `build_daily_report` 전파 설계, TC-05 |
| run_pipeline 무수정(회귀 0)? | ✅ | signal_details 기존 플래그로 funnel 도출, run_pipeline 시그니처 불변 |
| 보유-only 매도 포착? | ✅ | orders 기반 매도(D5), signal_details 없는 종목도 표기 (TC-03 EEE) |

---

## 2. Plan Success Criteria 평가

| SC | 기준 | 판정 | 근거 |
|----|------|:----:|------|
| SC-01 | run_live 종료 시 `reports/YYYY-MM-DD.md` 자동 생성 | ✅ | **G1/G2 해소** — `test_t1`이 `res["report_path"]` 존재 + `{date}.md` 종단 assert. reports 경로 tmp 격리(`_live_env`). |
| SC-02 | funnel 4단계 종목·사유 | ✅ | TC-01 |
| SC-03 | 매수·매도 사유 | ✅ | TC-02/03 |
| SC-04 | 탈락 관문 종목별 표기 | ✅ | TC-01/04 |
| SC-05 | 콘솔 한 줄 요약 | ✅ | TC-06 + `build_daily_report` 내 `logger.info(_console_summary())` |
| SC-06 | 예외 격리(보고서 실패→매매 정상) | ✅ | TC-05 + community_live try/except |
| SC-07 | run_pipeline 확장 회귀 0 | ✅ | run_pipeline 무수정, 140 passed |
| SC-08 | 신규 + 전체 테스트 통과 | ✅ | 140 passed, 0 failed |

**충족: 8/8** (G1/G2 iterate 해소 후 SC-01 ✅).

---

## 3. 정적 3축 + 런타임

### 3.1 Structural — 95%
| 항목 | 상태 |
|------|:----:|
| `decision_report.py`(M1) | ✅ |
| `community_live.py` 배선(M2) | ✅ `:485-496` |
| config 상수 | ✅ `config.py:381-383` |
| `tests/test_decision_report.py`(M3) | ✅ 9건 |
| `main.py --decision-report`(M4/FR-07) | ⬜ deferred(선택) |

### 3.2 Functional — 95%
- `_derive_funnel`/`_format_markdown`/`_console_summary`/`build_daily_report` 실 로직 완비, 플레이스홀더 없음.
- G1(테스트 격리)·G2(SC-01 assert) iterate 해소. 잔여 감점: FR-07 미구현(deferred).

### 3.3 Contract — 100%
| 계약 | Design | 구현 |
|------|--------|------|
| `ReportContext` | §5.1 | ✅ dataclass 일치 |
| funnel dict | §5.2 | ✅ input_n/neutral/consensus/gate/buys/sells |
| signal_details 플래그 소비 | §6.1 | ✅ neutral_filtered/passed_consensus/실제 키 일치 |

### 3.4 Runtime — 95%
- 신규 9건(TC-01~09) PASS + `test_t1` SC-01 종단 assert(`report_path` 생성).
- 전체 회귀 140 passed, 0 failed (community_live run_live 통합 포함 → 비침습 end-to-end 확인).
- reports 경로 tmp 격리 → 실 디렉터리 오염 0 검증.

### 3.5 Match Rate (런타임 공식, iterate 후)
```
Overall = Structural×0.15 + Functional×0.25 + Contract×0.25 + Runtime×0.35
        = 95×0.15 + 95×0.25 + 100×0.25 + 95×0.35
        = 14.25 + 23.75 + 25 + 33.25 = 96.25  →  96%
```

---

## 4. Decision Record 검증

| 결정 | 준수? | 비고 |
|------|:----:|------|
| D1 run_pipeline 무수정 | ✅ | signal_details 기존 플래그 사용 |
| D2 순수 포매터 | ✅ | _derive_funnel/_format_markdown 부수효과 없음 |
| D3 비침습 try/except | ✅ | community_live `:485`, TC-05 |
| D5 보유-only 매도 | ✅ | orders 기반, TC-03 |
| D7 한국어·표 | ✅ | _format_markdown |

**설계 이탈 없음.**

---

## 5. Gap 목록 (severity·confidence)

| # | Sev | Conf | 내용 | 처리 |
|---|-----|:----:|------|------|
| ~~G1~~ | ~~Important~~ | — | ~~run_live 통합테스트가 실 reports 디렉터리 기록(오염)~~ | ✅ **해소** — `_live_env`에 `COMMUNITY_LIVE_REPORTS_DIR` tmp 격리, 테스트 후 실 디렉터리 빈 것 확인 |
| ~~G2~~ | ~~Minor~~ | — | ~~SC-01 종단 파일 생성 assert 부재~~ | ✅ **해소** — `test_t1`에 `report_path` 존재+`{date}.md` assert 추가 |
| G3 | Minor | 85% | FR-07/M4 과거 재생성 CLI 미구현 | deferred(선택) — 차기 |

**Critical 없음.** G1/G2 iterate 해소 완료.

---

## 6. 결론

- **Match Rate 96% (≥90%)** — iterate 1회로 G1(테스트 오염)·G2(SC-01 assert) 해소.
- 핵심 기능(funnel·매수/매도/탈락 사유·비침습·콘솔) 완전 구현, SC 8/8 충족, 설계 이탈 없음, 회귀 140 passed.
- 잔여 G3(과거 재생성 CLI)은 Plan에서 선택/차기로 명시 → iterate 불요. **Report 진행 가능.**
