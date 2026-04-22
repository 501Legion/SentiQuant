# Plan: Market RSI Filter + FinBERT 감성 분석 추가

**Feature**: market-filter-finbert
**Date**: 2026-04-01
**Status**: Plan

---

## Executive Summary

| 항목 | 내용 |
|------|------|
| **Feature** | Market RSI Filter + FinBERT 감성 분석 추가 |
| **작성일** | 2026-04-01 |
| **단계** | Plan |

### 1.1 Value Delivered (4-perspective)

| 관점 | 내용 |
|------|------|
| **Problem** | 개별 종목 RSI/감성만으로 매수 진입하면 시장 전체가 과열·하락 추세일 때도 공격적 매수 신호가 발생하며, TextBlob은 금융 도메인 특화 NLP가 아니라 감성 정확도가 낮다 |
| **Solution** | QQQ 14일 Market RSI로 시장 상태를 판단해 과열/하락 시 매수 신호를 한 단계 낮추고, FinBERT(금융 특화 BERT)를 TextBlob과 병렬 실행하여 평균 감성으로 신호 품질을 높인다 |
| **Function UX Effect** | 시장 과열 시 로그에 "Market Filter 적용" 표시, signals.json에 두 감성 모델 점수 모두 기록되어 비교 분석 가능 |
| **Core Value** | 최소 코드 변경(기존 로직 유지)으로 시장 맥락 인식과 NLP 정확도를 동시에 향상 |


---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 시장 전체 방향성을 무시한 개별 종목 신호는 타이밍 오류를 유발. FinBERT로 감성 정확도 개선 및 모델 비교 데이터 확보 |
| **WHO** | news-rsi-trading 시스템 운영자 |
| **RISK** | FinBERT 초기 로딩 시간(~30초 CPU), QQQ API 호출 추가로 Rate Limit 위험 증가 |
| **SUCCESS** | Market RSI 필터 적용 로그 확인, signals.json에 두 감성 점수 동시 저장 |
| **SCOPE** | `config.py`, `indicators.py`, `signals.py`, `requirements.txt` 수정 (collector.py, trader.py, portfolio.py, scheduler.py 변경 없음) |

---

## 1. 개요

### 1.1 Market RSI Filter

QQQ ETF(나스닥 100 추종)의 14일 RSI를 계산하여 시장 상태를 판단한다.

| Market 상태 | 조건 | Filter 행동 |
|------------|------|-------------|
| 과열 (Overbought) | Market RSI > 70 | STRONG_BUY → BUY, BUY → NEUTRAL |
| 하락 추세 (Downtrend) | Market RSI < 30 | STRONG_BUY → BUY, BUY → NEUTRAL |
| 정상 | 30 ≤ Market RSI ≤ 70 | 신호 변경 없음 |

**설계 원칙**: 시장이 극단 상태일 때 공격적 매수를 억제할 뿐, SELL/STRONG_SELL에는 적용하지 않는다.

### 1.2 FinBERT 감성 분석

HuggingFace `ProsusAI/finbert` 모델을 기존 TextBlob과 병렬로 실행한다.

```
최종 sentiment = (textblob_score + finbert_score) / 2
```

- `textblob_score`: 기존 Scaled Sentiment [0, 100]
- `finbert_score`: FinBERT positive/negative/neutral → Scaled Sentiment [0, 100]
- 두 점수 모두 `signals.json`에 저장

---

## 2. 요구사항

### 2.1 기능 요구사항

| ID | 요구사항 |
|----|----------|
| FR-01 | `config.py`에 `MARKET_SYMBOL="QQQ"`, `MARKET_RSI_OVERBOUGHT=70.0`, `MARKET_RSI_DOWNTREND=30.0` 추가 |
| FR-02 | `indicators.py`에 `get_market_rsi()` 함수 추가 — QQQ OHLCV 조회 후 14일 RSI 반환 |
| FR-03 | `signals.py`에 `apply_market_filter(signal, market_rsi)` 함수 추가 — RSI>70 또는 <30 시 다운그레이드 |
| FR-04 | `indicators.py`에 `calculate_finbert_sentiment_score(articles)` 함수 추가 |
| FR-05 | 최종 sentiment = (TextBlob + FinBERT) / 2, signals.json에 `sentiment_textblob`, `sentiment_finbert` 저장 |
| FR-06 | `generate_signals_for_all()`에서 Market RSI 1회 조회 후 모든 종목에 재사용 |
| FR-07 | Market Filter 적용 시 로그: `[Market Filter] {symbol}: {원래신호} → {변경신호} (Market RSI={값})` |

