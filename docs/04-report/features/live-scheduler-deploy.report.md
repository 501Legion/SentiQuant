# Report: Live Scheduler Deploy

**Feature**: live-scheduler-deploy
**완료일**: 2026-06-09 · **Match Rate**: 95% (Check) · iteration 0
**아키텍처**: Option C (runtime_guard 순수+IO + notifier + scheduler 훅) + Approach B(외부 워치독)
**대상**: 24h Ubuntu + systemd · KIS 모의투자 실주문
**문서**: [Plan](../../01-plan/features/live-scheduler-deploy.plan.md) · [Design](../../02-design/features/live-scheduler-deploy.design.md) · [Analysis](../../03-analysis/live-scheduler-deploy.analysis.md)

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 스케줄러 로직은 완비됐으나, 서버 상시·자동복구 운영 + 실주문 안전장치가 없었음. 미검증 게이트 완화 상태라 통제 필수. |
| **Solution** | systemd 서비스(Restart=always·부팅시작) + 외부 워치독(hang 복구) + 4중 안전장치(키스위치/일일·노출 한도/알림/헬스·자가점검). 판단 로직 무수정. |
| **Function UX Effect** | `systemctl enable --now auto-stock` → 매 거래일 09:35 ET 자동 실주문, crash·hang 양쪽 자동 복구, 이상 시 알림·자동 중단. |
| **Core Value** | 무인 상시 운영하되 폭주·침묵 실패를 워치독·키스위치·한도·알림으로 통제. |

### 1.3 Value Delivered (실제 결과)
| 관점 | 지표 | 결과 |
|------|------|------|
| 자동복구 | crash+hang | ✅ systemd Restart + 워치독 timer(heartbeat stale→restart) |
| 안전 통제 | 4중 가드 | ✅ 키스위치·일일5/노출60%/종목20%·알림·selfcheck(모델·paper) |
| 판단 불변 | 회귀 | ✅ 신호/사이징 무수정, **148 passed, 0 failed** |
| 운영성 | provisioning | ✅ requirements(praw)·.env.example·모델 scp·py3.11 runbook |

---

## 2. 구현 요약
| 모듈 | 산출물 |
|------|--------|
| M1 | `runtime_guard.py`(is_halted·filter_by_limits·heartbeat·selfcheck) + `config.py` 상수 7 |
| M2 | `notifier.py`(Slack no-op·마스킹) |
| M3 | `scheduler.py` 훅(selfcheck/halt/heartbeat/알림) + `community_live.py` 매수 게이트 |
| M4 | `tests/test_runtime_guard.py` TC-01~08 |
| M5 | `deploy/auto-stock.service`·`watchdog.{service,timer}`·`scripts/watchdog_check.py`·requirements(praw)·`.env.example`·`docs/ops/live-scheduler.md` |

**신규 8파일 + 수정 4(scheduler·community_live·config·requirements). 판단 로직·신호엔진·백테스트 불가침.**

## 3. Key Decisions & Outcomes
| 결정 | 출처 | 준수 | 결과 |
|------|------|:----:|------|
| Approach B(가드+외부 워치독) | plan-plus | ✅ | crash+hang 양쪽 복구 — 무인 실주문 침묵 실패 방어 |
| Option C(순수+IO분리) | Design | ✅ | filter_by_limits 등 순수 fn 단위테스트 8건 |
| D2 매수 실행 게이트 | Design | ✅ | 판단 로직 불변(NFR-01), 회귀 0 |
| D4 selfcheck에 모델·paper 점검 | Design | ✅ | fresh-clone 갭(model 418MB)·실계좌 오설정 기동 차단 |

## 4. Success Criteria Final Status
✅ SC-03(키스위치) · SC-04(한도) · SC-05(알림) · SC-06(heartbeat/selfcheck) · SC-07(판단불변·회귀) · SC-08(마스킹) · SC-09(워치독)
⚠️ SC-01(systemd 복구) · SC-02(정시 ET) — **코드/유닛 완비, Ubuntu 서버 실동작 검증만 잔여**
**충족: 7/9 (2건 서버 실측 대기).**

## 5. 잔여 항목
| # | 내용 | 처리 |
|---|------|------|
| G1 | systemd 자동복구·정시 서버 미검증 | 우분투 배포 후 runbook §0~3 + 강제 kill·SIGSTOP·`list-timers`로 확인 |
| G2 | today_buy_count 런당(교차-런 카운트) | 노출% 절대방어로 보완, decision_log 집계는 차기 |

## 6. 결론
무인 실주문 스케줄러를 systemd + 외부 워치독으로 상시·자동복구 구동하고, 4중 안전장치(키스위치·일일/노출 한도·알림·자가점검)를 판단 로직 무수정으로 얹었다. 단위 8 + 회귀 148 무결. 코드·배포 산출물·runbook 완비. **남은 건 24h 우분투에서 runbook대로 설치 후 systemd 복구·정시 실행 실측(SC-01/02)뿐.**

**다음**: 우분투 배포(runbook) → SC-01/02 실측 → 안정화 후 `/pdca archive`.
