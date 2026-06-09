# Plan: Streamlit Dashboard Deploy — 읽기전용 슬림 대시보드 + GitHub 데이터 동기화

**Feature**: streamlit-dashboard-deploy
**작성일**: 2026-06-09
**상위 피처**: live-scheduler-deploy(우분투 실매매 본체), daily-decision-report
**대상**: Streamlit Community Cloud (무료, ~1GB RAM) · 데이터 동기화 = GitHub 푸시

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 현 `app.py`는 backtester→FinBERT(torch 418MB)·KIS 실연결·`os.getenv` 시크릿을 끌어와 Streamlit Cloud(1GB RAM)에서 **빌드/OOM/모델누락/시크릿 불일치**로 그냥 배포 불가. 게다가 Cloud는 우분투 박스의 **라이브 데이터를 못 봄**. |
| **Solution** | ① FinBERT·KIS·실주문 import 없는 **읽기전용 슬림 대시보드** + 슬림 requirements. ② 우분투 박스가 큐레이트된 data 서브셋(리포트·decision_log·portfolio·스냅샷 요약)을 **`dashboard-data` 브랜치로 주기 git push** → Cloud가 그 브랜치를 읽어 최신 반영. |
| **Function UX Effect** | Streamlit Cloud URL 1개로 어디서나 포트폴리오·일일 결정 funnel·매매 이력·여론 추세를 조회. 실매매(우분투)와 **역할 분리**. |
| **Core Value** | 무거운 실매매는 우분투, 가벼운 공개 조회는 Cloud — 안전(실주문 X)·저비용(추가 인프라 0)·최신(주기 push)으로 운영 가시성 확보. |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 현 app.py는 Cloud에 못 올림(무거움·모델누락·시크릿·라이브데이터 부재). 가벼운 읽기전용 + 데이터 동기화로 해결. |
| **WHO** | 운영자(본인) — 외부에서 운영 현황 조회. |
| **RISK** | 비밀(.env·KIS 토큰) Cloud/브랜치 유출, RAM 초과, 데이터 지연, dashboard-data 브랜치 비대. |
| **SUCCESS** | 슬림앱 torch/FinBERT/KIS 없이 구동 / Cloud 빌드 성공 / sync가 비밀 제외 서브셋만 push / Cloud 최신 표시 / 실주문 호출 0 / 우분투 timer 주기 sync. |
| **SCOPE** | 신규: `dashboard_app.py`·`requirements-dashboard.txt`·`scripts/sync_dashboard_data.py`·`deploy/sync-dashboard.{service,timer}`·runbook·테스트. 불가침: 실매매·스케줄러·신호 엔진·기존 app.py(로컬용 유지). |

---

## 1. 현재 상태 (As-Is) — 배포 블로커

| 블로커 | 근거 |
|--------|------|
| 무거운 의존성 | `requirements.txt`에 torch·transformers·optimum·onnxruntime → 1GB RAM 초과 위험 |
| FinBERT 모델 418MB 비-git | `app.py`가 `from backtester import BacktestEngine`(→indicators→FinBERT) import |
| KIS 실연결 | `app.py:64-76` `broker.connect()`·`sync_from_kis`·`get_quote` |
| 시크릿 불일치 | config는 `os.getenv`, Cloud는 `st.secrets` |
| 라이브 데이터 부재 | Cloud는 커밋 스냅샷만 — 우분투 라이브 data 없음 |

---

## 2. 목표 동작 (To-Be)

```
[우분투 24h 실매매]  data/ (리포트·decision_log·portfolio·snapshot)
     │ scripts/sync_dashboard_data.py  (systemd timer, 주문잡 후/주기)
     │   → 큐레이트 서브셋만 dashboard-data 브랜치에 commit+push (비밀 제외)
     ▼
[GitHub] dashboard-data 브랜치
     ▼
[Streamlit Cloud]  dashboard_app.py (읽기전용, 슬림 deps, KIS/FinBERT X)
     → 커밋된 data만 읽어 표시. 실주문·실연결 0.
```

---

## 3. 기능 요구사항

### FR
| ID | 내용 |
|----|------|
| FR-01 | **슬림 대시보드** `dashboard_app.py`: streamlit·pandas·altair만. `backtester/indicators/kis_broker/collector/signals/community_live` **미import**. 커밋된 `data/`·리포트만 표시(포트폴리오·일일 결정 funnel·매매 이력·여론 추세). |
| FR-02 | **슬림 requirements** `requirements-dashboard.txt`: streamlit·pandas·altair·python-dotenv(±pandas_market_calendars). **torch·transformers·optimum·onnxruntime·openai·praw·polygon 제외**. |
| FR-03 | **데이터 sync** `scripts/sync_dashboard_data.py`: 큐레이트 서브셋(`data/community/live/reports/`, `data/community/live/decisions.jsonl`, `portfolio.json`, `daily_opinion_snapshots` 요약, `trades.csv`)을 **`dashboard-data` 브랜치에 commit+push**. `.env`·`kis_token.json`·캐시·model **제외**(allowlist 방식). |
| FR-04 | **우분투 sync 자동화** `deploy/sync-dashboard.{service,timer}`: 주문잡 후 또는 N분 주기 sync 실행. |
| FR-05 | **Cloud 설정 가이드**: 앱 파일=`dashboard_app.py`, 브랜치=`dashboard-data`, requirements=`requirements-dashboard.txt`, 시크릿 최소(읽기전용이면 0). |
| FR-06 | **읽기전용 보장**: 대시보드는 KIS·실주문·외부 쓰기 호출 0. (공개앱 안전) |
| FR-07 | **runbook** `docs/ops/streamlit-dashboard.md`: Cloud 가입·앱 생성·브랜치/파일/requirements 지정·시크릿·sync 등록. |

