# System Architecture — auto_stock

> 이 문서는 현재 시스템의 전체 구조를 한눈에 파악하기 위한 Living Document입니다.
> 기능 추가/변경 시 반드시 업데이트하세요.
>
> **마지막 업데이트**: 2026-04-22
> **브랜치**: rsi_finBERT_combine

---

## 1. 시스템 개요

뉴스 감성 분석(TextBlob/FinBERT/GPT-4)과 Reddit 군중심리를 결합한 미국주 페이퍼 트레이딩 시스템.
RSI + 감성 점수 → 매매 신호 → 포지션 관리 → 백테스팅/포워드 테스팅.

```
[뉴스 파이프라인]                     [Reddit 파이프라인]
collector.get_news()                  reddit_collector.py
    ↓                                     ↓
sentiment_provider.py                 wsb_signal_engine.py  [V3]
(TextBlob / FinBERT / GPT-4)          (Velocity 보정 → 중립필터 → TopN)
    ↓                                     ↓
signals.generate_signals_for_all()    reddit_portfolio.py
(RSI + Sentiment → Signal)            (포지션 추적 / Stop-Loss / Trailing)
    ↓
market_filter.apply_market_filter()
(QQQ RSI → 시장 과열 시 다운그레이드)
    ↓
portfolio.py / trader.py
(포지션 관리 / 주문 실행)
```

---

## 2. 파일별 역할

### 핵심 모듈

| 파일 | 역할 | 주요 진입점 |
|------|------|------------|
| `main.py` | CLI 진입점, 모든 실행 모드 라우팅 | `main()` |
| `config.py` | 전체 상수 정의 (52개+) | — |
| `signals.py` | 신호 결정 5단계 파이프라인 | `generate_signals_for_all()` |
| `sentiment_provider.py` | TextBlob/FinBERT/GPT-4 Provider ABC | `get_provider(name)` |
| `wsb_preprocessor.py` | WSB 슬랭/이모지/반어법 → FinBERT 친화적 변환 | `WSBPreprocessor.preprocess()` |
| `collector.py` | OHLCV(Polygon) + 뉴스(Finnhub) 수집 | `get_ohlcv()`, `get_news()` |
| `reddit_collector.py` | Reddit PRAW 3서브레딧 수집 + Daily Thread | `collect_wsb_posts()` |
| `market_filter.py` | QQQ RSI 기반 시장 상태 필터 | `apply_market_filter()` |
| `indicators.py` | RSI, MA, ATR, VolumeMA20 계산 | `get_latest_rsi()`, `get_ma()`, `calculate_atr()` |
| `position_sizer.py` | Equal/Sentiment/Volatility 사이징 ABC | `get_sizer(name)` |
| `wsb_state.py` | mention_history / position_scores JSON I/O | `load_mention_history()`, `load_position_scores()` |

### 백테스팅/포워드 테스팅

| 파일 | 역할 |
|------|------|
| `backtester.py` | 뉴스 모델 백테스팅 (TextBlob/FinBERT/GPT-4) |
| `reddit_backtester.py` | Reddit 12전략 Replay 백테스팅 (V3 check_exit 연동) |
| `wsb_signal_engine.py` | Reddit 신호 생성 V3 (Velocity 보정 + 중립필터 + 5단계 청산) |
| `reddit_portfolio.py` | Reddit 포지션 관리 (Gap Down -5% / Stop-Loss -7% / Trailing Stop) |

### 실거래/스케줄러

| 파일 | 역할 |
|------|------|
| `portfolio.py` | 뉴스 모델 포지션 관리 |
| `trader.py` | 주문 실행 (페이퍼 트레이딩) |
| `scheduler.py` | 크론탭 연동 스케줄러 |

---

## 3. 신호 결정 파이프라인 (뉴스 모델)

```python
# signals.generate_signals_for_all() 내부 흐름
1. OHLCV 수집          collector.get_ohlcv(symbol)
2. RSI 계산             indicators.get_latest_rsi()
3. 뉴스 수집            collector.get_news(symbol)
4. 감성 분석            provider.score(articles) — 활성 Provider 평균
5. 신호 결정            determine_signal(rsi, sentiment)
6. Volume Spike 예외    _check_volume_spike() → BUY 오버라이드
7. Market Filter        market_filter.apply_market_filter(signal, mkt_rsi)
```

