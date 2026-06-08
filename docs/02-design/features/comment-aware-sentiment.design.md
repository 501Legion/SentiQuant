# Design: Comment-Aware Sentiment

**Feature**: comment-aware-sentiment
**작성일**: 2026-06-06
**선택 아키텍처**: **Option C — 실용 균형** (score() 댓글 확장 + 기존 location 가중 인프라 활성화)
**상위 Plan**: `docs/01-plan/features/comment-aware-sentiment.plan.md`

---

## Context Anchor (Plan 승계)

| 항목 | 내용 |
|------|------|
| **WHY** | 라이브/백테스트가 댓글을 버려서 데이터포인트 부족 → 컨센서스 0 → 매매 0. 댓글을 개별 집계(가중<1)해 N↑ + 신호 정확도↑. |
| **WHO** | 운영자(본인). |
| **RISK** | 1000댓글 = 429/지연(6-06 실측), 백테스트 의도적 변동, mentions 인플레. ※ 단일 DD 방향 주도는 **의도된 동작**. |
| **SUCCESS** | 댓글 개별·가중 카운트(N=글+댓글) / 100·1000+가드 / 품질필터 / FinBERT 공용 경로(백테스트+라이브) / 뉴스 무영향. |
| **SCOPE** | 수정: `reddit_collector`·`sentiment_provider`·`wsb_signal_engine`·`config`. 신규: 테스트. 제외: 뉴스 모델/경로. |

---

## 1. Overview

댓글을 **본문과 동급의 개별 감성 데이터포인트(가중<1)**로 집계한다. 핵심 모델은
**"DD가 있을 때만, DD 방향으로 거래"** — 잡담 글로는 N(≥10)이 안 차서 매매하지 않고,
제대로 된 DD(글+댓글 스레드)가 뜬 종목은 그 스레드의 감성 방향으로 간다.

**핵심 통찰 (현 코드 분석 기반)**:
1. `FinBERTProvider.score()`가 `NEUTRAL_FILTER_MIN_ARTICLES(≥10)`를 `n_valid`에 적용
   (`sentiment_provider.py:206-210`) → **여기서 댓글을 개별 article로 확장하면 N 게이트가
   자연히 댓글 포함으로 동작**. 단일 확장 지점.
2. 가중 인프라 완비 — `compute_weighted_counts(source_quality_weight × location_weight)`
   (`wsb_signal_engine.py:85-104`) + `COMMUNITY_COMMENT_MENTION_WEIGHT=0.5`(`config.py:295`).
   plan의 `COMMENT_WEIGHT`는 **신규 상수가 아니라 이 기존 0.5 재사용/튜닝**.
3. **그러나 가중은 현재 "휴면"** — `build_daily_snapshot`이 backtester(`:290`)·community_live
   어디서도 `labeled_posts`를 넘기지 않아 항상 raw count로 폴백(`wsb_signal_engine.py:151-154`).
   → Option C는 댓글 확장 + **`labeled_posts(location)` 전파로 이 가중을 활성화**한다.
4. 공용 경로 — backtester·community_live가 동일 `get_provider`+`WSBSignalEngine.run_pipeline`
   사용(`reddit_backtester.py:201-250`) → 한 곳 수정으로 양쪽 반영. 뉴스 기사는 `top_comments`가
   없어 동일 코드에서 무영향.

---

## 2. Selected Architecture — Option C

```
[collector] 글 수집 시 댓글 100/1000 + 품질필터 + DD flair 판별 → post["top_comments"]
     │ (스키마 불변: top_comments = list[str])
     ▼
[provider.score()] 각 post → [본문 article] + [댓글 article 각각]으로 확장 분류
     │ 각 detail에 location("body"|"comment") + source_quality_weight 부여
     │ n_valid(≥10 게이트)가 댓글 포함으로 동작
     ▼
[engine._score_posts()] details → counts + labeled_posts[{label,location,sqw}] 구성
     │
     ▼
[engine 컨센서스 + build_daily_snapshot(labeled_posts=…)] 가중 카운트 활성화
     (본문 weight=BODY 1.0 / 댓글 weight=COMMENT 0.5)
```

**왜 C인가**: 댓글 가중(<1) 의도를 **기존 인프라 재사용**으로 정확히 살리면서, 신규 모듈/엔진
대수술(B)을 피한다. A(최소)는 가중 활성화가 빠져 plan의 "댓글 가중<1"과 어긋난다.

---

## 3. 모듈 분해 (Module Map)

| 모듈 | 파일 | 책임 | --scope 키 |
|------|------|------|-----------|
| **M1 Config** | `config.py` | 수집량·DD_FLAIRS·품질/비용 가드 상수. 댓글 가중은 기존 `COMMUNITY_COMMENT_MENTION_WEIGHT` 재사용 | `module-1` |
| **M2 Collector** | `reddit_collector.py` | flair 기반 DD 판별 → 댓글 100/1000 수집, `replace_more` 가드, 품질 필터 | `module-2` |
| **M3 Provider** | `sentiment_provider.py` | `FinBERTProvider.score()`가 본문+댓글 개별 분류, detail에 `location`/`source_quality_weight` | `module-3` |
| **M4 Engine wiring** | `wsb_signal_engine.py` | `_score_posts`가 details→`labeled_posts` 구성, 호출처(`community_live`/`reddit_backtester`)가 `build_daily_snapshot(labeled_posts=…)` 전파 | `module-4` |
| **M5 Tests** | `tests/test_comment_aware_sentiment.py` | 개별 카운트·DD 수집·가중<1·품질필터·비용가드·공용반영 | `module-5` |