### NFR
| ID | 내용 |
|----|------|
| NFR-01 | **비밀 미노출** — sync는 allowlist(명시 파일만), `.env`·토큰·키 절대 push 금지. Cloud 시크릿은 매니저로만. |
| NFR-02 | **RAM < 1GB** — 슬림 deps(FinBERT/torch 없음)로 무료 티어 적합. |
| NFR-03 | **기존 무영향** — 로컬 `app.py`·실매매·스케줄러 무수정. dashboard_app.py는 별도 파일. |
| NFR-04 | **데이터 지연 허용** — 준실시간(주기 push). 실시간 아님 명시. |

---

## 4. 변경/신규 파일

| 파일 | 구분 | 변경 |
|------|------|------|
| `dashboard_app.py` | 신규 | 읽기전용 슬림 Streamlit (app.py에서 조회 패널만 추출, 무거운 import 제거) |
| `requirements-dashboard.txt` | 신규 | 슬림 의존성 |
| `scripts/sync_dashboard_data.py` | 신규 | allowlist 서브셋 → dashboard-data 브랜치 push |
| `deploy/sync-dashboard.service` + `.timer` | 신규 | 우분투 주기 sync |
| `docs/ops/streamlit-dashboard.md` | 신규 | Cloud 배포 runbook |
| `tests/test_sync_dashboard.py` | 신규 | allowlist(비밀 제외)·페이로드 검증 |
| `.streamlit/config.toml` | 신규(선택) | 테마/서버 옵션 |

---

## 5. 성공 기준

| SC | 기준 | 검증 |
|----|------|------|
| SC-01 | `dashboard_app.py`가 torch/FinBERT/KIS 미import로 구동 | import 그래프 점검 + 로컬 streamlit run |
| SC-02 | 슬림 requirements로 Cloud 빌드 성공(RAM 내) | Cloud 배포 |
| SC-03 | sync가 allowlist 서브셋만 push(.env·토큰 제외) | 테스트(파일목록 검증) |
| SC-04 | Cloud가 dashboard-data 최신 데이터 표시 | sync 후 Cloud 새로고침 |
| SC-05 | 대시보드 KIS·실주문 호출 0 | 코드 점검(grep) |
| SC-06 | 우분투 timer 주기 sync 동작 | 서버 수동 |
| SC-07 | 기존 app.py·실매매 회귀 0 | 전체 테스트 |

---

## 6. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 비밀(.env·토큰) push 유출 | 높음 | NFR-01 allowlist(명시 파일만), 테스트로 차단 검증 |
| RAM 초과/빌드 실패 | 중간 | 슬림 requirements, FinBERT/torch 제외 |
| dashboard-data 브랜치 비대(매 push 누적) | 중간 | orphan 브랜치 + force-push(히스토리 1개 유지) 또는 단일 커밋 amend |
| 데이터 지연 오해 | 낮음 | 대시보드에 "마지막 sync 시각" 표시 |
| 공개앱 노출 범위 | 중간 | 읽기전용 + 민감수치 마스킹 검토(계좌번호 등) |

---

## 7. 구현 순서 (예정)

1. `dashboard_app.py` — app.py에서 조회 패널 추출 + 무거운 import 제거(커밋 data만 읽기).
2. `requirements-dashboard.txt` 슬림 의존성.
3. `scripts/sync_dashboard_data.py` — allowlist 서브셋 → dashboard-data 브랜치 push.
4. `tests/test_sync_dashboard.py`(allowlist/비밀 제외) + 회귀.
5. `deploy/sync-dashboard.{service,timer}` + `docs/ops/streamlit-dashboard.md`.
6. Cloud 가입·앱 생성(브랜치/파일/requirements 지정)·sync 1회 후 표시 확인.

---

## 8. 가장 중요한 제약

- **읽기전용·실주문 0**: 공개 대시보드는 조회만. KIS·실매매·FinBERT 미사용 (FR-06, NFR-02).
- **비밀 allowlist**: sync는 명시 파일만 push, `.env`·`kis_token.json`·키 절대 제외 (NFR-01).
- **역할 분리**: 실매매=우분투, 조회=Cloud. 기존 app.py/스케줄러 무영향 (NFR-03).
- **준실시간**: GitHub push 동기화라 분~수분 지연 — 실시간 아님 (NFR-04).
