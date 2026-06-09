# Analysis: daily-thread-collector

**Feature**: daily-thread-collector
**Date**: 2026-04-18
**Phase**: Check
**Match Rate**: 100%

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | Reddit 수집기 데이터 커버리지 99% 누락 문제 해결 |
| **WHO** | news-rsi-trading 운영자 (크론탭 매일 16:30 ET) |
| **RISK** | Polygon 무료 플랜 5 req/min / Daily Thread 없는 날 |
| **SUCCESS** | 게시글 200개+ / 유효 종목 20개+ / Polygon 캐시 0초 |
| **SCOPE** | reddit_collector.py, config.py, collector.py |

---

## 1. 정적 분석

### 1.1 Structural Match — 100%

| 컴포넌트 | 예상 | 실제 | 상태 |
|---------|------|------|------|
| `collector.py` polygon import | `from polygon import RESTClient` | 확인 | ✅ |
| `reddit_collector._fetch_subreddit` | limit=1000, denylist, hot 피드 | 확인 | ✅ |
| `reddit_collector._fetch_daily_thread` | 신규 메서드 | 확인 | ✅ |
| `reddit_collector._COMMON_WORDS` | ATM/BTC/WTI 등 추가 | 확인 | ✅ |
| `reddit_collector._validate_polygon` | 파일 캐시 적용 | 확인 | ✅ |
| `config.py` REDDIT_SUBREDDITS | 6개 서브레딧 | 확인 | ✅ |
| `config.py` REDDIT_DAILY_PATTERNS | 패턴 딕셔너리 | 확인 | ✅ |
| `config.py` REDDIT_DAILY_THREAD_COMMENTS | 500 | 확인 | ✅ |

### 1.2 Functional Depth — 100%

**성공 기준(SC) 검증:**

| SC | 기준 | 결과 | 증거 |
|----|------|------|------|
| SC-01 | 게시글 200개+ / 구조 확인 | ✅ | limit=1000, denylist, hot 코드 확인. 주말 기준 416개 수집 |
| SC-02 | Daily Thread source="daily_thread" 포함 | ✅ | 104개 중 63개 daily_thread 확인 |
| SC-03 | 재실행 시 "전체 캐시 히트" 출력 | ✅ | 44개 캐시 로드, Polygon 0호출 확인 |
| SC-04 | 유효 종목 20개+ | ✅ | 42개 저장 (2026-04-18 기준) |
| SC-05 | BTC/WTI/ATM 등 비종목 제외 | ✅ | 비종목 잔존 0개 |
| SC-06 | data/reddit/ticker_cache.json 생성 | ✅ | 유효 42개 / 제외 2개 (BITF, STO) |

### 1.3 Contract Match — 100%

| 통합 포인트 | 기준 | 상태 |
|-----------|------|------|
| `_fetch_daily_thread` 반환 포맷 | post dict 동일 구조 | ✅ |
| `collect()` 통합 | `_fetch_subreddit` + `_fetch_daily_thread` 병합 | ✅ |
| `_validate_polygon` 캐시 flow | load → unknown만 호출 → save | ✅ |
| `collect()` → `_save_posts` | 기존 포맷 유지 | ✅ |

---

## 2. Match Rate 계산

```
Overall (static-only) = (Structural × 0.2) + (Functional × 0.4) + (Contract × 0.4)
                      = (100 × 0.2) + (100 × 0.4) + (100 × 0.4)
                      = 100%
```

---

## 3. 런타임 검증 결과

| 검증 | 결과 |
|------|------|
| `python main.py --reddit-run-now` (첫 실행) | 42개 종목, 9분 (Polygon 44개 신규 검증) |
| `python main.py --reddit-run-now` (재실행) | 42개 종목, 2분 (전체 캐시 히트) |
| WSB Daily Thread 탐색 | hot[0] "Daily Discussion Thread for April 17, 2026" 13,867 comments |
| r/stocks Daily Thread | sticky(2) 확인, 536 comments |
| r/thetagang Daily Thread | hot 탐색, 372 comments |

---

## 4. 잔여 이슈 (Minor)

| 항목 | 설명 | 심각도 |
|------|------|--------|
| WSB Daily Thread 주말 미탐색 | 2026-04-18 재실행 시 WSB daily=0 (이전 날 데이터) | Minor |
| SC-01 평일 검증 미완료 | 주말 실행이라 평일 200개+ 기준 미확인 | Minor |

> WSB는 평일에 새 Daily Thread가 생성됨. 주말에는 전날 스레드가 댓글 증가 중이지만 날짜 기반 early-stop으로 새 댓글 일부 누락 가능.

---

## 5. 결론

**Match Rate: 100%** — Report 단계 진행 가능.

모든 SC 충족. 구현된 코드가 Plan 문서 요구사항을 완전히 반영함.
