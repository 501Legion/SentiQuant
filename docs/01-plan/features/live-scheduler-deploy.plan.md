# Plan: Live Scheduler Deploy — 서버 상시 실주문 스케줄러 + 안전장치

**Feature**: live-scheduler-deploy
**작성일**: 2026-06-08 · **개정**: 2026-06-08 (`/plan-plus` 브레인스토밍 — 안전 아키텍처 Approach B 채택)
**상위 피처**: community-opinion-agent-live (라이브 에이전트), scheduler (APScheduler 2-job)
**대상 환경**: Linux + systemd · KIS 모의투자 계좌 실주문
**방식**: `/plan-plus` (Intent → Alternatives → YAGNI)

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 스케줄러 로직(`python main.py` → `start_scheduler`, 09:35 ET 실주문)은 완비됐으나, 서버에서 **죽지 않고 상시·자동 복구로 돌릴 운영 체계**와 **실주문 안전장치**가 없다. 게다가 직전 세션에 매수 게이트 7개를 미검증 완화한 상태. |
| **Solution** | 기존 `start_scheduler`를 **systemd 서비스**(Restart=always, 부팅 자동시작)로 배포 + **4중 안전장치**(키스위치/일일 한도/알림/헬스체크)를 코드와 운영에 추가. |
| **Function UX Effect** | 서버에서 `systemctl enable --now sentiquant.service` 한 번 → 매 거래일 자동 실주문, crash·재부팅에도 자동 복구. 이상 시 알림·자동 중단. |
| **Core Value** | 사람 개입 없이 안전하게 상시 운영. "조용한 폭주"(미검증 완화 + 무인 실주문) 리스크를 키스위치·한도·알림으로 통제. |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 무인 상시 운영이 목표지만, 실주문 + 미검증 완화라 **안전 통제 없는 자동화는 위험**. 자동 복구 + 가드레일이 핵심. |
| **WHO** | 운영자(본인) — 서버에 띄워두고 매일 결과만 확인. |
| **RISK** | 실주문 폭주/오류 누적, 자격증명·시간대 오설정, 프로세스 죽음 인지 못함, 미검증 완화로 과매매. |
| **SUCCESS** | systemd 자동 복구·부팅시작 / 키스위치 즉시 중단 / 일일 주문·노출 한도 / 주문·오류 알림 / 헬스체크·자가점검 / 매매 판단 로직 불변(회귀 0). |
| **SCOPE** | 신규: `deploy/sentiquant.service`·`notifier.py`·운영 runbook·테스트. 수정: `scheduler.py`(가드/하트비트/자가점검/알림 훅), `config.py`(상수). 불가침: 매매 판단·신호 엔진·백테스트. |

---

## 1. 현재 상태 (As-Is)

| 요소 | 상태 |
|------|------|
| 스케줄러 | ✅ `scheduler.start_scheduler()` — APScheduler BlockingScheduler, 16:30 ET 신호잡 + 09:35 ET 주문잡, NYSE 휴장 제외 |
| 진입점 | ✅ `python main.py` (무인자) → `start_scheduler()` (main.py:376-378) |
| 실주문 | ✅ 주문잡 기본 `dry_run=False`, `LIVE_STRATEGY="agent"` → `community_live.run_live(dry_run=False)` → KIS 모의투자 실주문 |
| 프로세스 관리 | ❌ 없음 (포그라운드, 죽으면 끝, 부팅시작 없음) |
| 키스위치 | ❌ 없음 (Ctrl+C / `/control stop`은 대화형) |
| 일일 한도 | △ size 클램프는 있으나 **일일 주문 건수·총 노출 상한 없음** |
| 알림 | ❌ 전무 (slack/webhook/sentry 코드 없음) |
| 헬스체크 | ❌ 없음 (마지막 성공 시각·자가점검 없음) |

---

## 2. 기능 요구사항

### FR — 배포/운영
| ID | 내용 |
|----|------|
| FR-01 | **systemd 유닛** `deploy/sentiquant.service`: `ExecStart=<venv>/bin/python main.py`, `Restart=always`, `RestartSec=10`, `WorkingDirectory`, `EnvironmentFile=.env`, `After=network-online.target`, `WantedBy=multi-user.target`(부팅 자동시작). |
| FR-02 | **운영 runbook** `docs/ops/live-scheduler.md`: 설치/시작/중지/로그확인/키스위치/업데이트 절차. |
| FR-03 | **로그 회전** — `trading.log` RotatingFileHandler(또는 journald) + 표준출력 journald 수집. |
| FR-04 | **시간대 독립** — APScheduler `config.TIMEZONE`(ET) 사용. 서버 TZ와 무관하게 09:35 ET 보장(검증 항목). |

