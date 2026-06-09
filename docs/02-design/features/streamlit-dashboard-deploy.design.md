# Design: Streamlit Dashboard Deploy

**Feature**: streamlit-dashboard-deploy
**작성일**: 2026-06-09
**선택 아키텍처**: **Option C — 독립 슬림앱** (자립형 `dashboard_app.py`, heavy import 0, app.py 불가침)
**상위 Plan**: `docs/01-plan/features/streamlit-dashboard-deploy.plan.md`
**대상**: Streamlit Community Cloud (~1GB RAM) · 동기화 = GitHub `dashboard-data` orphan 브랜치

---

## Context Anchor (Plan 승계)

| 항목 | 내용 |
|------|------|
| **WHY** | 무거운 app.py(FinBERT/KIS)는 Cloud 불가 + Cloud는 라이브 data 못 봄 → 슬림 읽기전용 + GitHub 동기화. |
| **WHO** | 운영자(본인) — 외부에서 운영 현황 조회. |
| **RISK** | 비밀(.env·토큰) 유출, RAM 초과, 데이터 지연, dashboard-data 브랜치 비대. |
| **SUCCESS** | 슬림앱 heavy import 0 구동 / Cloud 빌드 / allowlist push / 최신 표시 / 실주문 0 / 우분투 timer sync. |
| **SCOPE** | 신규 `dashboard_app.py`·`requirements-dashboard.txt`·`scripts/sync_dashboard_data.py`·`deploy/sync-dashboard.{service,timer}`·runbook·테스트. 불가침: app.py·실매매·신호엔진. |

---

## 1. Overview

Streamlit Cloud는 **1개 repo+브랜치+메인파일**에서 앱·requirements·데이터를 함께 읽는다. 따라서 우분투의
`sync_dashboard_data.py`가 **orphan `dashboard-data` 브랜치**를 매 sync마다 단일 커밋으로 재생성(force-push)
하되, 그 안에 **슬림 앱 코드(dashboard_app.py·requirements-dashboard.txt·.streamlit) + 큐레이트 data 서브셋**
(allowlist, 비밀 제외)을 담는다. Cloud는 이 브랜치를 배포 → 항상 최신 코드+데이터. 대시보드는 커밋된 data만
읽고 KIS·FinBERT·실주문을 일절 호출하지 않는다(읽기전용).

**핵심 통찰**:
1. Cloud는 단일 브랜치 배포 → 앱+데이터를 **같은 브랜치(dashboard-data)** 에 둬야 함 → orphan+force-push로 히스토리 1개 유지(비대 방지).
2. `app.py`는 `backtester`(→FinBERT)·`kis_broker`를 top-level import → 재사용 불가. **자립형 신규**가 정답(Option C).
3. 대시보드 데이터는 전부 파일(jsonl/json/csv/md) → Polygon/KIS 호출 없이 커밋 스냅샷만으로 렌더 가능 → **시크릿 0**.
4. 비밀 유출 차단은 **allowlist(명시 파일만 복사)** 가 denylist보다 안전.

---

## 2. Selected Architecture — Option C

```
[우분투 24h]  (실매매 본체, 무거운 FinBERT/KIS — 그대로)
   data/community/live/reports/*.md, decisions.jsonl, portfolio.json,
   daily_opinion_snapshots.jsonl, trades.csv
        │
        ▼  scripts/sync_dashboard_data.py   (deploy/sync-dashboard.timer, 주기)
   orphan 브랜치 'dashboard-data' 재생성(단일 커밋):
     ├─ dashboard_app.py              (메인 브랜치에서 복사)
     ├─ requirements-dashboard.txt    (슬림)
     ├─ .streamlit/config.toml
     ├─ data/<allowlist 서브셋>        (비밀 제외)
     └─ last_sync.json                ({synced_at})
   → git push --force origin dashboard-data
        │
        ▼
[GitHub] dashboard-data 브랜치
        ▼
[Streamlit Cloud]  branch=dashboard-data, main=dashboard_app.py, reqs=requirements-dashboard.txt
   → 커밋 data만 읽어 렌더. KIS/FinBERT/실주문 호출 0. 시크릿 0. "마지막 sync" 배지.
```

**왜 C인가**: app.py(heavy)를 안 건드리고(NFR-03 회귀 0), Cloud엔 처음부터 heavy 0인 자립앱만 올림(NFR-02 RAM). orphan+force-push로 데이터 누적 비대 방지. allowlist로 비밀 유출 차단(NFR-01).

