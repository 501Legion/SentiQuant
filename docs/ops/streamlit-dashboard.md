# Runbook: Streamlit Cloud 대시보드 (읽기전용)

> streamlit-dashboard-deploy. 무거운 실매매는 우분투, 가벼운 공개 조회는 Streamlit Cloud. 역할 분리.

## 0. 구조
```
[우분투] scripts/sync_dashboard_data.py (sync-dashboard.timer, 30분)
   → orphan 'dashboard-data' 브랜치에 슬림앱+데이터(allowlist) force-push
        ▼
[Streamlit Cloud]  branch=dashboard-data, main file=dashboard_app.py, reqs=requirements-dashboard.txt
```

## 1. 우분투: 동기화 등록

```bash
# 0) GitHub 푸시 인증 (force-push 권한) — PAT 또는 deploy key
#    예: git remote set-url origin https://<PAT>@github.com/501Legion/SentiQuant.git

# 1) 로컬 검증 (push 없이 curate만)
./venv/bin/python scripts/sync_dashboard_data.py --no-push   # 비밀 제외 확인

# 2) 실제 1회 동기화 (dashboard-data 브랜치 생성/force-push)
./venv/bin/python scripts/sync_dashboard_data.py

# 3) 타이머 등록 (30분마다 자동 sync)
sudo cp deploy/sync-dashboard.service deploy/sync-dashboard.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sync-dashboard.timer
systemctl list-timers sync-dashboard
```

## 2. Streamlit Community Cloud 배포

1. https://share.streamlit.io 가입 (GitHub 연동)
2. **New app** →
   - Repository: `501Legion/SentiQuant`
   - **Branch: `dashboard-data`**
   - **Main file: `dashboard_app.py`**
   - Advanced → **Requirements: `requirements-dashboard.txt`** (자동 인식 안 되면 지정)
3. Deploy → 빌드(슬림 deps라 RAM 내). URL 발급.
4. **Secrets: 비워둠** (읽기전용 — 키 불필요).

> dashboard-data 브랜치엔 `dashboard_app.py`·`requirements-dashboard.txt`·`.streamlit/`·`data/`(서브셋)·`last_sync.json`만 있음. torch/모델/비밀 없음.

## 3. 안전 (Plan NFR-01)
- sync는 **allowlist 명시 파일만** 복사 + DENY 재검사 → `.env`·`kis_token.json`·`models/`·cache 절대 제외.
- 대시보드는 **읽기전용** — KIS·실주문·FinBERT 호출 0.
- dashboard-data는 **orphan + force-push 단일 커밋** → 히스토리 비대 없음.

## 4. 확인
- Cloud URL 접속 → 상단 "데이터 기준 시각"(last_sync) 배지 확인.
- 4개 탭(포트폴리오/매매이력/일일 funnel/여론추세) 렌더.
- 30분 후 sync → Cloud 자동 재배포(브랜치 갱신 감지) → 최신 반영.

## 5. 장애 대응
| 증상 | 조치 |
|------|------|
| Cloud 빌드 OOM/실패 | requirements-dashboard.txt에 무거운 패키지 섞였는지 확인 |
| 데이터 안 보임 | dashboard-data 브랜치에 data/ 들어갔는지 `git ls-tree origin/dashboard-data` |
| Cloud 화면만 옛날 상태(브랜치는 갱신됨) | **GitHub 리포 리네임 후 Cloud 소스 연결 끊김**. share.streamlit.io에서 앱 Repository를 `501Legion/SentiQuant`로 변경 후 Reboot, 안 되면 앱 삭제→New app 재생성 (Secrets 비어있어 부담 없음) |
| sync push 실패 | 우분투 GitHub 인증(PAT/deploy key) 확인 |
| 비밀 노출 우려 | `git ls-tree -r origin/dashboard-data`로 .env/token 없는지 점검 |