**신호 결정 규칙** (`determine_signal`):
| 신호 | 조건 |
|------|------|
| STRONG_BUY | sentiment > 70 AND rsi < 30 |
| STRONG_SELL | sentiment < 30 AND rsi > 70 |
| BUY | sentiment > 50 AND 30 ≤ rsi < 50 |
| SELL | sentiment < 50 AND rsi > 70 |
| NEUTRAL | 40 ≤ sentiment ≤ 60 AND 40 ≤ rsi ≤ 60 |

**Market Filter** (`market_filter.py`):
- QQQ RSI > 70 → BUY/STRONG_BUY → NEUTRAL (과열)
- QQQ RSI < 30 → SELL/STRONG_SELL → NEUTRAL (패닉 과매도)

---

## 4. 감성 분석 모델

| 모델 | Provider 클래스 | 특징 |
|------|----------------|------|
| `textblob` | `TextBlobProvider` | 빠름, 금융 도메인 정확도 낮음 |
| `finbert` | `FinBERTProvider` | Bloomberg/Reuters 학습, ONNX 캐시 (~3초) |
| `finbert-wsb` | `FinBERTProvider(use_wsb_preprocessor=True)` | WSB 슬랭/이모지 전처리 후 FinBERT |
| `gpt4` | `GPTProvider` | gpt-4o, 배치10건, sha256 캐시, 비용 발생 |
| `combined` | 평균 | config.SENTIMENT_PROVIDERS에 정의된 모델 평균 |

**FinBERT Neutral 필터**: `positive_ratio >= 0.80` → neutral로 간주, 폴백 시 avg(p-n) 사용

---

## 5. Reddit 파이프라인

```
reddit_collector.collect_wsb_posts()
    → wallstreetbets / stocks / investing (PRAW)
    → Daily Thread 댓글 (_fetch_daily_thread)
    → Polygon 티커 검증 (캐시 활용)
    ↓
wsb_signal_engine.run_pipeline()  [V3 — wsb-signal-v3]
    → _score_posts()          bullish/bearish/neutral 카운트
    → _apply_neutral_filter() neutral/total > 0.70 → NEUTRAL 강제 (노이즈 제거)
    → _apply_velocity()       7일 멘션 이력 → velocity_state
                              HIGH_MOMENTUM(×2↑) / NORMAL / DECLINING(×0.5↓)
                              NEW_SPIKE(첫등장 ≥20언급) / NEW_IGNORE(<20언급)
    → _determine_signal_v3()  Velocity 보정 매트릭스 → STRONG_BUY/BUY/NEUTRAL
                              NORMAL: STRONG_BUY>70, BUY>55
                              HIGH_MOMENTUM: 임계값 -5 완화
                              DECLINING: 임계값 +5 강화
    → _filter_consensus()     bullish/bearish ≥ 1.5배
    → 랭킹 → TopN
    → wsb_state.save_mention_history()  (7일 FIFO)
    ↓
check_exit() V3 — 5단계 우선순위:
    1. sentiment_reversal   2일 연속 점수 < entry_score × 0.60
    2. rsi_overbought       RSI > 70 (HIGH_MOMENTUM: rsi_held 1회 유예)
    3. gap_down             open/prev_close ≤ -5% (WSB_GAP_DOWN_PCT)
    4. stop_loss            pnl ≤ -7% (STOP_LOSS_PCT)
    5. trailing_stop        pnl > 0% AND drawdown ≤ -5%
    ↓
reddit_portfolio.py
    → Equal / Sentiment / Volatility 사이징
    → Gap Down 즉시 청산 (전일 종가 대비 -5%)
    → Stop-Loss -7%, Trailing Stop -5%
    → 매수 시 entry_score → wsb_state.upsert_position_score()
    → 수수료 0.25% 양방 공제
```

---

## 6. CLI 주요 명령어

```bash
# 뉴스 모델 백테스팅
python main.py --backtest --model [textblob|finbert|finbert-wsb|gpt4|combined]

# Reddit 12전략 백테스팅
python main.py --backtest --source reddit \
  --model [finbert|finbert-wsb|gpt4] \
  --ranking [mentions|sentiment] \
  --sizing [equal|sentiment|volatility] \
  --from YYYY-MM-DD --to YYYY-MM-DD

# 실시간 신호 생성
python main.py --run-now

# Reddit Forward Testing (스케줄러)
python main.py --reddit-run-now
```

---

## 7. 주요 상수 (config.py)