### FR — 안전장치
| ID | 내용 |
|----|------|
| FR-05 | **키스위치**: `data/TRADING_HALT`(파일) 또는 `TRADING_HALT=1`(env) 존재 시 주문잡이 **주문 단계만 스킵**(스케줄러·수집·로그는 유지). `order_processing_job` 시작부 체크. |
| FR-06 | **일일 한도**: 하루 최대 신규 매수 건수(`MAX_DAILY_BUYS`) + 총 투자금 대비 노출 상한(`MAX_TOTAL_EXPOSURE_PCT`) + 종목당 비중 상한. 초과 시 추가 매수 차단(기존 포지션·매도 무관). |
| FR-07 | **알림**: `notifier.py` — Slack incoming webhook(`SLACK_WEBHOOK_URL`, 미설정 시 no-op). 발송 이벤트: 주문 체결 요약, 주문/잡 오류, 키스위치 발동, 헬스체크 실패. |
| FR-08 | **헬스체크/하트비트**: 각 잡 성공 시 `data/heartbeat.json`에 `{job, last_success_utc}` 기록. 기동 시 **자가점검**(KIS 자격·Reddit/Polygon 키·TIMEZONE·필수 파일) 후 실패 시 알림+로그(주문은 안전상 중단). |
| FR-09 | **외부 워치독 (Approach B)**: `deploy/watchdog.timer`+`watchdog.service`(systemd timer, N분 주기) 또는 cron이 `heartbeat.json` 신선도 검사 → **stale(=hang 추정)이면 알림 + `systemctl restart sentiquant.service`**. crash(Restart=always)가 못 잡는 "살아있는데 멈춤"을 복구. 워치독 자체는 주문 안 함(관측·재시작만). |

### FR — Provisioning (fresh clone 부팅, 2026-06-08 확인된 갭)
| ID | 내용 |
|----|------|
| FR-10 | **의존성 완전성**: `requirements.txt`에 **`praw` 누락**(reddit_collector가 `import praw`) → 추가 필수. fresh 설치 시 Reddit 수집 `ModuleNotFoundError` 방지. (확인: 현재 praw 없음) |
| FR-11 | **FinBERT 모델 전달**: `models/finbert-onnx/model.onnx`(**418MB**)는 `.gitignore: models/`로 **clone에 미포함** → 서버 배포 시 별도 전달 절차 필요(재export 스크립트 / scp / Git LFS / S3 중 택1, design 확정). 누락 시 FinBERT 로드 실패→전부 neutral→매수 0. |
| FR-12 | **`.env` 템플릿**: `.env`(KIS·Reddit·Polygon 키)는 미추적 → `.env.example` 제공 + runbook에 작성 절차. 자가점검(FR-08)이 누락 키 차단. |
| FR-13 | **Python 3.11 고정**: torch `c10.dll`이 3.13에서 깨짐(실측) → 3.11 venv 명시(runbook/systemd ExecStart 경로). **`data/kis_token.json`이 git 추적 중**(만료 토큰 커밋) → .gitignore 이전 검토. |

### NFR
| ID | 내용 |
|----|------|
| NFR-01 | **매매 판단 불변** — 신호 엔진·agent_gate·community_live 판단 로직 무수정. 가드는 주문 실행 전 게이트로만 추가. |
| NFR-02 | **회귀 0** — 기존 전체 테스트(140) 통과. 스케줄러 잡 시그니처 하위호환. |
| NFR-03 | **graceful** — 알림/하트비트/헬스체크 실패가 매매를 막지 않음(키스위치·자가점검 실패 제외, 이건 의도적 차단). |
| NFR-04 | **비밀 안전** — 자격증명은 `.env`/시스템 시크릿. 리포지토리·로그·알림에 평문 노출 금지. |

---

## 3. 변경/신규 파일

