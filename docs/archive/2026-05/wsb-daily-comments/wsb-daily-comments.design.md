# Design: wsb-daily-comments — Daily Thread 댓글 수집 확장

**Feature**: wsb-daily-comments
**Date**: 2026-05-02 (backfill)
**Status**: Design (Option A — Minimal)
**Plan**: `docs/01-plan/features/wsb-daily-comments.plan.md`
**Implementation Status**: ✅ 이미 완료 (2026-04-22, Match Rate 100%)

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 2026-04-22 기준 28개 종목 66개 포스트 — NVDA 2개, MSFT 1개로 IT 대형주 신호 부족. Daily Thread 댓글 활용 부족이 근본 원인 |
| **WHO** | Reddit 기반 페이퍼 트레이딩 시스템 운영자 |
| **RISK** | 댓글 증가로 Polygon 검증 대기 시간 증가 / 짧은 댓글 감성분석 정확도 낮을 수 있음 |
| **SUCCESS** | 수집 후 NVDA/MSFT/AMD 등 IT 대형주 포스트 수 3개 이상 / `source: 'daily_thread'` 태그 확인 |
| **SCOPE** | `config.py` 상수 수정, `reddit_collector.py` `_fetch_daily_thread()` 확장 |

---

## 1. Overview

Plan에서 제시된 7개 FR을 **최소 변경**(Option A — Minimal Changes)으로 충족하는 설계. 신규 클래스/모듈 도입 없이 기존 `RedditCollector._fetch_daily_thread()` 메서드 내부에서 상한·로깅·MoreComments 확장만 처리한다.

### 1.1 선택된 Architecture: Option A — Minimal

| 항목 | 결정 |
|------|------|
| 신규 파일 | 0개 |
| 수정 파일 | 2개 (`config.py`, `reddit_collector.py`) |
| 신규 클래스 | 0개 |
| 추가 코드 라인 | ~10 lines |
| 회귀 위험 | 🟢 낮음 (기존 호출 경로 불변) |

**Rationale**:
- Reddit Daily Thread는 **단일 패턴**이라 Strategy/Fetcher 추상화의 ROI가 낮음 (YAGNI)
- 기존 `wsb_posts.json` 스키마와 호출 경로가 안정 — 리팩토링 시 회귀 위험만 증가
- `source: 'daily_thread'` 태그가 이미 구현되어 있어 downstream(`wsb_signal_engine`) 코드 변경 불필요

### 1.2 Discarded Options

| Option | Reject 사유 |
|--------|------------|
| B (Clean Architecture / DailyThreadFetcher 클래스) | Daily Thread 외 다른 thread 패턴(주말/옵션) 추가 계획 없음. 추상화 ROI 음수 |
| C (Pragmatic / 함수 분리) | Reddit collector 호출 경로 시그니처 변경 발생, 회귀 비용 vs 이득 비대칭 |

---

## 2. Architecture

### 2.1 데이터 흐름

```
config.REDDIT_DAILY_THREAD_COMMENTS = 1000   ← 상수 변경 (구 500)
       │
       ▼
RedditCollector.collect(date_str)
       │
       ├─ 일반 포스트 수집 (subreddit.hot/new)
       │       └─ source: 일반 (없음)
       │
       └─ _fetch_daily_thread(name)               ◀── 본 설계 핵심
              ├─ 1. sticky 탐색 (#1, #2)
              ├─ 2. hot fallback (limit=50)
              ├─ 3. new fallback (limit=20)
              ├─ thread.comments.replace_more(limit=3)   [Plan FR-02 수정: limit=0→3]
              ├─ score 내림차순 정렬 → top 1000개         [FR-03]
              ├─ 각 댓글 → post dict (source: "daily_thread")  [FR-04]
              └─ logger.info(서브레딧별 수집량)            [FR-07]
       │
       ▼
posts_all = [일반 posts] + [daily_thread posts]
       │
       ▼
_extract_tickers(posts_all)
       │
       ▼
data/reddit/{date}/wsb_posts.json   ← 기존 스키마 유지 [FR-05, NFR-01]
       │
       ▼
wsb_signal_engine.run_pipeline()    ← source 태그 무관 처리 [FR-06]
```

### 2.2 모듈 구조

| Layer | 모듈 | 책임 | 변경 여부 |
|-------|------|------|----------|
| Config | `config.py` | `REDDIT_DAILY_THREAD_COMMENTS` 상수 정의 | ✏️ 수정 (500→1000) |
| Collection | `reddit_collector.RedditCollector._fetch_daily_thread()` | sticky/hot/new 탐색 + 댓글 추출 | ✏️ 수정 (상한+로깅+MoreComments) |
| Collection | `reddit_collector.RedditCollector.collect()` | 일반 포스트 + daily_thread 합산 | ↔️ 변경 없음 (이미 호출 중) |
| Storage | `data/reddit/{date}/wsb_posts.json` | 통합 저장 | ↔️ 스키마 호환 |
| Signal | `wsb_signal_engine.py` | source 무관 처리 | ↔️ 변경 없음 |