| 상수 | 기본값 | 설명 |
|------|--------|------|
| `RSI_OVERSOLD` | 30 | RSI 과매도 기준 |
| `RSI_OVERBOUGHT` | 70 | RSI 과매수 기준 |
| `SENTIMENT_BUY` | 50 | BUY 신호 감성 하한 |
| `SENTIMENT_STRONG_BUY` | 70 | STRONG_BUY 감성 하한 |
| `MA_ENTRY_PERIOD` | 30 | 진입 MA 기간 |
| `COMMISSION_RATE` | 0.0025 | 수수료율 (0.25%) |
| `COMMISSION_MIN_USD` | 2.0 | 최소 수수료 |
| `VOLUME_SPIKE_MULTIPLIER` | 2.0 | 거래량 급증 배수 |
| `NEWS_MAX_ARTICLES` | 100 | Finnhub 기사 수집 상한 |
| `NEUTRAL_FILTER_MIN_ARTICLES` | 10 | FinBERT 유효 기사 최소 수 |

**WSB V3 상수** (wsb-signal-v3):

| 상수 | 기본값 | 설명 |
|------|--------|------|
| `WSB_STRONG_BUY_SCORE` | 70.0 | NORMAL STRONG_BUY 기준 |
| `WSB_BUY_SCORE` | 55.0 | NORMAL BUY 기준 (구 50→55 강화) |
| `WSB_NEUTRAL_RATIO_MAX` | 0.70 | 중립 비율 상한 (초과 시 NEUTRAL 강제) |
| `WSB_VELOCITY_LOOKBACK_DAYS` | 7 | Velocity 계산 이력 일수 |
| `WSB_VELOCITY_HIGH_THRESHOLD` | 2.0 | HIGH_MOMENTUM 판정 배수 |
| `WSB_VELOCITY_LOW_THRESHOLD` | 0.5 | DECLINING 판정 배수 |
| `WSB_VELOCITY_SCORE_ADJUST` | 5.0 | 임계값 보정 폭 (±5) |
| `WSB_NEW_SPIKE_MIN_MENTIONS` | 20 | NEW_SPIKE 최소 언급 수 |
| `WSB_SENTIMENT_REVERSAL_RATIO` | 0.60 | 감성 역전 기준 (entry_score × 0.60) |
| `WSB_RSI_EXIT_OVERBOUGHT` | 70.0 | RSI 과매수 청산 기준 |
| `WSB_GAP_DOWN_PCT` | -5.0 | Gap Down 청산 기준 (%) |

---

## 8. 기능 이력 (완료된 PDCA)

| 기능 | 완료일 | 핵심 변경 | 아카이브 |
|------|--------|-----------|---------|
| `news-rsi-trading` | 2026-03 | 기반 시스템 구축 | `docs/archive/2026-04/news-rsi-trading/` |
| `polygon-massive-migration` | 2026-04-01 | Polygon SDK 마이그레이션 | `docs/archive/2026-04/polygon-massive-migration/` |
| `market-filter-finbert` | 2026-04-02 | QQQ Market Filter + FinBERT | `docs/archive/2026-04/market-filter-finbert/` |
| `signal-v2` | 2026-04-05 | FinBERT neutral 필터 + Volume Spike + 백테스팅 | `docs/archive/2026-04/signal-v2/` |
| `reddit-gpt4-quant` | 2026-04-17 | Reddit 파이프라인 + GPT-4 + 12전략 | `docs/archive/2026-04/reddit-gpt4-quant/` |
| `daily-thread-collector` | 2026-04-18 | Daily Thread 댓글 수집 | `docs/archive/2026-04/daily-thread-collector/` |
| `wsb-finbert-preprocessor` | 2026-04-18 | WSB 전처리 + finbert-wsb 옵션 | `docs/archive/2026-04/wsb-finbert-preprocessor/` |
| `wsb-signal-v3` | 2026-04-22 | 30MA 제거 + Velocity 보정 매트릭스 + 5단계 청산 | `docs/04-report/features/wsb-signal-v3.report.md` |

---

## 9. 변경 시 가이드

### 새 기능 추가 시
1. `/pdca plan {feature}` → `/pdca design` → `/pdca do` → `/pdca analyze` → `/pdca report`
2. 완료 후 이 문서 **§2, §3, §4, §5** 해당 섹션 업데이트
3. **§8 기능 이력**에 한 줄 추가
4. `/pdca archive {feature}`로 PDCA 문서 정리

### 기존 기능 변경 시
- **설정값(config.py)만 바꾸는 경우**: ARCHITECTURE.md §7 업데이트만
- **로직 변경 (signals.py, market_filter.py 등)**: §3~5 해당 섹션 업데이트
- **새 모델/Provider 추가**: §4 테이블에 행 추가
- **대규모 리팩토링**: PDCA 사이클 돌리고 §8에 기록
