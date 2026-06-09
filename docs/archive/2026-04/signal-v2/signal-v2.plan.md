# Plan: Signal V2 — 감성 고도화 + 백테스팅 + Volume Spike

**Feature**: signal-v2
**Date**: 2026-04-05
**Status**: Plan

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 중립 기사가 감성 평균을 희석시키고, 거래량 급증 신호를 무시하며, 신호 판단 근거를 추적할 수 없다. NewsAPI 1개월 한도로 백테스팅이 불충분하고, TextBlob vs FinBERT 성능 비교도 불가 |
| **Solution** | Finnhub으로 뉴스 소스 전환(기사 100개, 과거 2개월 가능), FinBERT neutral 필터 + 긍정 비율 공식, Volume Spike 예외 매수, 모델별(TextBlob/FinBERT/Combined) 별도 백테스팅, SentimentProvider 추상화 |
| **Function UX Effect** | `--backtest --model [textblob\|finbert\|combined]`로 2026-02-01~2026-04-01 모델별 수익률 비교 출력, `articles_detail.json`에 기사별 판단 근거 저장 |
| **Core Value** | 실증 데이터로 어느 감성 모델이 더 나은지 검증하고, 기관 매집 신호 포착, WSB/GPT 확장 시 코드 변경 최소화 |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | "FinBERT가 TextBlob보다 실제로 나은가?"를 데이터로 증명. 중립 기사 노이즈 제거 + 거래량 결합으로 신호 품질 향상 |
| **WHO** | news-rsi-trading 시스템 운영자 |
| **RISK** | Finnhub 60 req/min 제한 (백테스팅 시 rate limit) / neutral 필터 과적용 시 유효 기사 부족 / FinBERT 100건 처리 시간 (~50초/종목) |
| **SUCCESS** | 모델별 백테스팅 결과 비교 출력, articles_detail.json 생성, Volume Spike 신호 동작 확인 |
| **SCOPE** | `sentiment_provider.py`(신규) + `backtester.py`(신규) + `indicators.py`, `signals.py`, `collector.py`, `config.py`, `main.py` 수정 |

---

## 1. 기능 요구사항

### FR-01~03: 중립 기사 필터링 + 감성 점수 재산출

| ID | 요구사항 |
|----|----------|
| FR-01 | FinBERT `neutral` 확률 ≥ 80%인 기사는 점수 계산에서 제외(Drop) |
| FR-02 | 필터 후 유효 기사 < 10건이면 기존 avg_raw 방식으로 폴백 + 경고 로그 (100개 수집 기준 상향) |
| FR-03 | 새 점수 공식: `positive_count / (positive_count + negative_count) * 100` |

**점수 공식 비교:**

| 방식 | 공식 | 특징 |
|------|------|------|
| 기존 | `(avg(p - n) + 1) * 50` | 중립 기사가 p-n을 0으로 끌어내림 |
| 신규 | `pos / (pos + neg) * 100` | 명확한 호재/악재 기사만 반영 |

> **positive 기준**: FinBERT에서 가장 높은 확률이 `positive`인 기사
> **negative 기준**: FinBERT에서 가장 높은 확률이 `negative`인 기사

### FR-04~06: 뉴스 소스 Finnhub 전환 + 기사 100개

| ID | 요구사항 |
|----|----------|
| FR-04 | NewsAPI → **Finnhub** company-news API로 교체 (`/api/v1/company-news`) |
| FR-05 | 수집 기사 수: 50건 → **100건** (Finnhub pagination 활용) |
| FR-06 | Finnhub 응답 필드 매핑: `headline` → `title`, `summary` → `description` |

**Finnhub API 비교:**

| | NewsAPI 무료 | Finnhub 무료 |
|--|--|--|
| 과거 뉴스 | 1개월 | ~1년 |
| Rate Limit | 분당 제한 | 60 req/min |
| 응답 필드 | title, description | headline, summary |

> Finnhub API key는 `.env`에 `FINNHUB_API_KEY` 추가

### FR-07~08: 기사별 점수 기록

| ID | 요구사항 |
|----|----------|
| FR-07 | `data/articles_detail.json`에 기사별 `{title, finbert_label, scores, included}` 저장 |
| FR-08 | `included: false` = neutral 필터로 제외된 기사. 당일 데이터만 저장(누적 없음) |

```json
{
  "date": "2026-04-05",
  "AAPL": [
    {
      "title": "Apple reports record revenue...",
      "finbert_label": "positive",
      "scores": {"positive": 0.87, "negative": 0.05, "neutral": 0.08},
      "included": true
    },
    {
      "title": "Apple holds annual developer conference",
      "finbert_label": "neutral",
      "scores": {"positive": 0.03, "negative": 0.04, "neutral": 0.93},
      "included": false
    }
  ]
}
```