---

## 3. Data Model

### 3.1 wsb_posts.json 스키마 (변경 없음, 호환 유지)

```jsonc
{
  "NVDA": [
    {
      "title": "NVDA earnings beat",          // 일반 포스트는 제목 있음
      "body_excerpt": "...",                  // 최대 GPT_POST_BODY_MAX(300자)
      "top_comments": ["...", "..."],         // 일반 포스트만 / daily_thread는 []
      "subreddit": "wallstreetbets",
      "created_utc": 1714060800,
      "bullish": true,                        // 감성분석 후 채워짐, 초기 None
      "source": "daily_thread"                // ✨ daily_thread 댓글 식별자
    },
    {
      "title": "",                            // ← daily_thread 댓글은 빈 제목
      "body_excerpt": "I'm long NVDA $500 calls",
      "top_comments": [],
      "subreddit": "wallstreetbets",
      "created_utc": 1714060900,
      "bullish": null,
      "source": "daily_thread"
    }
  ]
}
```

### 3.2 Source 태그 의미 (NFR-01: 하위 호환)

| `source` 값 | 의미 | 예시 |
|------------|------|------|
| `"daily_thread"` | Daily Discussion Thread 댓글 | title="" (빈 문자열) |
| 키 부재 / 기타 | 일반 서브레딧 포스트 | title 채워짐 |

`wsb_signal_engine`은 source 키를 참조하지 않으므로 추가 분기 없음.

---

## 4. API Contract (config 상수)

| 상수 | 위치 | 기본값 | 의미 |
|------|------|--------|------|
| `REDDIT_DAILY_THREAD_COMMENTS` | `config.py:32` | **1000** (구 500) | Daily Thread 1개당 수집 댓글 상한 |
| `REDDIT_DAILY_PATTERNS` | `config.py` (기존) | dict[subreddit] → list[pattern] | sticky/hot/new 매칭 패턴 |
| `GPT_POST_BODY_MAX` | `config.py` (기존) | 300 | 댓글 본문 슬라이싱 길이 |

**Behavior Contract**:
- 댓글 수가 1000 미만이면 전부 수집 (filter 후 길이만큼)
- `[deleted]` / `[removed]` / 빈 문자열 댓글은 제외
- `replace_more(limit=3)` — top-level MoreComments 3회까지 확장 (Plan FR-02의 `limit=0`보다 적극적, 댓글 풀 확보 우선)

---

## 5. Logging Contract (FR-07)

```
INFO  r/wallstreetbets: Daily Thread 'Daily Discussion Thread for ...' (전체 1543개 댓글) → top 1000개 수집
INFO  r/wallstreetbets: Daily Thread 댓글 821개 추출 (source=daily_thread)
WARN  r/wallstreetbets Daily Thread 댓글 수집 실패: <Exception>
DEBUG r/{name}: Daily Discussion Thread 없음
```

3가지 로그 포인트로 운영자가 (1) 발견된 thread, (2) 실제 추출량, (3) 실패 사유를 추적할 수 있다.

---

## 6. Error Handling

| 시나리오 | 처리 | 영향 |
|---------|------|------|
| sticky/hot/new 모두에서 thread 미발견 | `logger.debug` + 빈 리스트 반환 | NFR-02: 에러 전파 없음 |
| `thread.comments.replace_more` 예외 | `logger.warning` + 부분 수집분 반환 | 부분 성공 가능 |
| `comment.body == "[deleted]"` | continue (다음 댓글로) | 유효 댓글만 수집 |
| PRAW 인증 실패 (`__init__`) | `self._reddit = None` | collect() 호출 시 빈 결과 |

**원칙**: Daily Thread 수집은 보조 데이터이므로 실패 시 전체 파이프라인을 멈추지 않는다.

---

## 7. Trade-offs (Plan vs Implementation 차이 명시)

| Plan FR | Plan 명세 | Implementation 결정 | 사유 |
|---------|----------|--------------------|------|
| FR-02 | `replace_more(limit=0)` — top-level only | `replace_more(limit=3)` (reddit_collector.py:252) | top-level 댓글 풀이 1000개 미달일 때 신호 손실 → MoreComments 3회 확장으로 풀 확보. 안정성 우려는 try/except로 흡수 |

기타 FR-01, 03~07 모두 Plan과 일치.

---

## 8. Test Plan

### 8.1 정적 검증 (Analysis 100% 검증 완료)

