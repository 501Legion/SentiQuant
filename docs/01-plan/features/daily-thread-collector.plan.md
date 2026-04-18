# Plan: Reddit 수집 고도화 - Daily Thread + 다중 서브레딧

**Feature**: daily-thread-collector
**Date**: 2026-04-18
**Status**: Plan

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | Reddit 수집기가 일반 게시글(54개/day)만 수집하여 WSB Daily Discussion Thread(13,000+ 댓글)를 완전히 놓침. Polygon 검증도 `massive` import 오류로 비작동. 수집 데이터 99% 누락 상태 |
| **Solution** | Daily Discussion Thread 댓글 수집(top 500) + 서브레딧 3→6개 확장 + hot 피드 병행 + Polygon import 수정 + 파일 기반 티커 캐시(7일 TTL) + _COMMON_WORDS 확장 |
| **Function UX Effect** | `--reddit-run-now` 실행 시 54개→416+개 수집, 8개→42개 유효 종목 추출. Polygon 검증: 첫 실행 9분 → 이후 0초(캐시 히트) |
| **Core Value** | Reddit 신호 데이터 커버리지 15배 향상으로 Forward Testing 신뢰도 확보. 특히 WSB 군중심리 지표인 Daily Discussion Thread가 핵심 신호 소스로 편입 |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | reddit-gpt4-quant Forward Testing의 데이터 품질 문제 해결. 수집량 부족으로 신호 유효성 검증 불가 |
| **WHO** | news-rsi-trading 시스템 운영자 (크론탭 매일 16:30 ET 자동 실행) |
| **RISK** | Polygon 무료 플랜 5 req/min 제한 / PRAW rate limit / Daily Thread 없는 날(주말 일부) |
| **SUCCESS** | 수집 게시글 200개+ / 유효 종목 20개+ / Polygon 재실행 0초 / BITF·STO 등 가짜 아닌 실제 소형주 포함 |
| **SCOPE** | 수정 파일: reddit_collector.py, config.py, collector.py |

---

## 1. 문제 분석

### 1.1 기존 수집 구조의 한계

| 항목 | 기존 | 문제 |
|------|------|------|
| 수집 방식 | `subreddit.new(limit=200)` | WSB 활발한 날 200개 = 4-8시간치만 커버 |
| Flair 필터 | Allowlist (DD/Discussion/Fundamentals만) | 무flair 게시글, News, Chart 등 전부 제외 |
| Daily Thread | 수집 안 함 | WSB 13,000+ 댓글 완전 누락 |
| Polygon 검증 | `from massive import RESTClient` | ModuleNotFoundError → 검증 스킵, 전체 통과 |
| 가짜 티커 | `_COMMON_WORDS` 20개 | BUY/SELL/HOLD/ATM/WTI 등 가짜 티커 대량 추출 |
| 캐시 | 없음 | 매 실행마다 12초×N종목 대기 |

### 1.2 실제 데이터 (2026-04-18 기준, 주말)

```
기존: 54개 게시글, 8개 유효 종목
개선: 416개 게시글 (new+hot+daily_thread), 42개 유효 종목
      - WSB Daily Discussion Thread: 473개 댓글 (top 500 기준)
      - r/stocks Daily Thread: 140개 댓글
      - r/thetagang Daily Thread: 134개 댓글
```

---

## 2. 구현 상세

### 2.1 collector.py — Polygon import 수정

```python
# 기존 (오류)
from massive import RESTClient
# 수정
from polygon import RESTClient
```

**배경**: Polygon.io가 Massive.com으로 리브랜딩했으나 Python 패키지명은 `polygon-api-client` 유지.
pip install polygon-api-client 필요.

### 2.2 reddit_collector.py — `_fetch_subreddit` 개선

```python
# limit 200 → 1000
# continue → break (시간 역순 보장 → early stop)
# flair allowlist → denylist
excluded_flairs = {"Gain", "Loss", "Meme", "YOLO", "Daily Discussion - Meme", "Screenshot"}

# hot 피드 병행 (중복 제거)
_process_feed(subreddit.new(limit=1000))
_process_feed(subreddit.hot(limit=100))
```

### 2.3 reddit_collector.py — `_fetch_daily_thread` 신규

핵심 구현:
- **탐색 순서**: `subreddit.sticky(1)` → `subreddit.sticky(2)` → `subreddit.hot(limit=20)` 패턴 매칭
- **서브레딧별 패턴**: config.REDDIT_DAILY_PATTERNS에 정의
- **댓글 정렬**: score 내림차순 top N (config.REDDIT_DAILY_THREAD_COMMENTS = 500)
- **포맷**: 댓글 1개 = post 1개 (title="", body_excerpt=comment.body, source="daily_thread")

