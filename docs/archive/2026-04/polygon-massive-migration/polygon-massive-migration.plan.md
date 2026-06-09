# Plan: Polygon.io → Massive.com API 마이그레이션

**Feature**: polygon-massive-migration
**Date**: 2026-04-01
**Status**: Plan

---

## Executive Summary

| 항목 | 내용 |
|------|------|
| **Feature** | Polygon.io → Massive.com Python SDK 마이그레이션 |
| **작성일** | 2026-04-01 |
| **단계** | Plan |

### 1.1 Value Delivered (4-perspective)

| 관점 | 내용 |
|------|------|
| **Problem** | Polygon.io가 Massive.com으로 리브랜딩(2025-10-30)되어 패키지명이 변경됨. 현재 `collector.py`는 raw `requests`로 REST API를 직접 호출하는 방식이라 신규 SDK로 교체가 필요 |
| **Solution** | `polygon-api-client` → `massive` 패키지로 교체하고, raw HTTP 호출을 `RESTClient.list_aggs()` SDK 메서드로 대체 |
| **Function UX Effect** | 마이그레이션 후 기존 기능 동일 유지. SDK 사용으로 페이지네이션 처리가 내장되어 코드 단순화 |
| **Core Value** | 최소 변경(collector.py + requirements.txt)으로 서비스 중단 없이 마이그레이션 완료 |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | Polygon.io → Massive.com 리브랜딩으로 패키지명 및 임포트 경로 변경 필수 |
| **WHO** | 기존 news-rsi-trading 시스템 운영자 |
| **RISK** | SDK의 응답 객체 구조가 raw JSON과 달라 DataFrame 변환 로직 수정 필요 가능성 |
| **SUCCESS** | `python main.py --run-now` 실행 시 OHLCV 데이터 정상 수집 |
| **SCOPE** | `collector.py` + `requirements.txt`만 수정 (나머지 모듈 변경 없음) |

---

## 1. 배경

### 1.1 리브랜딩 개요

| 항목 | 변경 전 | 변경 후 |
|------|---------|---------|
| 회사/서비스명 | Polygon.io | Massive.com |
| Python 패키지 | `polygon-api-client` | `massive` |
| 임포트 | `from polygon import RESTClient` | `from massive import RESTClient` |
| REST API URL | `api.polygon.io` | `api.massive.com` (기존 URL 병행 지원) |
| 메서드 시그니처 | — | **동일** (breaking change 없음) |

### 1.2 현재 코드 방식 (변경 전)

현재 `collector.py`는 `polygon-api-client`를 `requirements.txt`에만 선언하고, 실제로는 `requests` 라이브러리로 REST API를 직접 호출한다:

```python
# 현재 방식 (raw HTTP)
url = f"{config.POLYGON_BASE_URL}/v2/aggs/ticker/{symbol}/range/1/day/..."
params = {"apiKey": config.POLYGON_API_KEY, ...}
response = requests.get(url, params=params)
data = response.json()
results = data.get("results", [])
```

### 1.3 새로운 방식 (변경 후)

```python
# 새로운 방식 (massive SDK)
from massive import RESTClient
client = RESTClient(api_key=config.POLYGON_API_KEY)
aggs = client.list_aggs(
    ticker=symbol,
    multiplier=1,
    timespan="day",
    from_=start_date,
    to=end_date,
    limit=200,
)
# aggs는 iterator → 각 항목은 Agg 객체 (o, h, l, c, v, t 속성)
```

---

## 2. 요구사항

### 2.1 기능 요구사항

| ID | 요구사항 |
|----|----------|
| FR-01 | `requirements.txt`에서 `polygon-api-client` 제거, `massive` 추가 |
| FR-02 | `collector.py`의 `get_ohlcv()`를 `RESTClient.list_aggs()`로 재작성 |
| FR-03 | `collector.py`의 `get_latest_open_price()`를 동일하게 SDK 사용으로 업데이트 |
| FR-04 | Massive SDK의 `Agg` 객체를 기존과 동일한 DataFrame 구조로 변환 (`date, open, high, low, close, volume`) |
| FR-05 | 재시도 로직: SDK 예외(`APIError`, `AuthError`) 처리로 교체 |
| FR-06 | `config.py`의 `POLYGON_BASE_URL` 상수를 Massive URL로 업데이트 (선택) |

### 2.2 비기능 요구사항

| ID | 요구사항 |
|----|----------|
| NFR-01 | `get_news()` 함수 변경 없음 (NewsAPI는 영향 없음) |
| NFR-02 | 다른 모듈(`indicators.py`, `signals.py`, `trader.py`, `portfolio.py`, `scheduler.py`) 변경 없음 |
| NFR-03 | 기존 DataFrame 출력 컬럼 구조(`date, open, high, low, close, volume`) 유지 |

---

## 3. 변경 대상 파일

| 파일 | 변경 유형 | 내용 |
|------|-----------|------|
| `requirements.txt` | 수정 | `polygon-api-client` → `massive` |
| `collector.py` | 수정 | `get_ohlcv()`, `get_latest_open_price()` 재작성 |
| `config.py` | 선택적 수정 | `POLYGON_BASE_URL` 업데이트 |

---

## 4. Massive SDK 핵심 사용법

```python
from massive import RESTClient

client = RESTClient(api_key="YOUR_API_KEY")

# 일별 OHLCV (list_aggs — 페이지네이션 자동 처리)
aggs = client.list_aggs(
    ticker="AAPL",
    multiplier=1,
    timespan="day",
    from_="2026-01-01",
    to="2026-04-01",
    limit=200,
)

for agg in aggs:
    # agg.timestamp (ms), agg.open, agg.high, agg.low, agg.close, agg.volume
    print(agg.open, agg.close)
```

---

## 5. 리스크

| 리스크 | 영향도 | 대응 방안 |
|--------|--------|-----------|
| SDK 응답 객체 속성명 차이 | 중 | `agg.open/high/low/close/volume/timestamp` 확인 후 매핑 |
| `massive` 패키지 설치 실패 | 낮음 | `pip install massive` 후 import 테스트 |
| 기존 `api.polygon.io` URL 병행 지원 기간 종료 | 낮음 | 공식 URL `api.massive.com`으로 교체 |

---

## 6. 성공 기준

| SC | 기준 |
|----|------|
| SC-01 | `python main.py --run-now` 실행 시 OHLCV 수집 정상 |
| SC-02 | 기존과 동일한 DataFrame 구조 (`date, open, high, low, close, volume`) 반환 |
| SC-03 | 뉴스 수집(`get_news`) 영향 없음 |
| SC-04 | 신호 계산 전체 파이프라인 정상 동작 |

---

## 7. 구현 순서

1. `pip uninstall polygon-api-client` + `pip install massive`
2. `requirements.txt` 업데이트
3. `collector.py` 재작성 (`get_ohlcv`, `get_latest_open_price`)
4. `python main.py --run-now` 로 전체 파이프라인 테스트
