# Design: Market RSI Filter + FinBERT 감성 분석

**Feature**: market-filter-finbert
**Date**: 2026-04-01
**Architecture**: Option C — 실용적 균형
**Status**: Design

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 시장 전체 방향성을 무시한 개별 종목 신호는 타이밍 오류를 유발. FinBERT로 감성 정확도 개선 및 모델 비교 데이터 확보 |
| **WHO** | news-rsi-trading 시스템 운영자 |
| **RISK** | FinBERT 초기 로딩 시간(~30초 CPU), QQQ API 호출 추가로 Rate Limit 위험 증가 |
| **SUCCESS** | Market RSI 필터 적용 로그 확인, signals.json에 두 감성 점수 동시 저장 |
| **SCOPE** | `market_filter.py`(신규) + `indicators.py`, `signals.py`, `config.py`, `requirements.txt` 수정 |

---

## 1. 아키텍처 개요

### 1.1 선택 근거 (Option C)

Market Filter는 신호 결정 파이프라인과 독립적인 시장 상태 판단 로직이므로 별도 모듈로 분리한다. FinBERT는 TextBlob과 동일한 감성 분석 역할이므로 `indicators.py` 확장이 자연스럽다.

### 1.2 모듈 구조

```
신호 생성 파이프라인 (변경 후):

collector.get_ohlcv(QQQ)          ─┐
  → indicators.get_latest_rsi()    │
  → market_filter.get_market_rsi() ─┘  [1회 조회, 전 종목 재사용]

per symbol:
  collector.get_ohlcv(symbol)
    → indicators.get_latest_rsi()        → rsi, rsi_ma
  collector.get_news(symbol)
    → indicators.calculate_sentiment_score()      → textblob_score
    → indicators.calculate_finbert_sentiment_score() → finbert_score
    → sentiment = (textblob + finbert) / 2
  signals.determine_signal(rsi, sentiment) → signal_original
  market_filter.apply_market_filter()      → signal (final)
```

---

## 2. 신규 파일: `market_filter.py`

### 2.1 책임
- QQQ ETF 14일 RSI 계산 (세션 내 1회 캐시)
- Market 상태 판단 및 매수 신호 다운그레이드

### 2.2 함수 설계

```python
# market_filter.py

_market_rsi_cache: float | None = None

def get_market_rsi() -> float | None:
    """
    QQQ ETF의 14일 RSI를 계산해 반환한다 (세션 내 1회 캐시).
    Returns:
        float: Market RSI. 수집/계산 실패 시 None.
    """

def apply_market_filter(signal: str, market_rsi: float | None) -> str:
    """
    Market RSI 상태에 따라 매수 신호를 다운그레이드한다.

    규칙:
    - market_rsi > MARKET_RSI_OVERBOUGHT(70): 과열
    - market_rsi < MARKET_RSI_DOWNTREND(30): 하락 추세
    → STRONG_BUY → BUY, BUY → NEUTRAL
    → SELL, STRONG_SELL, NEUTRAL은 변경 없음

    Returns:
        str: 필터 적용 후 신호
    """
```

### 2.3 다운그레이드 매트릭스

| 원래 신호 | Market RSI 정상 (30~70) | Market RSI 극단 (<30 or >70) |
|----------|------------------------|------------------------------|
| STRONG_BUY | STRONG_BUY | BUY |
| BUY | BUY | NEUTRAL |
| NEUTRAL | NEUTRAL | NEUTRAL |
| SELL | SELL | SELL |
| STRONG_SELL | STRONG_SELL | STRONG_SELL |

---

## 3. 수정 파일: `indicators.py`

### 3.1 추가 함수: `calculate_finbert_sentiment_score()`

```python
# Lazy singleton — 모듈 레벨
_finbert_pipeline = None

def _get_finbert_pipeline():
    """FinBERT pipeline lazy singleton (CPU, 최초 1회 초기화)"""
    global _finbert_pipeline
    if _finbert_pipeline is None:
        from transformers import pipeline
        _finbert_pipeline = pipeline(
            "text-classification",
            model="ProsusAI/finbert",
            device=-1,          # CPU (-1), GPU는 device=0
            return_all_scores=True,
        )
    return _finbert_pipeline

def calculate_finbert_sentiment_score(articles: list[dict]) -> float:
    """
    FinBERT로 뉴스 감성 점수를 계산한다.

    알고리즘:
    1. title + description 결합, 512자 truncation
    2. FinBERT: positive/negative/neutral 확률
    3. raw = positive - negative → [-1, 1]
    4. scaled = (raw + 1) * 50 → [0, 100]
    5. 평균 scaled → 최종 점수

    실패 시 TextBlob 폴백이 아닌 50.0 반환 (signals.py에서 평균 처리)

    Returns:
        float [0, 100]. 기사 없거나 실패 시 50.0.
    """
```

### 3.2 스코어링 공식

```
FinBERT output: [{label: "positive", score: p}, {label: "negative", score: n}, {label: "neutral", score: neu}]

raw = p - n                    # [-1.0, 1.0]
finbert_scaled = (raw + 1) * 50   # [0, 100]

final_sentiment = (textblob_scaled + finbert_scaled) / 2
```

---

## 4. 수정 파일: `signals.py`

### 4.1 `generate_signals_for_all()` 변경사항

