# Design: Live Scheduler Deploy

**Feature**: live-scheduler-deploy
**작성일**: 2026-06-08
**선택 아키텍처**: **Option C — 실용 균형** (`runtime_guard.py` 순수 정책+IO분리 + `notifier.py` + scheduler 얇은 훅 + 외부 워치독)
**상위 Plan**: `docs/01-plan/features/live-scheduler-deploy.plan.md`
**대상**: 24h Ubuntu + systemd · KIS 모의투자 실주문

---

## Context Anchor (Plan 승계)

| 항목 | 내용 |
|------|------|
| **WHY** | 무인 자동복구(crash+hang) + 실주문 안전 통제. 미검증 게이트 완화 상태라 통제 필수. |
| **WHO** | 운영자(본인) — 24h 우분투에 띄워두고 결과만 확인. |
| **RISK** | 무인 실주문 폭주/침묵 실패, fresh clone 안 돎(model 418MB·praw·.env·py3.11), 자격·시간대. |
| **SUCCESS** | systemd 복구 / 키스위치 / 일일·노출 한도 / 알림 / 헬스·자가점검 / 워치독(hang) / 판단 로직 불변. |
| **SCOPE** | 신규 `runtime_guard`·`notifier`·deploy·watchdog·provisioning. 수정 `scheduler`·`community_live`(주문 게이트)·`config`·`requirements`. 불가침: 신호/사이징/라우터 판단. |

---

## 1. Overview

24h 우분투에서 `python main.py`(→`start_scheduler`)를 **systemd 서비스**로 상시 구동(crash→Restart, hang→워치독 재시작). 주문잡(09:35 ET, 기본 실주문)에 **얇은 안전 훅**을 추가하되, 정책 로직은 `runtime_guard.py`(순수+IO분리)로 분리해 테스트 가능하게 한다. 알림은 `notifier.py`(Slack webhook, 미설정 no-op). 판단 로직(신호/사이징/라우터)은 무수정 — 안전장치는 전부 **주문 실행 전 게이트/관측**으로만.

**핵심 통찰**:
1. 진입점·실주문은 이미 동작(`main.py:376`→`start_scheduler`, 주문잡 기본 `dry_run=False`, `LIVE_STRATEGY=agent`→`community_live.run_live`). → 코드 변경은 **가드/관측/배포**에 집중.
2. `start_scheduler`는 잡 시작/종료 훅 지점이 명확(`order_processing_job`). → 잡 시작 시 자가점검+키스위치, 종료 시 heartbeat, except 시 알림.
3. 일일/노출 한도는 `community_live`의 **매수 실행 루프 직전**(buy_intents 실행 `:459` 앞)에서 게이트 → 신호/사이징 불변.
4. hang은 `Restart=always`가 못 잡음 → **외부 워치독**(systemd timer)이 heartbeat 신선도로 감지.

---

## 2. Selected Architecture — Option C

```
[systemd] auto-stock.service (Restart=always, enable)
   └─ python main.py → scheduler.start_scheduler()
        └─ order_processing_job (09:35 ET)
             ├─ runtime_guard.selfcheck()      → 실패 시 abort + notifier(헬스실패)   [기동/잡 시작]
             ├─ runtime_guard.is_halted()       → True면 주문 스킵(스케줄러 유지)
             ├─ community_live.run_live(dry_run=False)
             │     └─ [매수 실행 직전] runtime_guard.filter_by_limits(buy_intents, portfolio)
             │            → 일일 매수 건수·총/종목당 노출 초과분 차단
             ├─ runtime_guard.write_heartbeat("order", ok)                         [잡 종료]
             └─ except → notifier(오류)
        └─ signal_calculation_job (16:30 ET) → 동일 heartbeat/except 훅

[systemd timer] watchdog.timer (N분) → scripts/watchdog_check.py
   └─ heartbeat.json stale? → notifier(stale) + systemctl restart auto-stock
```

**왜 C인가**: 정책(키스위치·한도·자가점검)을 `runtime_guard`의 **순수 함수**로 빼면 단위테스트가 쉽고(SC-04/06), `scheduler`/`community_live`엔 **호출 한 줄씩**만 추가돼 판단 로직 불변·회귀 0(NFR-01/02). A(인라인)는 결합·테스트난, B(레이어 추상화)는 이 규모에 과설계.

---

## 3. 모듈 분해 (Module Map)