### FR-09~11: Volume Spike 신호 결합

| ID | 요구사항 |
|----|----------|
| FR-09 | OHLCV에서 20일 평균 거래량(`volume_ma20`) 계산 — 기존 수집 데이터 재활용 |
| FR-10 | `volume_spike = 당일 거래량 ≥ volume_ma20 × 2.0` |
| FR-11 | Volume Spike + RSI < 40 + sentiment 40~60(중립) 동시 충족 시 → BUY 예외 신호 |

```
IF volume_spike AND rsi < 40 AND 40 <= sentiment <= 60:
    signal = "BUY"
    log: [Volume Spike] {symbol}: BUY (vol={당일}/ma20={평균}, ×{배수:.1f})
```

### FR-12~15: 백테스팅 엔진 (모델별 분리)

| ID | 요구사항 |
|----|----------|
| FR-12 | 백테스팅 기간: **2026-02-01 ~ 2026-04-01** (고정, Finnhub 과거 데이터 활용) |
| FR-13 | `--model [textblob\|finbert\|combined]` 플래그로 모델별 개별 백테스팅 실행 |
| FR-14 | 날짜별 신호 재계산: OHLCV(Polygon) + 뉴스(Finnhub, 해당 날짜 기준 7일치) |
| FR-15 | 출력: 총 수익률, 거래 횟수, 승률, MDD, 종목별 상세 + **모델간 비교 요약** |

**백테스팅 모드:**
```
python main.py --backtest --model textblob    # TextBlob 단독
python main.py --backtest --model finbert     # FinBERT 단독 (neutral 필터 포함)
python main.py --backtest --model combined    # (TextBlob + FinBERT) / 2 — 현재 방식
```

**rate limit 대응:** Finnhub 60 req/min → 백테스팅 루프에서 호출 간 1초 delay

**FinBERT 백테스팅 처리 시간 최적화:**
- 날짜별 감성 점수를 `data/backtest_cache.json`에 저장
- 재실행 시 캐시 히트 → API/모델 재호출 없음

> **Threshold 최적화는 이번 구현 제외** — 백테스팅 결과를 보고 config.py 수동 조정

### FR-16~18: SentimentProvider 추상화

| ID | 요구사항 |
|----|----------|
| FR-16 | `sentiment_provider.py`에 `SentimentProvider` ABC 정의 |
| FR-17 | `FinBERTProvider`, `TextBlobProvider` 구현 — 현재 indicators.py 로직 이동 |
| FR-18 | `config.py`에 `SENTIMENT_PROVIDERS = ["finbert", "textblob"]`로 활성 Provider 제어 |

```python
class SentimentProvider(ABC):
    @abstractmethod
    def score(self, articles: list[dict]) -> tuple[float, list[dict]]:
        """Returns: (score [0-100], article_details)"""

class FinBERTProvider(SentimentProvider): ...   # neutral 필터링 포함
class TextBlobProvider(SentimentProvider): ...  # 기존 로직

# 미래 확장:
# class WSBProvider(SentimentProvider): ...
# class GPTProvider(SentimentProvider): ...
```

### 비기능 요구사항

| ID | 요구사항 |
|----|----------|
| NFR-01 | 백테스팅은 실제 거래에 영향 없음 (별도 실행 모드) |
| NFR-02 | SentimentProvider 교체 시 signals.py 코드 변경 없음 |
| NFR-03 | articles_detail.json은 당일 데이터만 (용량 관리) |
| NFR-04 | Volume MA 계산은 기존 OHLCV 데이터 재사용 (추가 API 호출 없음) |
| NFR-05 | Finnhub 전환 후 기존 신호 로직(RSI, 감성 평균 공식) 동작 동일 보장 |

---

## 2. 변경 대상 파일

| 파일 | 유형 | 주요 내용 |
|------|------|-----------|
| `sentiment_provider.py` | 신규 | SentimentProvider ABC + FinBERTProvider + TextBlobProvider |
| `backtester.py` | 신규 | 2개월 백테스팅 엔진 + 모델별 분리 + 캐시 |
| `collector.py` | 수정 | Finnhub company-news API 추가, `get_news(symbol, from_date, limit=100)` |
| `indicators.py` | 수정 | volume_ma20 계산 추가, FinBERTProvider 호출로 교체 |
| `signals.py` | 수정 | Volume Spike 예외 로직 + articles_detail 저장 + Provider 호출 변경 |
| `config.py` | 수정 | FINNHUB_API_KEY, NEWS_MAX_ARTICLES, NEUTRAL_FILTER_*, VOLUME_SPIKE_*, BACKTEST_* 추가 |
| `main.py` | 수정 | `--backtest`, `--model` 플래그 처리 |

