# 📈 SentiQuant — 감성·군중심리 기반 미국주 페이퍼 트레이딩 시스템

뉴스 감성 분석과 Reddit 군중심리를 RSI 등 기술적 지표와 결합해 매매 신호를 생성하고,
한국투자증권(KIS) OpenAPI **모의투자**로 자동 주문까지 처리하는 시스템입니다.
백테스팅 → 포워드 테스팅 → 페이퍼 트레이딩의 전 주기를 한 코드베이스에서 운영합니다.

> 시스템 전체 구조는 [`ARCHITECTURE.md`](ARCHITECTURE.md)를 참고하세요 (Living Document).

---

## 🧭 시스템 개요

```
[뉴스 파이프라인]                       [Reddit 파이프라인]
collector.get_news() (Finnhub)          reddit_collector.py (6 서브레딧 + Daily Thread)
    ↓                                       ↓
sentiment_provider.py                   wsb_signal_engine.py  [V3]
(TextBlob / FinBERT / GPT-5.4 Mini)     (Velocity 보정 → 중립필터 → TopN)
    ↓                                       ↓
signals.generate_signals_for_all()      Community Opinion Agent 게이팅
(RSI + Sentiment → Signal)              (universe → cost → memory → router)
    ↓                                       ↓
market_filter (QQQ RSI 과열 필터)        reddit_portfolio.py (Stop-Loss / Trailing)
    ↓
portfolio.py / trader.py → kis_broker.py (KIS OpenAPI 모의투자)
```

- **뉴스 모델**: Finnhub 뉴스 → 감성 점수(FinBERT 기본) → RSI와 결합 → 신호 → KIS 모의투자 주문
- **Reddit 모델**: 6개 서브레딧 군중심리 → WSB Signal Engine V3 → Community Opinion Agent 게이팅 → 포지션 관리
- **공통**: QQQ RSI 시장 필터, 비용 인지 게이팅, 백테스팅/포워드 테스팅, Streamlit 대시보드

---

## 🛠️ Tech Stack

- **언어/데이터**: Python 3.10+, Pandas, NumPy
- **감성 분석**: FinBERT (Transformers + ONNX Runtime), TextBlob, OpenAI GPT-5.4 Mini
- **데이터 소스**: Finnhub(뉴스), Polygon(OHLCV/티커), Reddit PRAW(6 서브레딧)
- **증권 연동**: 한국투자증권 KIS OpenAPI (모의투자 — `requests` 직접 호출)
- **스케줄링/운영**: APScheduler, pandas_market_calendars, systemd + watchdog
- **대시보드**: Streamlit
- **테스트**: pytest

---

## ⚙️ 설치

```bash
git clone https://github.com/501Legion/auto_stock.git
cd auto_stock

# FinBERT ONNX는 torch CPU 휠을 먼저 깔아야 함 (GPU 없는 서버 기준)
bash scripts/install_server.sh        # torch==2.3.1+cpu → 나머지 requirements

# 또는 수동 설치
pip install torch==2.3.1+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

`.env.example`를 복사해 API 키를 채워주세요.

```bash
cp .env.example .env
# FINNHUB / POLYGON / REDDIT / OPENAI / KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO ...
```

> ⚠️ KIS는 `KIS_PAPER_TRADING=true`(모의투자)가 강제됩니다. 실전 도메인 접속은 코드에서 차단됩니다.

---

## ▶️ 주요 명령어

```bash
# 뉴스 모델 백테스팅
python main.py --backtest --model [textblob|finbert|finbert-wsb|gpt5|combined]

# Reddit 백테스팅 (전략 조합 비교)
python main.py --backtest --source reddit \
  --model [finbert|finbert-wsb|gpt5] \
  --ranking [mentions|ratio|sentiment] \
  --sizing [equal|sentiment|volatility|opinion_trend] \
  --universe [sp500_only|nasdaq100_only|sp500_nasdaq100|liquid_us|community_liquid|custom_watchlist] \
  [--llm-router] --from YYYY-MM-DD --to YYYY-MM-DD

# 실시간 신호 생성 / KIS 잔고 동기화
python main.py --run-now
python main.py --run-now --source kis

# KIS 모의투자 주문 처리
python main.py --order-now              # 신호 기반 실주문
python main.py --order-now --dry-run    # 주문 직전까지 시뮬레이션

# Reddit 포워드 테스팅
python main.py --reddit-run-now

# 대시보드
streamlit run dashboard_app.py
```

---

## 📊 신호 로직 요약

**뉴스 모델** (`determine_signal`):

| 신호 | 조건 |
|------|------|
| STRONG_BUY | sentiment > 70 AND rsi < 30 |
| BUY | sentiment > 50 AND 30 ≤ rsi < 50 |
| SELL | sentiment < 50 AND rsi > 70 |
| STRONG_SELL | sentiment < 30 AND rsi > 70 |

- QQQ RSI > 70(과열) / < 30(패닉) 시 신호 다운그레이드 (`market_filter`)

**Reddit 모델 (WSB Signal Engine V3)**:
- 표본 수축(K=8)으로 극소표본 노이즈 차단 → 방향성 멘션 최소 3건 → Velocity 보정(HIGH_MOMENTUM/NORMAL/DECLINING) → 합의 비율(bull/bear ≥ 1.5) → TopN
- 청산 5단계: 감성 역전 → RSI 과매수 → Gap Down(-5%) → Stop-Loss(-7%) → Trailing Stop(-5%)
- `opinion_trend` 사이징 시 Community Opinion Agent 게이팅(universe·cost·memory·router) 적용

---

## 📁 주요 파일

| 파일 | 역할 |
|------|------|
| `main.py` | CLI 진입점, 실행 모드 라우팅 |
| `signals.py` / `signal_provider.py` | 뉴스 신호 디스패처 + 결정 파이프라인 |
| `sentiment_provider.py` | TextBlob/FinBERT/GPT-5.4 Mini Provider |
| `wsb_signal_engine.py` | Reddit 신호 생성 V3 + DailyOpinionSnapshot |
| `reddit_collector.py` / `reddit_portfolio.py` | Reddit 수집 / 포지션 관리 |
| `decision_router.py` / `universe_filter.py` / `cost_aware_trade_filter.py` | Community Opinion Agent 게이팅 |
| `kis_broker.py` / `trader.py` / `portfolio.py` | KIS 모의투자 주문·계좌·포지션 |
| `backtester.py` / `reddit_backtester.py` | 백테스팅 엔진 |
| `scheduler.py` | APScheduler (수집 08:45 ET / 주문 09:35 ET) |
| `dashboard_app.py` | Streamlit 대시보드 |
| `config.py` | 전체 상수 정의 (뉴스·WSB V3·KIS·`COMMUNITY_*` 100+) |

---

## 🚀 로드맵 / 현황

- ✅ 뉴스 RSI+감성 신호 → 백테스팅 → KIS 모의투자 주문 연동
- ✅ Reddit 군중심리 파이프라인(V3) + Community Opinion Agent 게이팅
- ✅ Streamlit 대시보드 + systemd/watchdog 운영
- 🔄 페이퍼 트레이딩 라이브 운영 및 깔때기 튜닝 (funnel-fix / timing-fix)
- ⏭️ LLM 라우터 검증 후 활성화, 전략 파라미터 지속 튜닝

> 상세 기능 이력은 [`ARCHITECTURE.md`](ARCHITECTURE.md) §8을 참고하세요.