| 모듈 | 파일 | 책임 | --scope 키 |
|------|------|------|-----------|
| **M1 Guard core** | `runtime_guard.py`(신규), `config.py`(수정) | 키스위치·일일/노출 한도·heartbeat·selfcheck (순수 정책 + IO 분리), 상수 | `module-1` |
| **M2 Notifier** | `notifier.py`(신규) | Slack webhook 발송(미설정 no-op), 비밀 마스킹 | `module-2` |
| **M3 Wiring** | `scheduler.py`·`community_live.py`(수정) | 잡 시작 selfcheck/halt, 종료 heartbeat, except 알림 / 매수 실행 직전 한도 게이트 | `module-3` |
| **M4 Tests** | `tests/test_runtime_guard.py`(신규) | 키스위치·한도·heartbeat·selfcheck·알림 no-op·마스킹 + 회귀 | `module-4` |
| **M5 Deploy/Provision** | `deploy/*`, `scripts/watchdog_check.py`, `requirements.txt`, `.env.example`, `docs/ops/live-scheduler.md` | systemd 서비스/워치독 timer, 워치독 검사, praw 추가, env 템플릿, runbook(모델 scp·py3.11) | `module-5` |

---

## 4. 핵심 설계 결정

| ID | 결정 | 내용 |
|----|------|------|
| **D1** | 가드 = 순수+IO분리 | `runtime_guard` 정책 함수는 입력→판정(순수), 파일 IO(halt/heartbeat/portfolio 읽기)는 얇은 래퍼. 테스트는 순수 fn 단위. |
| **D2** | 일일/노출 한도 = 주문 실행 게이트 | `community_live` 매수 실행 루프 직전 `filter_by_limits(buy_intents, portfolio, today_orders)` → 초과분만 제거. 신호/사이징 무수정(NFR-01). 매도·청산은 한도 무관(리스크 축소 방향). |
| **D3** | 키스위치 = 파일 OR env | `data/TRADING_HALT` 파일 존재 **또는** `TRADING_HALT=1` → 주문만 스킵, 스케줄러·수집·로그 유지(FR-05). 둘 중 하나라도 halt. |
| **D4** | selfcheck 실패 = 주문 차단 | 기동/잡 시작 시 KIS 자격·Reddit/Polygon 키·TIMEZONE·필수 파일 점검. 실패 시 **주문 차단 + 알림**(의도적, graceful 예외). |
| **D5** | heartbeat = 잡 성공 시각 | 각 잡 성공 시 `data/heartbeat.json` `{job: ts}` 갱신. 워치독이 신선도 판단. |
| **D6** | 워치독 = 외부·관측만 | systemd timer가 `watchdog_check.py` 주기 실행. stale이면 알림+`systemctl restart`. **워치독은 주문 안 함**. |
| **D7** | notifier no-op | `SLACK_WEBHOOK_URL` 미설정 시 발송 안 함(예외 없음). 페이로드에 비밀 평문 금지(마스킹). |
| **D8** | 일일 주문 상태 출처 | 오늘 매수 건수는 `data/daily_orders.json`(날짜·count) 또는 decision_log 당일 BUY 집계 — **구현 시 확정**(decision_log 재사용 우선, 신규 파일 최소화). |

---

## 5. 데이터 구조

### 5.1 heartbeat.json
```json
{"order": "2026-06-08T13:35:10Z", "signal": "2026-06-07T20:30:05Z"}
```

### 5.2 키스위치
- 파일: `data/TRADING_HALT`(존재=halt, 내용 무관) / env: `TRADING_HALT=1`

### 5.3 한도 config (M1)
```python
TRADING_HALT_FILE = "data/TRADING_HALT"
HEARTBEAT_FILE = "data/heartbeat.json"
MAX_DAILY_BUYS = 5                 # 하루 신규 매수 건수 상한
MAX_TOTAL_EXPOSURE_PCT = 60.0      # 총 투자금 대비 보유 평가액 상한 %
MAX_SYMBOL_WEIGHT_PCT = 20.0       # 종목당 비중 상한 %
WATCHDOG_STALE_MINUTES = 90        # heartbeat 이보다 오래되면 hang 추정
SLACK_WEBHOOK_URL = ""             # 미설정 시 알림 no-op
```

### 5.4 filter_by_limits 계약 (순수)
```python
def filter_by_limits(buy_intents, *, equity, positions, today_buy_count,
                     prices) -> tuple[list, list[str]]:
    """한도 통과 buy_intents, 차단 사유 리스트 반환. 매도/청산 무관."""
```

---

## 6. 변경 상세 & Open Issues

### 6.1 M1 `runtime_guard.py`
- `is_halted() -> bool`: TRADING_HALT 파일 or env.
- `filter_by_limits(...)`: 일일 건수(MAX_DAILY_BUYS) + 노출(총/종목당) 초과분 제거, 사유 반환(순수).
- `write_heartbeat(job)` / `read_heartbeat()` / `heartbeat_stale(job, now, minutes) -> bool`.
- `selfcheck() -> list[str]`: 누락 항목 목록(빈 리스트=정상). 자격/TZ/필수파일.