```python
import market_filter  # 신규 import

def generate_signals_for_all(symbols: list[str]) -> dict[str, dict]:
    results = {}
    timestamp = datetime.now().isoformat()

    # [추가] Market RSI 1회 조회 (전 종목 공유)
    market_rsi = market_filter.get_market_rsi()

    for symbol in symbols:
        # ... (기존 OHLCV + RSI 계산 동일)

        articles = collector.get_news(symbol)

        # [변경] TextBlob + FinBERT 병렬 계산
        sentiment_textblob = indicators.calculate_sentiment_score(articles)
        sentiment_finbert = indicators.calculate_finbert_sentiment_score(articles)
        sentiment = round((sentiment_textblob + sentiment_finbert) / 2, 2)

        # [기존] 신호 결정
        signal_original = determine_signal(rsi, sentiment)

        # [추가] Market Filter 적용
        signal = market_filter.apply_market_filter(signal_original, market_rsi)

        if signal != signal_original:
            logger.warning(
                f"[Market Filter] {symbol}: {signal_original} → {signal} "
                f"(Market RSI={market_rsi:.1f})"
            )

        results[symbol] = {
            "rsi": round(rsi, 2),
            "rsi_ma": round(rsi_ma, 2) if rsi_ma is not None else None,
            # [변경] sentiment 필드 확장
            "sentiment": sentiment,
            "sentiment_textblob": sentiment_textblob,
            "sentiment_finbert": sentiment_finbert,
            # [추가] Market Filter 메타
            "market_rsi": round(market_rsi, 2) if market_rsi is not None else None,
            "market_filter_applied": signal != signal_original,
            # [변경] signal 필드
            "signal": signal,
            "signal_original": signal_original,
            "timestamp": timestamp,
        }
```

---

## 5. 수정 파일: `config.py`

```python
# --- Market RSI Filter ---
MARKET_SYMBOL = "QQQ"              # 나스닥 100 추종 ETF
MARKET_RSI_OVERBOUGHT = 70.0       # 초과열 임계값
MARKET_RSI_DOWNTREND = 30.0        # 하락 추세 임계값
```

---

## 6. 수정 파일: `requirements.txt`

```
# 추가
transformers>=4.40.0
torch>=2.2.0
```

---

## 7. signals.json 스키마 (변경 후)

```json
{
  "AAPL": {
    "rsi": 47.57,
    "rsi_ma": 41.39,
    "sentiment": 54.25,
    "sentiment_textblob": 54.95,
    "sentiment_finbert": 53.55,
    "market_rsi": 58.3,
    "market_filter_applied": false,
    "signal": "BUY",
    "signal_original": "BUY",
    "timestamp": "2026-04-01T16:30:00"
  }
}
```

---

## 8. FinBERT 모델 정보

| 항목 | 내용 |
|------|------|
| 모델 | `ProsusAI/finbert` |
| 기반 | BERT-base, 금융 텍스트 파인튜닝 |
| 출력 클래스 | positive / negative / neutral |
| 입력 제한 | 512 tokens (자동 truncation) |
| 초기 로딩 | ~30초 (CPU, 최초 1회) |
| 이후 속도 | 기사당 ~0.5초 (CPU 50건 기준 ~25초) |
| 캐시 위치 | HuggingFace 기본 캐시 (`~/.cache/huggingface/`) |

---

## 9. 에러 처리

| 케이스 | 처리 방법 |
|--------|-----------|
| QQQ OHLCV 수집 실패 | `market_rsi=None` → `apply_market_filter()` 신호 변경 없음 |
| FinBERT 초기화 실패 | 경고 로그 + `finbert_score=50.0` 반환 (중립 처리) |
| FinBERT 개별 기사 처리 실패 | 해당 기사 스킵, 나머지로 평균 계산 |
| Rate Limit (QQQ 429) | 기존 재시도 로직 적용 (3회), 최종 실패 시 None |

---

## 10. 세션 캐시 전략

Market RSI는 `_market_rsi_cache`로 세션 내 1회만 조회한다.

```
python main.py --run-now 실행 시:
  1. generate_signals_for_all() 진입
  2. market_filter.get_market_rsi() 호출 (QQQ OHLCV 1회)
  3. _market_rsi_cache에 저장
  4. AAPL / PLTR / NVDA 각각에 캐시값 재사용 (추가 API 호출 없음)
```

---

## 11. 구현 가이드

### 11.1 구현 순서

1. `requirements.txt` 업데이트
2. `config.py` — Market RSI 상수 3개 추가
3. `market_filter.py` — 신규 파일 생성
4. `indicators.py` — FinBERT singleton + `calculate_finbert_sentiment_score()` 추가
5. `signals.py` — market_filter import + `generate_signals_for_all()` 수정
6. `pip install transformers torch` 실행
7. `python main.py --run-now` 테스트

### 11.2 검증 체크리스트

- [ ] SC-01: 로그에 `[Market Filter] QQQ RSI=XX.X` 출력
- [ ] SC-02: config에서 `MARKET_RSI_OVERBOUGHT=30.0`으로 임시 변경 → BUY→NEUTRAL 확인
- [ ] SC-03: 로그에 `FinBERT 감성 점수: ...` 출력
- [ ] SC-04: signals.json에 `sentiment_textblob`, `sentiment_finbert` 존재
- [ ] SC-05: market_filter_applied 값 정확성 확인

### 11.3 Session Guide

| Module | 파일 | 예상 작업량 |
|--------|------|------------|
| M1: 설정 | `config.py`, `requirements.txt` | ~5줄 추가 |
| M2: Market Filter | `market_filter.py` (신규) | ~50줄 |
| M3: FinBERT | `indicators.py` 추가 | ~60줄 |
| M4: 신호 통합 | `signals.py` 수정 | ~20줄 수정 |

전체 구현 1 세션 권장 (총 ~135줄).
