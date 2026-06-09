# Report: Streamlit Dashboard Deploy

**Feature**: streamlit-dashboard-deploy
**완료일**: 2026-06-09 · **Match Rate**: 95% (Check) · iteration 0
**아키텍처**: Option C — 자립형 슬림 대시보드 + GitHub `dashboard-data` orphan 브랜치 동기화
**문서**: [Plan](../../01-plan/features/streamlit-dashboard-deploy.plan.md) · [Design](../../02-design/features/streamlit-dashboard-deploy.design.md) · [Analysis](../../03-analysis/streamlit-dashboard-deploy.analysis.md)

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 현 `app.py`는 FinBERT(torch 418MB)·KIS 실연결로 Streamlit Cloud(1GB)에 못 올리고, Cloud는 우분투 라이브 데이터를 못 봄. |
| **Solution** | FinBERT·KIS 없는 **자립형 읽기전용 `dashboard_app.py`** + 슬림 requirements + 우분투가 큐레이트 데이터를 **`dashboard-data` orphan 브랜치로 force-push**(allowlist 비밀 제외). |
| **Function UX Effect** | Cloud URL 1개로 포트폴리오·매매이력·일일 결정 funnel·여론 추세 조회. 실매매(우분투)와 역할 분리. |
| **Core Value** | 무거운 실매매=우분투, 가벼운 공개 조회=Cloud. 안전(실주문 0·비밀 0)·저비용(추가 인프라 0)·준실시간. |

### 1.3 Value Delivered
| 관점 | 지표 | 결과 |
|------|------|------|
| 배포 가능성 | 의존성 | ✅ 슬림 requirements(torch/FinBERT 제외) — 1GB 내 |
| 안전 | 비밀 차단 | ✅ allowlist+DENY 이중방어, 테스트로 잠금(.env·token·model·cache 제외) |
| 읽기전용 | 실주문 | ✅ KIS/실주문/FinBERT import 0 (TC-05) |
| 무영향 | 회귀 | ✅ app.py·실매매 무수정, **153 passed, 0 failed** |

---

## 2. 구현 요약
| 모듈 | 산출물 |
|------|--------|
| M1 | `dashboard_app.py`(4탭 읽기전용) · `requirements-dashboard.txt` · `.streamlit/config.toml` |
| M2 | `scripts/sync_dashboard_data.py`(curate allowlist + orphan force-push) · `deploy/sync-dashboard.{service,timer}` |
| M3 | `tests/test_sync_dashboard.py`(TC-01~05) · `docs/ops/streamlit-dashboard.md` |

**신규 8파일, 기존 코드 수정 0 (app.py·실매매·스케줄러 불가침).**

## 3. Key Decisions & Outcomes
| 결정 | 준수 | 결과 |
|------|:----:|------|
| Option C 자립형 슬림앱 | ✅ | heavy import 0, app.py 무영향 |
| D2 allowlist+DENY 이중방어 | ✅ | 비밀 유출 차단 테스트로 잠금 |
| D3 orphan force-push(worktree) | ✅ | 히스토리 비대 없음·메인 무오염 |
| D4 시크릿 0 | ✅ | 읽기전용 → Cloud 시크릿 불필요 |

## 4. Success Criteria Final Status
✅ SC-01(heavy import 0) · SC-03(allowlist 비밀 제외) · SC-05(실주문 0) · SC-07(회귀 0)
⚠️ SC-02(Cloud 빌드) · SC-04(Cloud 표시) · SC-06(timer sync) — **코드 완비, Cloud/서버 실측 대기**
**충족: 4/7 (3건 배포 실측 대기).**

## 5. 잔여 항목
| # | 내용 | 처리 |
|---|------|------|
| G1 | Cloud 빌드·표시·timer 미검증 | runbook §1~2: 우분투 sync 1회 + Cloud 앱 생성(branch=dashboard-data) |
| G2 | snapshots 전체 push(브랜치 크기) | 최근 N일 요약 제한 차기 |
| G3 | 우분투 GitHub push 인증 | PAT/deploy key (runbook §1) |

## 6. 결론
무거운 실매매 코드를 전혀 끌어오지 않는 자립형 읽기전용 대시보드와, allowlist로 비밀을 차단하는 GitHub orphan-브랜치 동기화를 완성했다. 신규 5 + 회귀 153 무결, 기존 app.py·실매매 무영향. **남은 건 우분투에서 sync 1회 → Streamlit Cloud 앱 생성(SC-02/04/06)뿐**이며 절차는 `docs/ops/streamlit-dashboard.md`에 있다.

**다음**: 우분투 sync 1회 + Cloud 배포 → 실측 → `/pdca archive`.
