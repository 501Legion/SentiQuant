# Analysis: Live Scheduler Deploy (Check)

**Feature**: live-scheduler-deploy
**분석일**: 2026-06-09 · iteration 0 · scope module-1~5 (전체)
**검증**: 정적 3축 + 런타임(단위/통합/회귀). systemd·서버 실동작(SC-01/02)은 Ubuntu 배포 시 수동.

## Context Anchor (Design 승계)
| 항목 | 내용 |
|------|------|
| WHY | 무인 자동복구(crash+hang) + 실주문 안전 통제 |
| RISK | 무인 실주문 폭주/침묵 실패, fresh clone 안 돎 |
| SUCCESS | systemd 복구 / 키스위치 / 일일·노출 한도 / 알림 / 헬스·자가점검 / 워치독 / 판단 불변 |

## 1. Plan Success Criteria
| SC | 기준 | 판정 | 근거 |
|----|------|:----:|------|
| SC-01 | systemd 자동재시작·부팅시작 | ⚠️ | `deploy/sentiquant.service`(Restart=always·WantedBy) 작성 — **Ubuntu 서버 실동작 미검증** |
| SC-02 | 09:35 ET 정시(서버 TZ 무관) | ⚠️ | 기존 APScheduler CronTrigger(TIMEZONE) — 서버 미검증 |
| SC-03 | 키스위치 주문만 스킵 | ✅ | `is_halted()` 파일/env, scheduler 차단, TC-01 |
| SC-04 | 일일·노출 한도 | ✅ | `filter_by_limits` + community_live 게이트, TC-02/03/04 |
| SC-05 | 주문·오류·할트·헬스 알림 | ✅ | `notifier.notify`, scheduler 훅, TC-07 |
| SC-06 | heartbeat/자가점검 | ✅ | `write_heartbeat`/`selfcheck`(모델·paper 포함), TC-05/06 |
| SC-07 | 판단 로직 불변·회귀 0 | ✅ | 신호/사이징/라우터 무수정, 148 passed |
| SC-08 | 비밀 마스킹 | ✅ | `notifier._mask`, TC-07 |
| SC-09 | 워치독 stale(hang) 감지 | ✅ | `heartbeat_stale` + `watchdog_check.py`, TC-08 + 스모크 |

**충족: 7/9 ✅ (SC-01/02는 서버 배포 시 검증 — 코드 완비).**

## 2. 정적 3축 + 런타임
- **Structural 100%**: runtime_guard·notifier·watchdog_check·deploy 3종·.env.example·runbook·테스트·config·requirements(praw) 전부 존재.
- **Functional 95%**: 가드/알림/워치독/게이트 실로직 완비. 감점: today_buy_count=0(런당, 교차-런은 노출%가 방어 — Open-1 단순화).
- **Contract 100%**: config 상수(§5.3)·`filter_by_limits`(§5.4)·heartbeat 스키마 일치.
- **Runtime 90%**: 신규 8 + 회귀 148 passed + buy 게이트 run_live 통합(test_community_live 10) + 워치독 스모크. systemd 서버 실동작 미실행.

```
Overall = 100×0.15 + 95×0.25 + 100×0.25 + 90×0.35 = 95.25 → 95%
```

## 3. Decision Record 검증
| 결정 | 준수 |
|------|:----:|
| D1 가드 순수+IO분리 | ✅ |
| D2 매수 실행 게이트(판단 불변) | ✅ |
| D3 키스위치 파일/env | ✅ |
| D4 selfcheck 실패→차단(모델·paper) | ✅ |
| D6 워치독 관측·재시작만 | ✅ |
| D7 notifier no-op·마스킹 | ✅ |

## 4. Gap 목록
| # | Sev | 내용 | 처리 |
|---|-----|------|------|
| G1 | Important | SC-01/02 systemd·정시 서버 미검증 | Ubuntu 배포 후 수동(강제 kill·SIGSTOP·timer) |
| G2 | Minor | today_buy_count 런당(교차-런 카운트 미반영) | 노출% 절대방어로 보완, decision_log 집계는 차기 |

**Critical 0.** G1은 코드 결함이 아니라 서버 환경 실측 항목.

## 5. 결론
- **Match Rate 95% (≥90%)** — 코드/배포 산출물 완비, 판단 로직 불변, 회귀 148 passed.
- 잔여는 **서버 실배포 검증(SC-01/02)** — 우분투에서 runbook대로 설치 후 확인.