---

## 4. 핵심 설계 결정

| ID | 결정 | 내용 |
|----|------|------|
| **D1** | 확장 지점 = `score()` | 본문+댓글을 개별 article로 확장. `n_valid(≥10)`가 자연히 댓글 포함 → N 게이트 활성. 백테스트/라이브 자동 공용. |
| **D2** | 가중 활성화 = `labeled_posts` 전파 | 댓글 detail에 `location="comment"` → 기존 `_location_weight`(0.5) 적용. **휴면 가중을 켜는 게 핵심**. 본문=`"body"`(1.0). |
| **D3** | N 게이트 정의 | `NEUTRAL_FILTER_MIN_ARTICLES`(score 내부 n_valid)가 글+댓글 각 1로 카운트 → DD 스레드가 게이트 통과. (단일 DD 트리거 = 의도) |
| **D4** | 컨센서스 가중 | 방향 판정(`build_daily_snapshot`의 weighted 카운트/`consensus_ratio`)은 가중 기준. run_pipeline `_filter_consensus`(raw)는 **1차 게이트 유지**, 최종 방향은 가중 스냅샷 — 구현 시 일관성 확인(§7 Open-1). |
| **D5** | 댓글 가중 값 | 기존 `COMMUNITY_COMMENT_MENTION_WEIGHT=0.5` 재사용(plan "0.3"은 예시). **설계 확정값=0.5**, 실측 후 Act에서 튜닝. |
| **D6** | 뉴스 무영향 | 뉴스 article엔 `top_comments` 없음 → score() 확장이 빈 동작. `location` 미지정 시 기본 "body". TextBlob/news 경로 무수정. |
| **D7** | 스키마 불변 | `top_comments`는 계속 `list[str]`. 수집량만 증가. `wsb_posts.json` 하위호환(과거 데이터=댓글 3개로 제한적 반영). |

---

## 5. 데이터 구조

### 5.1 post (collector 출력 — 스키마 불변, 수집량만 ↑)
```python
{
  "title": str, "body_excerpt": str,
  "top_comments": list[str],     # 일반 ≤100 / DD ≤1000 (품질필터 후)
  "flair": str, "source": "post",
  "source_quality_weight": float,
  ...
}
```

### 5.2 article_detail (score() 출력 — `location`/`source_quality_weight` 추가)
```python
{
  "title": str,
  "finbert_label": "positive"|"negative"|"neutral",
  "scores": {positive,negative,neutral},
  "included": bool,
  "location": "body"|"comment",          # 신규 — 본문="body", 댓글="comment"
  "source_quality_weight": float,        # 신규 — post에서 상속(댓글은 부모 글 가중 상속)
}
```

### 5.3 labeled_post (engine 내부 — compute_weighted_counts 입력)
```python
{"label": "bullish"|"bearish"|"neutral", "location": str, "source_quality_weight": float}
# finbert_label(positive/negative) → label(bullish/bearish) 매핑
```

---

## 6. Config 상수 (M1)

```python
# 댓글 수집 규모 (comment-aware-sentiment)
COMMENT_COLLECT_NORMAL = 100         # 일반 글 댓글 상위 N
COMMENT_COLLECT_DD = 1000            # DD형 글 댓글 상위 N
DD_FLAIRS = {"DD", "Discussion"}     # DD형 판별 flair 집합 (소문자 비교)

# 비용 가드 (NFR-02 — 6-06 429 실측 근거, 필수)
COMMENT_REPLACE_MORE_LIMIT = 4       # replace_more 확장 상한 (0=이미 로드분만)
COMMENT_COLLECT_TIMEOUT_SEC = 20     # 글당 댓글 수집 타임아웃
COMMENT_MAX_DD_POSTS_PER_SUB = 10    # 서브레딧당 DD형 대량수집 글 수 상한

# 품질 필터 (FR-08)
COMMENT_MIN_LEN = 15                 # 최소 글자수
COMMENT_TEXT_MAX = 200               # FinBERT 입력용 댓글 최대 길이
COMMENT_BOT_AUTHORS = {"AutoModerator", "VisualMod"}

# 댓글 방향 가중 = 기존 상수 재사용 (신규 생성 안 함)
#   COMMUNITY_COMMENT_MENTION_WEIGHT = 0.5  (config.py:295, 기존)
#   COMMUNITY_BODY_MENTION_WEIGHT    = 1.0  (config.py:294, 기존)
```

---

## 7. 변경 상세 & Open Issues