### 2.2 비기능 요구사항

| ID | 요구사항 |
|----|----------|
| NFR-01 | 기존 `calculate_sentiment_score()` (TextBlob) 코드 변경 없음 |
| NFR-02 | FinBERT CPU 실행 지원 — `device="cpu"` 명시, GPU 없어도 동작 |
| NFR-03 | FinBERT 모델은 모듈 로드 시 1회만 초기화 (lazy singleton) |
| NFR-04 | FinBERT 실패 시 TextBlob 단독 사용으로 폴백 (파이프라인 중단 방지) |
| NFR-05 | collector.py, trader.py, portfolio.py, scheduler.py 변경 없음 |

---

## 3. 변경 대상 파일

| 파일 | 변경 유형 | 내용 |
|------|-----------|------|
| `config.py` | 수정 | MARKET_SYMBOL, MARKET_RSI_OVERBOUGHT, MARKET_RSI_DOWNTREND 추가 |
| `indicators.py` | 수정 | `get_market_rsi()`, `calculate_finbert_sentiment_score()` 추가 |
| `signals.py` | 수정 | `apply_market_filter()` 추가, `generate_signals_for_all()` 수정 |
| `requirements.txt` | 수정 | `transformers`, `torch` 추가 |

---

## 4. signals.json 스키마 변경

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

## 5. FinBERT 스코어링 방식

```
FinBERT 출력: {"positive": p, "negative": n, "neutral": neu}

finbert_raw = p - n  # [-1, 1] — positive면 +, negative면 -
finbert_score = (finbert_raw + 1) * 50  # [0, 100]

최종 sentiment = (textblob_score + finbert_score) / 2
```

---

## 6. 리스크

| 리스크 | 영향도 | 대응 방안 |
|--------|--------|-----------|
| FinBERT 초기 로딩 지연 (~30초) | 낮음 | lazy singleton으로 1회만 로드, 로그 안내 |
| QQQ API 호출로 Rate Limit 증가 | 중 | Market RSI는 `generate_signals_for_all()` 진입 시 1회만 조회 |
| FinBERT CPU 메모리 부족 | 낮음 | 기사 수 50개 이하로 배치 처리, OOM 시 TextBlob 폴백 |
| HuggingFace 모델 다운로드 필요 | 낮음 | 최초 실행 시 자동 다운로드, 이후 캐시 사용 |

---

## 7. 성공 기준

| SC | 기준 |
|----|------|
| SC-01 | `python main.py --run-now` 실행 시 Market RSI(QQQ) 계산 로그 출력 |
| SC-02 | Market RSI > 70 조건 시뮬레이션 시 BUY → NEUTRAL 다운그레이드 확인 |
| SC-03 | FinBERT 감성 점수 계산 로그 출력 (초기화 메시지 포함) |
| SC-04 | `signals.json`에 `sentiment_textblob`, `sentiment_finbert` 두 필드 모두 존재 |
| SC-05 | Market Filter 미적용 시 `signal_original == signal`, 적용 시 다른 값 |

---

## 8. 구현 순서

1. `requirements.txt` 업데이트 (`transformers`, `torch`)
2. `config.py` — Market RSI 임계값 상수 추가
3. `indicators.py` — `get_market_rsi()` + `calculate_finbert_sentiment_score()` 추가
4. `signals.py` — `apply_market_filter()` 추가, `generate_signals_for_all()` 수정
5. `pip install transformers torch` 후 `python main.py --run-now` 테스트