| 파일 | 구분 | 변경 |
|------|------|------|
| `deploy/sentiquant.service` | 신규 | systemd 유닛 (메인 스케줄러) |
| `deploy/watchdog.service` + `deploy/watchdog.timer` | 신규 | 외부 워치독(heartbeat stale 감지→재시작·알림, FR-09) |
| `scripts/watchdog_check.py` | 신규 | 워치독 검사 로직(heartbeat 신선도→exit code/restart 트리거) |
| `docs/ops/live-scheduler.md` | 신규 | 설치·운영 runbook |
| `notifier.py` | 신규 | Slack webhook 알림(미설정 no-op) |
| `runtime_guard.py` | 신규 | 키스위치·일일 한도·하트비트·자가점검 헬퍼(순수+IO 분리) |
| `scheduler.py` | 수정 | 주문잡 시작부 키스위치·자가점검, 잡 종료 하트비트, 알림 훅 |
| `config.py` | 수정 | `TRADING_HALT_FILE`, `MAX_DAILY_BUYS`, `MAX_TOTAL_EXPOSURE_PCT`, `MAX_SYMBOL_WEIGHT_PCT`, `SLACK_WEBHOOK_URL`, `HEARTBEAT_FILE` |
| `tests/test_runtime_guard.py` | 신규 | 키스위치·한도·하트비트·자가점검 단위 |
| `requirements.txt` | 수정 | **`praw` 추가**(FR-10) |
| `.env.example` | 신규 | 키 템플릿(FR-12) |
| `scripts/export_finbert_onnx.py` 또는 모델 전달 절차 | 신규/문서 | model.onnx 418MB 전달(FR-11, design 확정) |

> 일일 한도(FR-06)의 실제 적용 지점(community_live 주문 실행 직전 vs agent_gate)은 **설계에서 확정** — 판단 로직 불변(NFR-01) 원칙상 "주문 실행 게이트"로 넣는 것이 1순위.

---

## 4. 성공 기준

| SC | 기준 | 검증 |
|----|------|------|
| SC-01 | systemd 서비스로 기동·자동재시작·부팅시작 동작 | 서버 `systemctl` + 강제 kill 후 자동 복구 확인 |
| SC-02 | 09:35 ET 주문잡이 서버 TZ 무관하게 정시 실행 | 로그 타임스탬프(ET) 확인 |
| SC-03 | 키스위치(파일/env) 발동 시 주문만 스킵, 스케줄러 유지 | 테스트 + 수동 |
| SC-04 | 일일 매수 한도·노출 상한 초과 시 추가 매수 차단 | 테스트(mock 포지션/주문) |
| SC-05 | 주문 체결·오류·키스위치·헬스실패 알림 발송(웹훅 mock) | 테스트 |
| SC-06 | 하트비트 기록 + 기동 자가점검(자격/TZ/파일) 실패 시 차단·알림 | 테스트 |
| SC-07 | 매매 판단 로직 불변, 기존 140 테스트 통과 | 전체 회귀 |
| SC-08 | 비밀 미노출(.env·시크릿, 로그/알림 마스킹) | 코드 점검 |
| SC-09 | 워치독: heartbeat stale(hang) 시 재시작+알림 트리거 (FR-09) | 테스트(stale heartbeat mock) + 수동(프로세스 SIGSTOP 주입) |

---

## 5. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 미검증 게이트 완화 + 무인 실주문 → 과매매 | 높음 | FR-06 일일 한도 + FR-05 키스위치 + 백테스트 검증(별도, 진행 중) |
| 프로세스 죽음 인지 못함 | 높음 | FR-01 Restart=always + FR-08 하트비트 + 알림 |
| 시간대 오설정으로 잘못된 시각 주문 | 중간 | FR-04 TIMEZONE 명시 + SC-02 검증 |
| 자격증명 누락/만료로 잡 실패 누적 | 중간 | FR-08 기동 자가점검 + 오류 알림 |
| 비밀 노출(로그/알림/리포) | 중간 | NFR-04 마스킹·.env |
| KIS API 장애/429 중 주문 | 중간 | 기존 graceful degradation 유지 + 오류 알림 |
| **fresh clone이 안 돎** (model 418MB·.env 미포함, praw 누락, py3.13 torch 깨짐) | 높음 | FR-10~13 provisioning + runbook 자가점검(FR-08)으로 기동 전 차단 |
| `data/kis_token.json` git 커밋(비밀 노출) | 중간 | .gitignore 이전 + 토큰 회전 (FR-13) |

---

## 6. 가장 중요한 제약

