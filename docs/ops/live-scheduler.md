# Runbook: Live Scheduler (24h Ubuntu, KIS 모의투자 실주문)

> live-scheduler-deploy. systemd로 상시 구동(crash→Restart, hang→워치독). 매 거래일 09:35 ET 실주문.

## 0. 사전 점검 (fresh clone은 그냥 안 돎)

| 항목 | 비고 |
|------|------|
| **Python 3.11** | 3.13은 torch `c10.dll` 깨짐. `python3.11 -m venv venv` |
| **FinBERT 모델 418MB** | `.gitignore: models/` → clone에 없음. **개발 PC에서 scp 필요**(아래) |
| **.env** | `.gitignore` 대상 → 수동 작성(아래) |
| **praw** 등 | `pip install -r requirements.txt` |

## 1. 설치

```bash
# 1) 클론
git clone <repo> /opt/auto-stock && cd /opt/auto-stock

# 2) Python 3.11 venv + 의존성 — 헬퍼 한 줄 (CPU torch 먼저)
bash scripts/install_server.sh
#   ⚠️ 그냥 `pip install -r requirements.txt`만 하면 Linux는 CUDA torch ~3GB 받음(GPU 없으면 낭비).
#      헬퍼가 CPU torch(--index-url .../whl/cpu)를 먼저 깔아 회피.

# 3) FinBERT 모델 — scp 불필요! 첫 --agent-run-now 시 HuggingFace에서 자동 다운로드+ONNX 변환(~1분).
#    (시간 아끼려면 개발PC에서 scp -r models/finbert-onnx auto-stock@SERVER:/opt/auto-stock/models/ — 선택)

# 4) .env 작성
cp .env.example .env && nano .env        # KIS/Reddit/Polygon 키, KIS_PAPER_TRADING=true

# 5) 자가점검 (자격/모델/paper/TZ — 누락 시 항목 출력)
./venv/bin/python -c "import runtime_guard as r; print(r.selfcheck() or 'OK')"
```

## 2. systemd 등록

```bash
# 유저/경로 맞게 deploy/*.service 수정 후
sudo cp deploy/auto-stock.service deploy/watchdog.service deploy/watchdog.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now auto-stock.service     # 부팅 자동시작 + 즉시 시작
sudo systemctl enable --now watchdog.timer         # 워치독 15분 주기
```

> 워치독이 `systemctl restart` 하려면 서비스 유저에 권한 필요:
> `/etc/sudoers.d/auto-stock` → `auto-stock ALL=NOPASSWD: /bin/systemctl restart auto-stock` (또는 user-level service).

## 3. 운영 명령

```bash
systemctl status auto-stock          # 상태
journalctl -u auto-stock -f          # 실시간 로그 (또는 data/trading.log)
systemctl restart auto-stock         # 재시작
systemctl stop auto-stock            # 중지
systemctl list-timers watchdog       # 워치독 다음 실행 확인
```

## 4. 키스위치 (즉시 주문 중단)

```bash
touch data/TRADING_HALT      # 주문만 스킵 (스케줄러·수집·로그는 유지)
rm data/TRADING_HALT         # 재개
# 또는 .env에 TRADING_HALT=1 후 restart
```

## 5. 안전장치 요약 (config.py)

| 상수 | 기본 | 의미 |
|------|:---:|------|
| `MAX_DAILY_BUYS` | 5 | 하루 신규 매수 건수 상한(런당) |
| `MAX_TOTAL_EXPOSURE_PCT` | 60 | 총 노출 상한 %(교차-런 절대 방어) |
| `MAX_SYMBOL_WEIGHT_PCT` | 20 | 종목당 비중 상한 % |
| `WATCHDOG_STALE_MINUTES` | 90 | heartbeat 이보다 오래되면 hang 추정 |
| `SLACK_WEBHOOK_URL` | "" | 알림(미설정 시 no-op) |

## 6. 장애 대응

| 증상 | 확인 | 조치 |
|------|------|------|
| 주문 0건 지속 | 로그 funnel(중립/컨센) | WSB 중립 쏠림 정상 가능 — daily-decision-report 확인 |
| 서비스 죽음 | `systemctl status` | Restart=always 자동 복구. 반복 시 로그 |
| hang(살아있는데 멈춤) | `data/heartbeat.json` 시각 | 워치독이 stale 감지→재시작+알림 |
| selfcheck 실패 알림 | 자격/모델/paper | .env·모델 scp 재확인 |
| 실계좌 경보 | `KIS_PAPER_TRADING=false` | selfcheck가 주문 차단 — paper로 되돌림 |