`docs/03-analysis/wsb-daily-comments.analysis.md` 참조. 5개 SC 모두 Met:

| SC | 위치 | 상태 |
|----|------|------|
| SC-01: 상수 1000 | `config.py:32` | ✅ |
| SC-02: 로그 출력 | `reddit_collector.py:237, 266` | ✅ |
| SC-03: source 태그 | `reddit_collector.py:261` (실제 L271) | ✅ |
| SC-04: IT 대형주 코드 패스 | `signal_engine` 전체 처리 | ✅ |
| SC-05: 감성분석 daily_thread 포함 | source 필터 없음 확인 | ✅ |

### 8.2 권장 회귀 테스트 (현재 미구현, 향후 별도 PDCA로)

| # | 시나리오 | 도구 |
|---|---------|------|
| 1 | `_fetch_daily_thread` mock thread (1543 comments) → top 1000 추출 | pytest + MockSubmission |
| 2 | `[deleted]` 댓글 50개 섞인 1050개 → 정확히 1000개 유효 추출 | pytest |
| 3 | sticky 미일치 → hot fallback → new fallback 순서 | pytest + Reddit mock |
| 4 | `replace_more` 예외 → 부분 결과 + warning 로그 | pytest + monkeypatch |

---

## 9. Performance Considerations

| 항목 | 추정 | 비고 |
|------|------|------|
| 댓글 수집 시간 | +5~10초/서브레딧 | `replace_more(limit=3)` 추가 호출 |
| 메모리 | +~1MB | 1000개 × 300자 |
| Polygon API 호출 | 변동 없음 | ticker_cache.json 7일 TTL (NFR-03) |
| 신호 품질 (NVDA/MSFT) | mention 수 ~2배 기대 | velocity_state 정확도 향상 |

---

## 10. Backward Compatibility (NFR-01, NFR-02, NFR-03)

| Aspect | 호환 여부 | 검증 |
|--------|----------|------|
| `wsb_posts.json` 스키마 | ✅ 유지 | source 키는 옵셔널 |
| 기존 호출 경로 | ✅ 유지 | `RedditCollector.collect()` 시그니처 불변 |
| Polygon ticker 검증 | ✅ 유지 | `_extract_tickers` 로직 불변 |
| 에러 처리 | ✅ 유지 | 빈 리스트 반환 정책 |
| `wsb_signal_engine` 입력 | ✅ 유지 | source 키 무시 |

---

## 11. Implementation Guide

### 11.1 변경 위치

| # | 파일 | 라인 | 변경 |
|---|------|------|------|
| 1 | `config.py` | L32 | `REDDIT_DAILY_THREAD_COMMENTS = 500` → `1000` + 코멘트 추가 |
| 2 | `reddit_collector.py` | L245-248 | sticky 탐색 후 thread 정보 + 상한 로그 강화 |
| 3 | `reddit_collector.py` | L252 | `replace_more(limit=0)` → `limit=3` |
| 4 | `reddit_collector.py` | L276 | 추출 완료 시 댓글 수 + source 명시 로그 |

### 11.2 구현 순서

1. config.py 상수 변경 (1줄)
2. reddit_collector.py 로그 메시지 강화 (info 2건)
3. replace_more limit 조정 (1줄)
4. 수동 검증: `python main.py --reddit-run-now` → wsb_posts.json에 source=daily_thread 항목 확인
5. wsb_signal_engine 동작 확인 (source 무관 처리)

### 11.3 Session Guide (Module Map)

| Module | Files | Items | Recommended Session |
|--------|-------|-------|---------------------|
| **module-1: config** | config.py | 상수 1개 변경 | Session 1 (5분) |
| **module-2: collector** | reddit_collector.py | _fetch_daily_thread 로그·MoreComments 조정 | Session 1 (15분) |
| **module-3: verify** | (실행) | `--reddit-run-now` 1회 + wsb_posts.json 검증 | Session 1 (10분) |

**Recommended Session Plan**: 1 session (총 ~30분) — SCOPE 최소이므로 분할 불필요.

`/pdca do wsb-daily-comments --scope module-1` 등으로 부분 실행 가능 (현재는 이미 구현 완료).

---

## 12. References

- **Plan**: `docs/01-plan/features/wsb-daily-comments.plan.md` (FR-01~07, NFR-01~03, SC-01~05)
- **Analysis**: `docs/03-analysis/wsb-daily-comments.analysis.md` (Match Rate 100%)
- **Source code**:
  - `config.py:32`
  - `reddit_collector.py:198-278` (`_fetch_daily_thread()`)
- **ARCHITECTURE.md §5**: Reddit 파이프라인 — `_fetch_daily_thread` 호출 위치