### 7.1 M2 Collector
- `_fetch_subreddit`: flair로 댓글 한도 결정 — `limit = COMMENT_COLLECT_DD if flair.lower() in {f.lower() for f in DD_FLAIRS} else COMMENT_COLLECT_NORMAL`.
- `replace_more(limit=COMMENT_REPLACE_MORE_LIMIT)` + 타임아웃·DD글수 상한.
- 품질 필터: `[deleted]`/`[removed]`/`len<COMMENT_MIN_LEN`/author∈BOT 제외, `[:COMMENT_TEXT_MAX]` 절단.

### 7.2 M3 Provider
- `score()`: 루프 진입 전 `articles`를 **확장** — 각 post → `[post]`(location="body") + `top_comments`별 `{title:"", body_excerpt: c, location:"comment", source_quality_weight: post.sqw}`.
- 기존 본문 분류 로직 재사용(이미 `body_excerpt` 읽음 `:162`). detail에 `location`/`source_quality_weight` 기록.
- 뉴스 article: `top_comments` 없음 → 확장 0건, location 기본 "body".

### 7.3 M4 Engine
- `_score_posts`: details에서 `labeled_posts`(label/location/sqw) 구성, `result[symbol]["labeled_posts"]`에 포함. counts는 details 기반(댓글 포함) 그대로.
- 호출처 전파: `reddit_backtester.py:290`·`community_live`의 `build_daily_snapshot(...)`에 `labeled_posts=scored[sym]["labeled_posts"]` 추가.

### 7.4 Open Issues (구현 중 확정)
- **Open-1 (D4)**: run_pipeline `_filter_consensus`(raw count)와 `build_daily_snapshot.is_consensus_buy`(weighted) 이중 게이트. 댓글 가중이 최종 매매 방향에 반영되려면 가중 스냅샷이 결정에 쓰이는지 community_live/agent_gate 경로에서 확인·정합.
- **Open-2**: `mentions`(=글 수) vs `n_valid`(=글+댓글). MIN_DAILY_MENTIONS는 글 수 기준 유지, ≥10 게이트는 댓글 포함 — 의도와 일치 확인.
- **Open-3**: 과거 `wsb_posts.json`은 댓글 3개만 저장 → 과거 백테스트는 제한적 반영(완전 재현 아님). 신규 수집분부터 100/1000.

---

## 8. Test Plan (M5)

| TC | 시나리오 | 검증 |
|----|----------|------|
| TC-01 | mock submission flair="DD" | 댓글 ≤1000 수집, 일반 flair는 ≤100 |
| TC-02 | 품질 필터 | `[deleted]`/봇/단문 제외 |
| TC-03 | `score()` 확장 | 본문1+댓글N → detail N+1건, 댓글 detail location="comment" |
| TC-04 | 가중 카운트 | 본문(1.0)+댓글(0.5) → compute_weighted_counts 반영, raw N=글+댓글 |
| TC-05 | N 게이트 | 댓글 포함 n_valid≥10 → 폴백 미발생 |
| TC-06 | 공용 경로 | backtester·community_live가 동일 labeled_posts 전파 |
| TC-07 | 뉴스 무영향 | top_comments 없는 article → 확장 0, 기존 동작 동일 |
| TC-08 | 비용 가드 | replace_more/타임아웃/DD글수 상한 작동 |
| TC-09 | 회귀 | 기존 KIS 9 / community_live 10 테스트 통과 |

검증 실행: 각 테스트 파일 단독 실행(`python tests/test_*.py`) — pytest 미설치 환경.

---

## 9. Implementation Guide

### 9.1 구현 순서
1. **M1** config 상수 추가 (가중은 기존 재사용).
2. **M2** collector 댓글 수집 확대 + DD 판별 + 품질필터 + 가드.
3. **M3** provider score() 본문+댓글 확장 분류 + location/sqw.
4. **M4** engine `_score_posts` labeled_posts 구성 + 호출처 전파.
5. **M5** 테스트 작성 + 전체 회귀 + 실수집 N 증가 확인.

### 9.2 코드 주석 규약
- 파일/모듈: `# Design Ref: §{n} — {결정}`
- 핵심 로직: `# Plan SC: {SC-id}`

### 11.3 Session Guide (--scope)

| 세션 | scope | 모듈 | 선행 |
|------|-------|------|------|
| S1 | `module-1` | M1 config | — |
| S2 | `module-2` | M2 collector | M1 |
| S3 | `module-3,module-4` | M3 provider + M4 engine (강결합 — 함께) | M1 |
| S4 | `module-5` | M5 tests + 회귀 | M2~M4 |

권장: `/pdca do comment-aware-sentiment --scope module-1,module-2` (수집), 이후 `--scope module-3,module-4` (분석/가중), 마지막 `--scope module-5`.

---

## 10. 영향 범위 요약

- **수정 4파일**: config / reddit_collector / sentiment_provider / wsb_signal_engine (+ 호출처 2: community_live, reddit_backtester 스냅샷 인자)
- **신규 1파일**: tests/test_comment_aware_sentiment.py
- **불가침**: signals.py(뉴스), kis_broker, trader, backtester(news)
- **예상 변경량**: ~150–220 LOC