### 6.2 M3 wiring
- `scheduler.order_processing_job` 시작부: `fails=selfcheck(); if fails: notify("healthcheck", fails); return`(주문 차단). `if is_halted(): logger.warning; (주문 스킵)`. 종료부 `write_heartbeat("order")`. except → `notify("error", ...)`.
- `signal_calculation_job` 종료부 `write_heartbeat("signal")`.
- `community_live.run_live` 매수 실행 루프(`:459`) 직전: `buy_intents, blocked = filter_by_limits(buy_intents, ...)`; blocked 사유 로그/리포트. **dry_run/posts 로직 불변.**

### 6.3 M5 deploy/provision
- `deploy/auto-stock.service`: `ExecStart=/opt/auto-stock/venv/bin/python main.py`, `Restart=always`, `RestartSec=10`, `EnvironmentFile`, `WorkingDirectory`, `After=network-online.target`, `WantedBy=multi-user.target`.
- `deploy/watchdog.service`+`.timer`: `OnUnitActiveSec=WATCHDOG간격`, `ExecStart=python scripts/watchdog_check.py`.
- `scripts/watchdog_check.py`: heartbeat_stale면 exit 1 + notify + (`--restart` 시 systemctl restart).
- `requirements.txt`: **praw 추가**. `.env.example` 작성. runbook: 모델 `scp models/finbert-onnx/ →`, py3.11 venv, systemctl 절차.

### 6.4 Open Issues (구현 중 확정)
- **Open-1 (D8)**: 오늘 매수 건수 출처 — decision_log 당일 BUY 집계 vs `daily_orders.json`. decision_log 재사용 1순위.
- **Open-2**: 노출% 계산의 equity 기준(현금+평가액) — community_live `account_equity` 재사용.
- **Open-3**: 워치독 `systemctl restart` 권한(서비스 유저 sudoers 또는 user-service). runbook 명시.
- **Open-4**: selfcheck의 KIS 자격 점검을 "토큰 발급 시도"까지 할지(비용) vs 키 존재만. 키 존재 우선.

---

## 7. Test Plan (M4)

| TC | 시나리오 | 검증 |
|----|----------|------|
| TC-01 | 키스위치 파일/env | `is_halted()` True/False |
| TC-02 | 일일 매수 건수 한도 | today_buy_count≥MAX → 신규 매수 전량 차단, 사유 |
| TC-03 | 노출 한도(총/종목당) | 초과분 차단, 통과분 유지 |
| TC-04 | 매도/청산 무관 | filter_by_limits가 sell/exit 안 건드림 |
| TC-05 | heartbeat 기록/신선도 | write→read, stale 판정 |
| TC-06 | selfcheck | 키 누락/필수파일 없음 → 항목 반환 |
| TC-07 | notifier no-op·마스킹 | URL 미설정 시 무발송, 비밀 마스킹 |
| TC-08 | 워치독 검사 | stale heartbeat → exit 1 + notify 호출 |
| TC-09 | 회귀 | 기존 140 + scheduler/community_live 무영향 |

검증: `python tests/test_runtime_guard.py` (단독 러너). systemd/워치독 실동작은 서버 수동(SC-01/02/09).

---

## 8. Implementation Guide

### 8.1 구현 순서
1. **M1** config 상수 + `runtime_guard.py`(순수 정책 + IO).
2. **M2** `notifier.py`.
3. **M3** scheduler 훅 + community_live 매수 게이트.
4. **M4** `tests/test_runtime_guard.py` + 전체 회귀.
5. **M5** deploy 유닛·워치독·requirements(praw)·.env.example·runbook.
6. 서버: scp 모델 → py3.11 venv → pip install → .env → `systemctl enable --now` → crash/hang/정시 수동 검증.

### 8.2 코드 주석 규약
- `# Design Ref: §{n}` / `# Plan SC: {SC-id}`

### 8.3 Session Guide (--scope)

| 세션 | scope | 모듈 | 선행 |
|------|-------|------|------|
| S1 | `module-1,module-2` | guard core + notifier (순수, 테스트 쉬움) | — |
| S2 | `module-3,module-4` | wiring + 테스트 + 회귀 | S1 |
| S3 | `module-5` | deploy/워치독/provisioning/runbook | S2 |

권장: `/pdca do live-scheduler-deploy --scope module-1,module-2` → `--scope module-3,module-4` → `--scope module-5`.

---

## 9. 영향 범위 요약

- **신규**: `runtime_guard.py`·`notifier.py`·`scripts/watchdog_check.py`·`deploy/{auto-stock.service,watchdog.service,watchdog.timer}`·`.env.example`·`docs/ops/live-scheduler.md`·`tests/test_runtime_guard.py`
- **수정**: `scheduler.py`(훅 ~15 LOC)·`community_live.py`(매수 게이트 ~8 LOC)·`config.py`(상수)·`requirements.txt`(praw)
- **불가침**: 신호 엔진·agent_gate·사이징·라우터·reddit_backtester·뉴스 경로
- **예상 변경량**: ~250–350 LOC (대부분 신규 가드/배포)
