# Analysis: Streamlit Dashboard Deploy (Check)

**Feature**: streamlit-dashboard-deploy
**분석일**: 2026-06-09 · iteration 0 · scope module-1~3 (전체)
**검증**: 정적 3축 + 런타임(단위/회귀/curate 스모크). Cloud 빌드·실제 push·streamlit run은 배포 시 수동.

## Context Anchor (Design 승계)
| 항목 | 내용 |
|------|------|
| WHY | 무거운 app.py는 Cloud 불가 → 슬림 읽기전용 + GitHub 동기화 |
| RISK | 비밀 유출·RAM 초과·데이터 지연 |
| SUCCESS | heavy import 0 / Cloud 빌드 / allowlist push / 최신 표시 / 실주문 0 / timer sync |

## 1. Plan Success Criteria
| SC | 기준 | 판정 | 근거 |
|----|------|:----:|------|
| SC-01 | 슬림앱 heavy import 0 구동 | ✅ | `dashboard_app.py` streamlit/pandas/altair만, TC-05 소스 스캔 |
| SC-02 | 슬림 requirements로 Cloud 빌드 | ⚠️ | `requirements-dashboard.txt`(torch/FinBERT 제외) 완비 — Cloud 실빌드 미실측 |
| SC-03 | allowlist만 push(비밀 제외) | ✅ | `curate`+DENY 이중방어, TC-01/02, 스모크(10파일·누출 0) |
| SC-04 | Cloud 최신 데이터 표시 | ⚠️ | sync+`last_sync.json`+배지 구현 — Cloud 실표시 미실측 |
| SC-05 | 대시보드 KIS·실주문 0 | ✅ | TC-05(import 0) + 소스 점검 |
| SC-06 | 우분투 timer 주기 sync | ⚠️ | `deploy/sync-dashboard.{service,timer}` 완비 — 서버 미실측 |
| SC-07 | 기존 회귀 0 | ✅ | app.py·실매매 무수정, 153 passed |

**충족: 4/7 ✅ (SC-02/04/06은 Cloud/서버 배포 시 검증 — 코드 완비).**

## 2. 정적 3축 + 런타임
- **Structural 100%**: dashboard_app·requirements-dashboard·.streamlit·sync_dashboard_data·deploy 2종·테스트·runbook 전부 존재.
- **Functional 95%**: curate/allowlist/DENY/패널/orphan push 실로직 완비. server-only(실push·Cloud빌드·streamlit run) 미실행만 감점.
- **Contract 100%**: allowlist·dashboard-data 레이아웃·DENY(§5) 일치.
- **Runtime 90%**: 신규 5 + 회귀 153 + curate 스모크(10파일·비밀 0). 실 push/Cloud 미실행.

```
Overall = 100×0.15 + 95×0.25 + 100×0.25 + 90×0.35 = 95.25 → 95%
```

## 3. Decision Record 검증
| 결정 | 준수 |
|------|:----:|
| D1 자립형 heavy import 0 | ✅ TC-05 |
| D2 allowlist + DENY | ✅ TC-01/02 |
| D3 orphan force-push(worktree) | ✅ 구현(서버 실행) |
| D4 시크릿 0 | ✅ 읽기전용 |
| D6 last_sync 배지 | ✅ |
| app.py 불가침 | ✅ 회귀 0 |

## 4. Gap 목록
| # | Sev | 내용 | 처리 |
|---|-----|------|------|
| G1 | Important | SC-02/04/06 Cloud·서버 미검증 | runbook대로 우분투 sync 1회 + Cloud 배포 후 확인 |
| G2 | Minor | Open-2(snapshots 전체 push — 브랜치 크기) | 최근 N일 요약 제한은 차기 |
| G3 | Minor | Open-4 우분투 GitHub push 인증(PAT/deploy key) | runbook §1 명시, 서버 설정 |

**Critical 0.** G1은 코드 결함이 아니라 Cloud/서버 실측 항목.

## 5. 결론
- **Match Rate 95% (≥90%)** — 슬림 대시보드·sync·테스트·runbook 완비, app.py·실매매 무영향(153 passed).
- 잔여는 **Cloud 배포/서버 sync 실측(SC-02/04/06)** — runbook으로 수행.