- **판단 로직 불가침**: 안전장치는 전부 "주문 실행 전 게이트/관측"으로만. 신호·사이징·라우터 무수정 (NFR-01).
- **실주문 인지**: 주문잡 기본 `dry_run=False`. 배포 = 곧 실주문. 키스위치·한도·알림이 1차 방어선.
- **검증 선행 권고**: 진행 중인 백테스트(2026-05-13~06-06)로 완화 조합의 과매매·손실을 먼저 확인한 뒤 실배포 권장.

---

## 7. 구현 순서 (예정)

1. `config.py` 상수 + `runtime_guard.py`(키스위치·한도·하트비트·자가점검, 순수 로직 우선).
2. `notifier.py`(Slack webhook, no-op fallback).
3. `scheduler.py` 배선(주문잡 가드·자가점검·하트비트·알림 훅).
4. `tests/test_runtime_guard.py` + 전체 회귀.
5. `deploy/sentiquant.service` + `scripts/watchdog_check.py` + `deploy/watchdog.{service,timer}` + `docs/ops/live-scheduler.md` runbook.
6. 서버 설치·자가점검·강제 kill 복구(crash)·SIGSTOP 후 워치독 복구(hang)·정시 실행(SC-01/02/09) 수동 검증.

---

## User Intent Discovery (Plan Plus Phase 1)

| 항목 | 결정 |
|------|------|
| **핵심 문제** | 스케줄러 로직은 완비. 서버에서 죽지 않고 상시·자동복구로 도는 운영 + 실주문 안전장치가 없음. |
| **타깃 사용자** | 운영자(본인) — 서버에 띄워두고 결과만 확인. |
| **성공 기준** | 무인 자동복구(crash+hang) + 4중 안전장치(키스위치/한도/알림/헬스) + 판단 로직 불변. |
| **제약** | 배포=곧 실주문(KIS 모의), 미검증 게이트 완화 상태, 자격증명/시간대. |

## Alternatives Explored (Plan Plus Phase 2)

### 안전 아키텍처

| 안 | 내용 | 채택 |
|----|------|:----:|
| A 인프로세스 가드만 | 가드를 scheduler 내부 체크. 단순하나 **hang 미감지**(스케줄러 멈추면 가드도 멈춤, Restart 안 걸림) | |
| **B 가드 + 외부 워치독** | A + systemd timer/cron이 heartbeat stale 감지→재시작+알림. **crash(Restart)+hang(워치독) 양쪽 복구** | ✅ |
| C bkit /control 연동 | 기존 거버넌스 재사용하나 트레이딩 잡과 과결합·복잡도↑ | |

> **근거**: 사람이 안 보는 서버 실주문에서 "살아있는데 멈춤(hang)"이 가장 위험한 침묵 실패. `Restart=always`는 crash만 복구 → 외부 워치독으로 hang까지 메움. C는 YAGNI.

## YAGNI Review (Plan Plus Phase 3)

**v1 포함** (사용자 선택 — 전 항목):
1. systemd 서비스(Restart=always + 부팅시작) — 필수
2. 키스위치(파일/env 즉시 중단)
3. 일일 매수 건수 한도(`MAX_DAILY_BUYS`)
4. 외부 워치독(hang 감지→재시작+알림)
5. Slack 알림(주문·오류·할트, no-op fallback)
6. 기동 자가점검(자격·TZ·필수파일)
7. 노출 한도(총/종목당 %)
8. 로그 회전

**Out of Scope** (deferred):
- bkit /control-plane 연동(Approach C)
- 다중 채널 알림(이메일/텔레그램) — Slack 1채널로 시작
- 웹 대시보드/원격 제어 UI — 키스위치 파일·systemctl로 충분
- 실계좌(실돈) 지원 — 모의투자 검증 후 별도 사이클

## Brainstorming Log (Plan Plus)

- **2026-06-08** Phase 0: 코드 완비 확인(`python main.py`→`start_scheduler`, 주문잡 기본 `dry_run=False` 실주문, `LIVE_STRATEGY=agent`). 알림 인프라·프로세스관리·가드 전무.
- Phase 1 의도: 무인 자동복구 + 안전 통제(실주문·미검증 완화 맥락).
- Phase 2 결정: **Approach B**(가드+외부 워치독) — hang 침묵실패 방어가 핵심.
- Phase 3 YAGNI: 8개 항목 전부 v1(사용자 선택). /control 연동·다채널 알림·대시보드·실계좌는 deferred.
