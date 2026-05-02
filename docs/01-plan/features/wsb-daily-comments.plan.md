# Plan: wsb-daily-comments — Daily Thread 댓글 수집 확장

**Feature**: wsb-daily-comments
**Date**: 2026-04-22
**Status**: Plan

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | Daily Discussion Thread 댓글이 일반 포스트와 동일하게 처리되고 수집 상한이 500개로 IT 대형주(NVDA=2, MSFT=1) mention이 부족해 신호 품질 저하 |
| **Solution** | Daily Thread 댓글 1000개로 확장 + `source: 'daily_thread'` 태그로 구분하여 wsb_posts.json 합산, 감성분석 동일 적용 |
| **Function UX Effect** | 기존 wsb_posts.json 구조 유지하며 NVDA/MSFT 등 IT 대형주 포스트 수 증가 → velocity/ranking 상승 |
| **Core Value** | 추가 인프라 없이 config 1줄 + 코드 소량 수정으로 IT 대형주 신호 커버리지 향상 |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 2026-04-22 기준 28개 종목 66개 포스트 — NVDA 2개, MSFT 1개로 대형주 신호 부족. Daily Thread 댓글 활용 부족이 근본 원인 |
| **WHO** | Reddit 기반 페이퍼 트레이딩 시스템 운영자 |
| **RISK** | 댓글 증가로 Polygon 검증 대기 시간 증가 / 짧은 댓글 감성분석 정확도 낮을 수 있음 |
| **SUCCESS** | 수집 후 NVDA/MSFT/AMD 등 IT 대형주 포스트 수 3개 이상 / `source: 'daily_thread'` 태그 확인 |
| **SCOPE** | `config.py` 상수 수정, `reddit_collector.py` `_fetch_daily_thread()` 확장 |

---

## 1. 요구사항

### 1.1 기능 요구사항

| ID | 요구사항 | 우선순위 |
|----|---------|---------|
| FR-01 | `REDDIT_DAILY_THREAD_COMMENTS` 500 → 1000으로 변경 | Must |
| FR-02 | `replace_more(limit=0)` 유지 — top-level 댓글만 수집 (API 안정성) | Must |
| FR-03 | 수집된 댓글 score 내림차순 정렬 후 상위 1000개 선별 | Must |
| FR-04 | 각 댓글 포스트에 `"source": "daily_thread"` 태그 포함 | Must |
| FR-05 | 일반 포스트와 동일하게 `wsb_posts.json`에 합산 저장 | Must |
| FR-06 | 댓글 포스트에도 감성분석(FinBERT/GPT-5.4 Mini) 동일 적용 | Must |
| FR-07 | 수집 시 댓글 수/서브레딧별 로그 출력 | Should |

### 1.2 비기능 요구사항

| ID | 요구사항 |
|----|---------|
| NFR-01 | 기존 `wsb_posts.json` 스키마 호환 유지 — 하위 호환 |
| NFR-02 | 수집 실패 시 기존처럼 빈 리스트 반환 (에러 전파 없음) |
| NFR-03 | Polygon API 호출 패턴 변경 없음 (ticker 검증 로직 불변) |

---

## 2. 현재 상태 분석

### 2.1 현재 코드 (`reddit_collector.py`)

```python
# _fetch_daily_thread() 현재
REDDIT_DAILY_THREAD_COMMENTS = 500  # config.py

thread.comments.replace_more(limit=0)
top_comments = sorted(
    [c for c in thread.comments if hasattr(c, "body")],
    key=lambda c: getattr(c, "score", 0),
    reverse=True,
)[:config.REDDIT_DAILY_THREAD_COMMENTS]  # 500개 상한

# 각 댓글 → post dict (source: "daily_thread" 이미 포함)
posts.append({
    "title": "",
    "body_excerpt": body[:config.GPT_POST_BODY_MAX],  # 300자
    "top_comments": [],
    "subreddit": name,
    "created_utc": int(comment.created_utc),
    "bullish": None,
    "source": "daily_thread",  # 이미 구현됨
})
```

### 2.2 문제점

- 상한 500개: 3개 서브레딧 × 500 = 최대 1500개 → 1000개로 확장 시 최대 3000개
- 실제 수집에서 IT 대형주 댓글이 충분히 포함되지 않음
- `source` 태그는 이미 구현됨 — 확장 작업은 config 수정이 핵심

---

## 3. 성공 기준

| SC | 기준 | 측정 방법 |
|----|------|---------|
| SC-01 | `REDDIT_DAILY_THREAD_COMMENTS = 1000` 적용 | config.py 확인 |
| SC-02 | 수집 로그에 "top 1000개 수집" 메시지 출력 | 실행 로그 확인 |
| SC-03 | wsb_posts.json에 `source: 'daily_thread'` 항목 존재 | 파일 내용 확인 |
| SC-04 | IT 대형주 최소 1개 이상 포스트 수 증가 (NVDA or MSFT or AMD) | 수집 후 종목별 카운트 |
| SC-05 | 감성분석 파이프라인이 daily_thread 댓글 포함 처리 | wsb_signal_engine 정상 동작 |

**전체 5/5 충족 시 완료**

---

## 4. 구현 범위

### 4.1 변경 파일

| 파일 | 변경 유형 | 변경 내용 |
|------|---------|---------|
| `config.py` | 수정 | `REDDIT_DAILY_THREAD_COMMENTS`: 500 → 1000 |
| `reddit_collector.py` | 수정 | `_fetch_daily_thread()` 로그 메시지 개선 + 댓글 수 로깅 강화 |

### 4.2 변경 없는 파일

| 파일 | 이유 |
|------|------|
| `wsb_signal_engine.py` | source 태그 무관하게 모든 포스트 동일 처리 |
| `wsb_posts.json` 스키마 | 기존 구조 그대로 유지 |
| `reddit_backtester.py` | 데이터 포맷 변경 없음 |

---

## 5. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 짧은 댓글 감성분석 노이즈 | 낮음 | wsb_signal_engine의 consensus 필터가 다수결 → 노이즈 희석 |
| Polygon ticker 검증 대기 시간 증가 | 낮음 | ticker_cache.json 7일 TTL → 신규 티커만 검증 |
| 3000개 댓글로 메모리 증가 | 미미 | 포스트당 ~300자 → 최대 ~1MB 수준 |

---

## 6. 다음 단계

```
/pdca design wsb-daily-comments
/pdca do wsb-daily-comments
```