---

## 3. 새 config 상수

```python
# --- 뉴스 수집 ---
NEWS_PROVIDER = "finnhub"            # "newsapi" → "finnhub"
NEWS_MAX_ARTICLES = 100              # 50 → 100
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"

# --- Signal V2 ---
NEUTRAL_FILTER_THRESHOLD = 0.80      # FinBERT neutral 이상이면 제외
NEUTRAL_FILTER_MIN_ARTICLES = 10     # 필터 후 최소 기사 수 (100개 기준 상향)
VOLUME_SPIKE_MULTIPLIER = 2.0        # 20일 평균 대비 거래량 급증 배수
VOLUME_MA_PERIOD = 20

# --- 백테스팅 ---
BACKTEST_START = "2026-02-01"        # 고정 시작일
BACKTEST_END   = "2026-04-01"        # 고정 종료일
BACKTEST_CACHE_FILE = "data/backtest_cache.json"
ARTICLES_DETAIL_FILE = "data/articles_detail.json"

SENTIMENT_PROVIDERS = ["finbert", "textblob"]
```

---

## 4. 백테스팅 출력 예시

```
=== 백테스팅 결과 (2026-02-01 ~ 2026-04-01) ===

모델: TextBlob
  총 수익률: +5.1% | 거래: 10회 | 승률: 60.0% | MDD: -2.8%

모델: FinBERT
  총 수익률: +8.3% | 거래: 12회 | 승률: 66.7% | MDD: -3.1%

모델: Combined
  총 수익률: +7.2% | 거래: 11회 | 승률: 63.6% | MDD: -2.5%

종목별 (FinBERT 기준):
  AAPL  | +5.2% | 4거래
  PLTR  | +2.8% | 5거래
  NVDA  | +0.3% | 3거래

* Threshold 조정 후 재실행: python main.py --backtest --model finbert
* config.py에서 SENTIMENT_BUY, RSI_OVERSOLD 값을 변경하세요
```

---

## 5. 리스크

| 리스크 | 영향도 | 대응 |
|--------|--------|------|
| Finnhub 60 req/min 백테스팅 시 제한 | 중 | 호출 간 1초 delay + backtest_cache.json 캐시 |
| neutral 필터 후 유효 기사 < 10건 | 중 | 폴백 + 경고 로그 |
| FinBERT 100건 처리 시간 (~50초/종목) | 중 | 백테스팅 캐시로 재실행 시 단축 |
| Volume Spike 이벤트성 오판 | 낮음 | RSI < 40 조건으로 단순 급등 제거 |
| Finnhub 전환 후 기존 뉴스 수 차이 | 낮음 | headline+summary 매핑 검증 후 배포 |

---

## 6. 성공 기준

| SC | 기준 |
|----|------|
| SC-01 | `--backtest --model textblob/finbert/combined` 각각 수익률/승률/MDD 출력 |
| SC-02 | 모델별 결과 비교 요약 출력 (3개 모델 나란히) |
| SC-03 | `data/articles_detail.json` 생성, `included` 필드 존재 |
| SC-04 | Volume Spike 조건 충족 시 `[Volume Spike]` 로그 출력 |
| SC-05 | neutral 필터 후 유효 기사 < 10건 시 폴백 로그 출력 |
| SC-06 | Finnhub 전환 후 기사 100건 수집 확인 |

---

## 7. 구현 순서 (Module Map)

| Module | 파일 | 작업 |
|--------|------|------|
| M1 | `config.py` | 새 상수 추가 (Finnhub, backtest 날짜, 필터 등) |
| M2 | `collector.py` | Finnhub API 추가, get_news limit=100, from_date 지원 |
| M3 | `sentiment_provider.py` | SentimentProvider ABC + FinBERTProvider + TextBlobProvider |
| M4 | `indicators.py` | volume_ma20 추가, Provider 호출 교체 |
| M5 | `signals.py` | Volume Spike 예외 + articles_detail 저장 |
| M6 | `backtester.py` | 백테스팅 엔진 + 모델별 분리 + 캐시 |
| M7 | `main.py` | --backtest, --model 플래그 |

---

## 8. 미래 확장 포인트

| 확장 | 방법 |
|------|------|
| WSB 감성 | `WSBProvider(SentimentProvider)` + config에 "wsb" 추가 |
| GPT 감성 | `GPTProvider(SentimentProvider)` |
| 앙상블 | `EnsembleProvider([FinBERTProvider, GPTProvider])` |
| Threshold 자동 최적화 | `--backtest --optimize` 플래그 (다음 피처) |
| 백테스팅 기간 변경 | BACKTEST_START / BACKTEST_END 수정만으로 확장 |