```python
REDDIT_DAILY_PATTERNS = {
    "wallstreetbets": ["daily discussion"],
    "investing":      ["daily general discussion", "daily discussion"],
    "stocks":         ["daily discussion"],
    "options":        ["megathread", "safe haven", "what are your moves"],
    "StockMarket":    ["daily discussion"],
    "thetagang":      ["daily discussion", "what are your moves"],
}
```

### 2.4 reddit_collector.py — `_COMMON_WORDS` 확장

추가된 카테고리:
- **옵션 용어**: ATM, ITM, OTM, DTE, IV, HV, PNL, THETA, DELTA, GAMMA, VEGA, RHO, PUT, CSP, PMC, LEAPS, PMCC
- **암호화폐**: BTC, ETH, SOL, XRP, DOGE, SHIB
- **ETF/인덱스**: VTI, VOO, VT, VTV, GLD, SLV, IWM, TLT, HYG, LQD, XLF, XLE
- **원자재/지표**: WTI, NDX, USD, EUR, JPY, DXY
- **비종목 약어**: DCA, LOL, SAY, MAY, OPEN, CASH, SABER, AXIOS, ADAS, CTA, BTO, XSP
- **국가명**: IRAN, IRAQ, CHINA, KOREA, INDIA

### 2.5 reddit_collector.py — 파일 기반 Polygon 캐시

```python
_TICKER_CACHE_FILE = "data/reddit/ticker_cache.json"
_TICKER_CACHE_TTL_DAYS = 7

# 포맷: {"NVDA": {"valid": true, "checked": "2026-04-18"}}
```

동작:
1. 캐시 로드 (7일 이내 항목만)
2. 신규 티커만 Polygon 호출 (12초 간격)
3. 결과를 파일에 병합 저장
4. 재실행 시 전체 캐시 히트 → 0초

### 2.6 config.py — 서브레딧 확장

```python
# 기존
REDDIT_SUBREDDITS = ["wallstreetbets", "investing", "stocks"]

# 수정
REDDIT_SUBREDDITS = [
    "wallstreetbets", "investing", "stocks",
    "options", "StockMarket", "thetagang",
]

# 신규 설정
REDDIT_DAILY_THREAD_COMMENTS = 500
REDDIT_DAILY_PATTERNS = { ... }
```

---

## 3. 성능 지표

| 지표 | 기존 | 개선 후 |
|------|------|---------|
| 수집 게시글 (주말) | 54개 | 416개 (+669%) |
| 유효 종목 | 8개 | 42개 (+425%) |
| Polygon 검증 (첫 실행) | 즉시 (스킵됨) | ~9분 (1회) |
| Polygon 검증 (이후) | - | 0초 (캐시) |
| 총 실행 시간 (이후) | ~3분 | ~2분 |
| 가짜 티커 비율 | 높음 (검증 스킵) | 낮음 (Polygon 검증 정상화) |

---

## 4. 리스크 및 대응

| 리스크 | 대응 |
|--------|------|
| WSB Daily Thread 없는 날 | sticky/hot 탐색 후 없으면 빈 리스트 반환 (graceful) |
| Polygon 429 (첫 실행) | 12초 간격 준수, collector.py 내부 3회 retry |
| _COMMON_WORDS 과도한 필터 | 실제 소형주 티커가 일반 단어와 겹치면 Stage 1($TICKER 명시) 우선 |
| PRAW rate limit | 서브레딧당 2초 sleep, 전체 6개 = 약 2분 수집 시간 |

---

## 5. 성공 기준 (SC)

| # | 기준 | 검증 방법 |
|---|------|----------|
| SC-01 | `--reddit-run-now` 실행 시 평일 200개+ 게시글 수집 | 로그 확인 |
| SC-02 | WSB Daily Thread 댓글 수집 확인 (source="daily_thread") | wsb_posts.json 확인 |
| SC-03 | Polygon 재실행 시 "전체 캐시 히트" 메시지 출력 | 로그 확인 |
| SC-04 | 유효 종목 20개+ 저장 | wsb_posts.json 종목 수 확인 |
| SC-05 | BTC/WTI/ATM 등 비종목 제외 확인 | 추출 티커 목록 확인 |
| SC-06 | data/reddit/ticker_cache.json 생성 및 7일 TTL 동작 | 파일 존재 확인 |

---

## 6. 파일 변경 목록

| 파일 | 변경 유형 | 내용 |
|------|----------|------|
| `collector.py` | 수정 | `from massive` → `from polygon` |
| `config.py` | 수정 | REDDIT_SUBREDDITS 확장, REDDIT_DAILY_PATTERNS/REDDIT_DAILY_THREAD_COMMENTS 추가 |
| `reddit_collector.py` | 수정 | `_fetch_subreddit` 개선, `_fetch_daily_thread` 신규, `_COMMON_WORDS` 확장, 파일 캐시 구현 |