---

## 3. 모듈 분해 (Module Map)

| 모듈 | 파일 | 책임 | --scope 키 |
|------|------|------|-----------|
| **M1 Slim app** | `dashboard_app.py`(신규)·`requirements-dashboard.txt`(신규)·`.streamlit/config.toml`(신규) | 커밋 data 읽어 패널 렌더(포트폴리오/매매이력/일일 funnel/여론추세 + 마지막 sync 배지). heavy import 0, KIS/실주문 0 | `module-1` |
| **M2 Sync** | `scripts/sync_dashboard_data.py`(신규)·`deploy/sync-dashboard.{service,timer}`(신규) | allowlist 서브셋 + 슬림앱 코드를 orphan `dashboard-data`에 단일커밋 force-push. 우분투 timer | `module-2` |
| **M3 Tests/Docs** | `tests/test_sync_dashboard.py`(신규)·`docs/ops/streamlit-dashboard.md`(신규) | allowlist(비밀 제외) 검증 + Cloud 배포 runbook | `module-3` |

---

## 4. 핵심 설계 결정

| ID | 결정 | 내용 |
|----|------|------|
| **D1** | 자립형 슬림앱 | `dashboard_app.py`는 `streamlit·pandas·altair·json·pathlib`만. `backtester/indicators/kis_broker/community_live/collector/signals/config의 heavy 경로` 미import. config는 상수만 얕게 참조(또는 자체 상수). |
| **D2** | allowlist sync | 복사 대상 **명시 목록만**: `data/community/live/reports/`, `data/community/live/decisions.jsonl`, `data/portfolio.json`, `data/trades.csv`, `data/community/daily_opinion_snapshots.jsonl`(또는 최근 N일 요약). **제외 기본** — `.env`·`data/kis_token.json`·`models/`·`*cache*`·키. |
| **D3** | orphan 브랜치 force-push | `dashboard-data`를 매 sync `git checkout --orphan` 재생성 → 단일 커밋 → `push --force`. 히스토리 1개(비대 방지). 메인 브랜치 히스토리 무오염. |
| **D4** | 시크릿 0 | 대시보드는 파일만 읽음 → KIS/Polygon 키 불필요. Cloud 시크릿 비움. (NFR-01) |
| **D5** | 민감수치 마스킹 | 공개앱이므로 계좌번호·실투자금 절대액 등은 표시 안 함/마스킹. 비율·신호 위주. |
| **D6** | 마지막 sync 배지 | `last_sync.json` 읽어 "데이터 기준 시각" 표시(준실시간 명시, 실시간 오해 방지, NFR-04). |
| **D7** | 코드 전달 방식 | sync가 메인 워킹트리의 `dashboard_app.py`·`requirements-dashboard.txt`·`.streamlit/`을 orphan 브랜치에 함께 복사 → Cloud가 항상 최신 코드+데이터. |

---

## 5. 데이터 구조

### 5.1 dashboard-data 브랜치 레이아웃 (sync 산출)
```
dashboard_app.py
requirements-dashboard.txt
.streamlit/config.toml
last_sync.json                    # {"synced_at": "2026-06-09T13:40:00Z", "source_commit": "<sha>"}
data/
  portfolio.json
  trades.csv
  community/live/reports/*.md
  community/live/decisions.jsonl
  community/daily_opinion_snapshots.jsonl
```

### 5.2 sync allowlist (scripts 상수)
```python
SYNC_ALLOWLIST = [
    "data/portfolio.json",
    "data/trades.csv",
    "data/community/live/reports",            # 디렉터리
    "data/community/live/decisions.jsonl",
    "data/community/daily_opinion_snapshots.jsonl",
]
SYNC_CODE = ["dashboard_app.py", "requirements-dashboard.txt", ".streamlit/config.toml"]
# 절대 금지(방어적 차단): .env, data/kis_token.json, models/, *cache*, *.key, *secret*
DENY_SUBSTR = [".env", "kis_token", "models/", "cache", "secret", ".key"]
```

---

## 6. 변경 상세 & Open Issues

### 6.1 M1 `dashboard_app.py`
- 패널: ① 포트폴리오(현금·보유·평가, 마스킹) ② 매매 이력(trades.csv) ③ 일일 결정 funnel(최근 reports/*.md 렌더 or decisions.jsonl 집계) ④ 여론 추세(daily_opinion_snapshots 요약 차트) ⑤ 상단 "마지막 sync: …" 배지.
- 데이터 없음/빈 파일 graceful(안내 메시지). 경로는 repo 루트 상대.
- `requirements-dashboard.txt`: `streamlit`, `pandas`, `altair`. (torch/transformers/onnxruntime/optimum/openai/praw/polygon/kis 전부 **제외**)

### 6.2 M2 `scripts/sync_dashboard_data.py`
- allowlist+code를 임시 워크트리/스테이징에 복사 → DENY_SUBSTR 재검사(이중 안전) → `git worktree`/`checkout --orphan dashboard-data` → add → 단일 커밋 → `push --force`.
- `last_sync.json` 생성(synced_at, source_commit).
- `deploy/sync-dashboard.{service,timer}`: 주문잡 후 또는 N분 주기 실행.

### 6.3 Open Issues (구현 중 확정)
- **Open-1 (D3)**: orphan force-push vs `git worktree add`로 분리 워크트리 사용 — 메인 작업트리 오염 없이 안전한 방식 확정.
- **Open-2 (D2)**: snapshots 전체(대용량) vs 최근 N일만 — 브랜치 크기/렌더 성능 고려, 최근 N일 요약 1순위.
- **Open-3 (D5)**: 마스킹 범위(계좌번호 확실 제외, 평가금 절대액 표시 여부) — 운영자 판단.
- **Open-4**: push 인증 — 우분투에서 GitHub PAT/deploy key(읽기쓰기) 설정(runbook).

---

## 7. Test Plan (M3)

| TC | 시나리오 | 검증 |
|----|----------|------|
| TC-01 | allowlist 선별 | 지정 파일만 스테이징, 그 외 제외 |
| TC-02 | 비밀 차단 | `.env`·`kis_token.json`·`models/`·cache가 절대 포함 안 됨(DENY 재검사) |
| TC-03 | last_sync 생성 | synced_at·source_commit 기록 |
| TC-04 | dashboard_app import | heavy 모듈(torch/transformers/kis_broker) 미import (import 그래프 점검) |
| TC-05 | 빈 데이터 graceful | 파일 없음 시 앱 크래시 안 함 |
| TC-06 | 회귀 | 기존 전체 테스트 통과(app.py·실매매 무영향) |

검증: `python tests/test_sync_dashboard.py` + 로컬 `streamlit run dashboard_app.py`. 실제 push/Cloud는 수동.

---

## 8. Implementation Guide

### 8.1 구현 순서
1. **M1** `dashboard_app.py`(자립·읽기전용) + `requirements-dashboard.txt` + `.streamlit/config.toml`.
2. **M2** `scripts/sync_dashboard_data.py`(allowlist+code→orphan force-push) + `deploy/sync-dashboard.{service,timer}`.
3. **M3** `tests/test_sync_dashboard.py` + `docs/ops/streamlit-dashboard.md` + 회귀.
4. 로컬 `streamlit run` 확인 → sync 1회 → Cloud 앱 생성(branch=dashboard-data, main=dashboard_app.py, reqs=requirements-dashboard.txt) → 표시 확인.

### 8.2 코드 주석 규약
- `# Design Ref: §{n}` / `# Plan SC: {SC-id}`

### 8.3 Session Guide (--scope)

| 세션 | scope | 모듈 | 선행 |
|------|-------|------|------|
| S1 | `module-1` | 슬림 대시보드 + 슬림 requirements (로컬 streamlit run 확인) | — |
| S2 | `module-2,module-3` | sync 스크립트 + timer + 테스트 + runbook | S1 |

권장: `/pdca do streamlit-dashboard-deploy --scope module-1` → `--scope module-2,module-3`.

---

## 9. 영향 범위 요약

- **신규**: `dashboard_app.py`·`requirements-dashboard.txt`·`.streamlit/config.toml`·`scripts/sync_dashboard_data.py`·`deploy/sync-dashboard.{service,timer}`·`docs/ops/streamlit-dashboard.md`·`tests/test_sync_dashboard.py`
- **수정**: 없음(app.py·실매매·스케줄러 불가침). 단 `.gitignore`에 dashboard-data 관련 임시 워크트리 경로 추가 가능.
- **예상 변경량**: ~250–350 LOC (대시보드 + sync)
- **불가침**: app.py·community_live·scheduler·신호 엔진·KIS
