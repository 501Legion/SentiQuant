# Plan: Comment-Aware Sentiment — 댓글을 개별 감성 데이터포인트로 집계

**Feature**: comment-aware-sentiment
**작성일**: 2026-06-05 · **개정**: 2026-06-06 (Plan Plus 브레인스토밍 반영)
**상위 피처**: community-opinion-agent-live / community-opinion-agent (감성 신호 엔진)
**방식**: `/plan-plus` (Intent Discovery → Alternatives → YAGNI → 설계 방향 확정)

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | FinBERT-WSB가 **댓글을 완전히 무시**(제목+본문만 분류, `sentiment_provider.py:162-163` 검증). 일반 글 댓글은 상위 3개만 수집. 종목당 글이 1~6개뿐이라 `NEUTRAL_FILTER_MIN_ARTICLES=10` 항상 미달 → 컨센서스 미형성 → 매매 0(2026-06-06 실측: 입력 50, 컨센서스 통과 0). DD처럼 신호가 댓글에 있는 글의 방향성을 통째로 버림. |
| **Solution** | 댓글을 **본문과 동급의 개별 감성 데이터포인트**로 집계하되 **가중 < 1**. 수집량 확대(일반 100, DD형 1000) + 품질 필터 + FinBERT가 본문·댓글 각각을 분류해 bull/bear/neutral 카운트에 합산. **백테스트·라이브 공용 경로**(get_provider + WSBSignalEngine)에 단일 반영. |
| **Function UX Effect** | 종목당 유효 데이터포인트 N = 글 + 댓글로 크게 늘어 `≥10` 충족률↑, 댓글 토론의 실제 방향성이 신호에 반영 → 정상적인 날엔 컨센서스가 형성되어 매매 발생. 백테스트도 동일 로직으로 재평가. |
| **Core Value** | "글 수가 적어 못 산다"는 구조적 한계 해소. 여론의 실제 무게중심(댓글)을 신호에 반영해 전략 적중도·거래 빈도 개선. |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 라이브가 댓글을 버려서 데이터포인트 부족 → 컨센서스 0 → 매매 0. 댓글을 개별 집계(가중<1)해 N↑ + 신호 정확도↑. |
| **WHO** | 운영자(본인) — 더 자주, 더 근거 있는 매매 신호를 원함. |
| **RISK** | 댓글 1000개 수집 = Reddit API 비용·지연·rate limit↑(2026-06-06 Polygon/Reddit 429 실측). 백테스트 결과가 (의도적으로) 변동. mentions 인플레로 기존 임계 의미 변화. ※ 단일 DD가 방향을 주도하는 것은 **리스크가 아닌 의도된 동작**. |
| **SUCCESS** | 댓글이 개별 라벨로 가중(<1) 카운트 / 수집 100·1000 + 비용 가드 작동 / 품질 필터 / FinBERT 공용 경로 반영(백테스트+라이브) / 실제 N↑ 확인 / 테스트 통과. |
| **SCOPE** | 수정: `reddit_collector.py`(수집량·DD판별·품질필터), `sentiment_provider.py`(FinBERT 본문+댓글 개별 분류), `wsb_signal_engine.py`(댓글 가중 합산), `config.py`(상수). 신규: 테스트. **불가침**: `signals.py`(뉴스 판단). **스코프 제외**: 뉴스 모델/경로 — 고려 대상 아님. |

---

## User Intent Discovery (Plan Plus Phase 1)

| 항목 | 결정 |
|------|------|
| **핵심 문제** | 댓글 무시로 데이터포인트 부족 → 컨센서스/매매 0 (구조적). |
| **타깃 사용자** | 운영자(본인). |
| **성공 기준** | 댓글 개별·가중 카운트로 N↑ & 방향성 반영, 백테스트·라이브 공용. |
| **제약** | Reddit rate limit(429), 단일 DD 지배 방지, 뉴스는 스코프 밖. |

---

## Alternatives Explored (Plan Plus Phase 2)

### 결정 1 — 댓글 카운팅 반영 범위 / 회귀 처리

| 안 | 내용 | 채택 |
|----|------|:----:|
| 라이브 전용 분리 | collector가 라이브일 때만 댓글 emit, 백테스트 무변경 | |
| score() 플래그 | `include_comments` 분기 | |
| **공용 경로 통합** | **백테스트·라이브 모두 댓글 인지(공용 provider/engine). 뉴스는 스코프 제외** | ✅ |

> **근거**: 사용자 의도 = "백테스트·실주문 모두 반영, 뉴스 모델 미포함". `reddit_backtester.py:201-250`이 `community_live`와 동일한 `get_provider`+`WSBSignalEngine.run_pipeline`을 공유함을 검증 → **provider 스코어링 시점에서 댓글 확장**하면 한 곳 수정으로 양쪽 동시 반영. 격리/플래그 불필요(더 단순). 뉴스 기사는 `top_comments`가 없어 동일 코드에서 자연히 무영향.

### 결정 2 — 댓글 가중 (본문 thesis 앵커)

| 안 | 내용 | 채택 |
|----|------|:----:|
| **기존 가중 활용: 댓글 < 1** | 본문 1.0 / 댓글 `COMMENT_WEIGHT`(기본 0.3, **설계/실측 튜닝**)을 기존 `compute_weighted_counts`에 태움 | ✅ |
| 댓글 동등 1.0 | DD 스레드 전체(본문+댓글)가 동등하게 방향 결정 | |

> **근거**: 사용자 의도 = **DD(글+댓글 스레드)가 종목 방향을 주도하는 게 정상**. 따라서 가중은 "단일 DD 지배 방지"가 목적이 아니라, DD **본문(thesis)을 앵커**로 두고 노이즈 댓글이 본문을 압도하지 않게 하는 것. `wsb_signal_engine.py:85` `compute_weighted_counts(source_quality_weight, location)` 이미 존재 → 신규 로직 없이 가중만 부여. **`COMMENT_WEIGHT` 값은 plan에서 확정하지 않고 설계/실측에서 튜닝**(기본 0.3).

### 결정 3 — 수집 규모

| 안 | 내용 | 채택 |
|----|------|:----:|
| 보수적 30/200 | rate limit 안전 | |
| plan 원안 50/500 | | |
| **공격적 100/1000** | 최대 데이터포인트 | ✅ |

> **근거**: 사용자 선택. 단, 2026-06-06 429 실측 → **비용 가드(replace_more 한도·종목수 상한·타임아웃)가 필수 전제 조건**으로 격상.

---

## YAGNI Review (Plan Plus Phase 3)

**v1 포함** (사용자 선택):
1. 댓글 개별 FinBERT 분류 + 카운트 (provider/engine 공용 경로)
2. 댓글 가중 < 1 (`COMMENT_WEIGHT`, 기존 weighted_counts 재사용)
3. 수집 100/1000 + 비용 가드 (flair DD 판별, replace_more 한도, 종목수·타임아웃 상한)
4. 댓글 품질 필터 (삭제/봇/초단문 제외)

**Out of Scope** (deferred/removed):
- 글당 댓글 기여 상한 — 가중<1로 대체되어 불필요
- GPT/LLM provider 댓글 개별화 — 라이브는 FinBERT 경로, 우선순위 밖
- 배치 분류 성능 최적화(NFR) — 일일 잡 시간 초과 시에만 도입
- 뉴스 경로/모델 — 명시적 제외
- mentions 임계 자동 재튜닝 — Act 단계에서 실측 보고 결정

---

## 1. 현재 동작 (As-Is) — 변경 출발점

| 경로 | 댓글 수집 | 댓글 감성 반영 |
|------|----------|--------------|
| WSB Daily Thread | 댓글 각각을 별도 "글"로 수집 | ✅ 1댓글=1데이터포인트 |
| 일반 글(DD 등) | `top_comments` 상위 **3개**(`GPT_TOP_COMMENTS=3`), `replace_more(limit=0)` | — |
| **FinBERT(공용)** | — | ❌ `text=제목+본문`, `top_comments` **무시** (`:162-163`) |
| GPT provider | — | △ 상위 3 댓글 이어붙여 1텍스트→1라벨(개별 카운트 X, `:318`) |
| 카운트(`run_pipeline`) | — | 1글=1라벨, 가중은 `compute_weighted_counts`(location/weight) 존재 |

→ DD 글 1개(본문 중립) + 통찰 댓글 50개 = **1 중립**으로 처리됨.

---

## 2. 목표 동작 (To-Be)

> **핵심 모델 — "DD가 있을 때만, DD 방향으로 거래"**: 잡담 글 몇 개로는 N이 안 차서 매매하지 않고,
> **제대로 된 DD(글+댓글 스레드)가 뜬 종목**은 그 스레드의 감성 방향으로 간다. 댓글이 N을 채워 DD 종목을
> 후보로 진입시키는 게이트 역할을 하고, 단일 DD가 그 종목의 방향을 주도하는 것은 **의도된 동작**이다.
> `COMMENT_WEIGHT<1`은 지배 방지가 아니라 **DD 본문(thesis)을 앵커로 삼고 노이즈 댓글이 본문을
> 압도하지 않게** 하는 장치(값은 설계/실측 튜닝).

```
수집(collector): 일반 글 → 상위 100 댓글, DD형 글 → 상위 1000 댓글
                 + flair 기반 DD 판별 + 품질 필터 + 비용 가드(replace_more 한도/타임아웃/종목수)
분석(FinBERT score): [본문] + [댓글 각각]을 개별 텍스트로 분류, location/weight 부여
카운트(engine):  본문(weight 1.0) + 댓글(weight COMMENT_WEIGHT<1)을 compute_weighted_counts에 합산
N 게이트:        N = 글 수 + 댓글 수(각 1) → ≥10 = "충분한 DD 토론" 게이트 → 후보 진입
방향성:          bull/bear/neutral 비율·neutral_ratio는 가중(본문 1.0 / 댓글 <1) 기준
공용:            reddit_backtester + community_live 동일 경로 → 양쪽 동시 반영 (뉴스 무영향)
```

---

## 3. 기능 요구사항

### FR
| ID | 내용 |
|----|------|
| FR-01 | **수집 확대**: 일반 글 댓글 상위 **100개**(`COMMENT_COLLECT_NORMAL=100`), DD형 글은 상위 **1000개**(`COMMENT_COLLECT_DD=1000`). `replace_more`를 비용 한도 내 확장. |
| FR-02 | **DD 판별**: flair가 `DD_FLAIRS`(설정 가능 집합: DD/Discussion 등)이면 DD형 → 1000개, 그 외 100개. |
| FR-03 | **댓글 개별 분류**: FinBERT-WSB `score()`가 본문 + 각 댓글을 **개별 데이터포인트**로 분류(라벨 산출). 댓글은 `location="comment"`로 표식. |
| FR-04 | **가중 카운트**: bull/bear/neutral 카운트에 본문(weight 1.0)·댓글(weight `COMMENT_WEIGHT`<1)을 기존 `compute_weighted_counts`로 합산. `neutral_ratio`·합의비율은 가중 기준으로 재계산. raw `mentions`/N은 데이터포인트 수(글+댓글) 기준. |
| FR-05 | **N 게이트**: `NEUTRAL_FILTER_MIN_ARTICLES`(≥10)/컨센서스 판정의 N = 글+댓글 데이터포인트 수(각 1). 의미는 "충분한 DD 토론이 있는가" 게이트 — DD 스레드가 N을 채워 종목을 후보로 진입시킴(단일 DD 트리거는 의도). |
| FR-06 | **공용 경로 단일 반영**: `reddit_backtester`·`community_live`가 공유하는 `get_provider`+`WSBSignalEngine` 한 곳만 수정해 양쪽 동시 반영. 뉴스 경로(`signals.py`)·뉴스 모델 무수정·무영향. |
| FR-07 | **config 상수**: `COMMENT_COLLECT_NORMAL=100`, `COMMENT_COLLECT_DD=1000`, `DD_FLAIRS`(집합), `COMMENT_WEIGHT`(기본 0.3 — **설계/실측 튜닝**), 품질 필터·비용 가드 상수. |
| FR-08 | **품질 필터**: `[deleted]`/`[removed]`/봇/`COMMENT_MIN_LEN` 미만 댓글 제외. |

### NFR
| ID | 내용 |
|----|------|
| NFR-01 | **뉴스 경로 불변** — `signals.py` 등 뉴스 판단 로직 무수정. 뉴스 모델은 스코프 제외(고려 안 함). |
| NFR-02 | **수집 비용 가드(필수)** — DD 1000 댓글은 종목 수 상한·글 수 상한·`replace_more` 한도·전체 타임아웃으로 폭주/429 방지. (2026-06-06 rate limit 실측 근거) |
| NFR-03 | 댓글 분류 추가로 인한 처리시간 증가가 일일 구동(09:35 ET 잡) 한도 내. 초과 시 배치 분류 최적화 도입. |
| NFR-04 | 백테스트 결과는 **의도적으로 변동**(댓글 반영). 회귀가 아닌 개선으로 간주 — 변동 전/후를 기록·관찰. |

---

## 4. 변경/신규 파일

| 파일 | 구분 | 변경 |
|------|------|------|
| `reddit_collector.py` | 수정 | 일반 100·DD 1000 댓글 수집, flair 기반 DD 판별, 품질 필터, replace_more·타임아웃 가드 |
| `sentiment_provider.py` | 수정 | FinBERT `score()`가 본문+댓글을 개별 데이터포인트로 분류, `location`/weight 부여 |
| `wsb_signal_engine.py` | 수정 | 댓글(weight<1)을 `compute_weighted_counts`에 합산, neutral_ratio/합의비율 가중 재계산 |
| `config.py` | 수정 | `COMMENT_COLLECT_NORMAL/DD`, `DD_FLAIRS`, `COMMENT_WEIGHT`, 품질·비용 가드 상수 |
| `tests/test_comment_aware_sentiment.py` | 신규 | 댓글 개별 카운트, DD 1000 수집, 가중<1, 품질 필터, 비용 가드, 공용 경로 반영 |

---

## 5. 성공 기준

| SC | 기준 | 검증 |
|----|------|------|
| SC-01 | 일반 글 댓글 상위 100, DD형 글 상위 1000 수집 | 테스트(mock submission) |
| SC-02 | flair 기반 DD 판별 정확 | 테스트 |
| SC-03 | FinBERT가 본문+댓글 각각을 개별 라벨로 분류 | 테스트 |
| SC-04 | bull/bear/neutral에 본문(1.0)·댓글(`COMMENT_WEIGHT`<1) 가중 합산, N=글+댓글 | 테스트 |
| SC-05 | 댓글 품질 필터(삭제/봇/초단문) 작동 | 테스트 |
| SC-06 | 수집 비용 가드(종목수·replace_more·타임아웃) 작동 | 테스트/관찰 |
| SC-07 | 공용 경로 — 백테스트·라이브 동일 로직 반영, 뉴스 무영향 | 테스트 + 회귀 관찰 |
| SC-08 | 댓글 포함 시 종목 N이 `≥10` 충족률↑ (전/후 비교) | 실측 비교 |
| SC-09 | 신규 테스트 + 기존 전체(KIS 9 / community_live 10 등) 통과 | 단독 실행 러너 |

---

## 6. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| 1000 댓글 수집 비용·지연·429 | 높음 | NFR-02 비용 가드 필수(종목수·replace_more 한도·타임아웃) |
| 단일 DD가 방향 주도 | — | **의도된 동작**(리스크 아님). 본문 thesis 앵커는 `COMMENT_WEIGHT`<1로 유지 |
| 저품질/조작성 DD가 트리거 | 중간 | 품질 필터(FR-08) + flair/DD 판별 정확도 + (Act) 실측 모니터링 |
| 백테스트 결과 변동 | 중간(의도적) | NFR-04 변동 기록·관찰, 회귀 아님으로 간주 |
| mentions 인플레로 기존 임계 의미 변화 | 중간 | Act 단계 실측 후 `COMMUNITY_MIN_DAILY_MENTIONS` 등 재튜닝 검토 |
| 댓글 노이즈(밈·봇) | 낮음 | 품질 필터(FR-08) |

---

## 7. 구현 순서 (예정)

1. `config.py` 상수 추가(수집량 100/1000·`DD_FLAIRS`·`COMMENT_WEIGHT`·품질/비용 가드).
2. `reddit_collector.py`: flair DD 판별 + 일반 100·DD 1000 수집 + 품질 필터 + replace_more·타임아웃 가드.
3. `sentiment_provider.py`: FinBERT `score()` 본문+댓글 개별 분류 + location/weight.
4. `wsb_signal_engine.py`: 댓글 가중<1 합산, neutral_ratio/합의비율 재계산.
5. `tests/test_comment_aware_sentiment.py`.
6. 전체 테스트 + 실제 수집·구동으로 N 증가/공용 반영 확인 + 백테스트 변동 기록 + (필요시) 임계 재튜닝.

---

## 8. 가장 중요한 제약

- **공용 경로 단일 반영**: provider/engine 한 곳 수정으로 백테스트+라이브 동시 반영. **뉴스 경로/모델은 스코프 밖**(무수정·무영향) (FR-06, NFR-01).
- **비용 가드 필수**: DD 1000 댓글은 반드시 종목 수·시간 한도와 함께 (NFR-02, 429 실측 근거).
- **DD 주도 = 의도**: DD(글+댓글)가 종목 방향을 주도하는 게 정상. 댓글 가중<1은 지배 방지가 아니라 본문 thesis 앵커링·노이즈 저항용(값은 설계/실측 튜닝) (결정 2).
- 선행 분석 근거: `docs/04-report/community-opinion-agent-live.no-trade-diagnosis.md` + 2026-06-06 실측(입력 50, 컨센서스 통과 0, 글당 1~6건).

---

## Brainstorming Log (Plan Plus)

- **2026-06-06** Phase 0: 기존 plan 검증 — FinBERT 댓글 무시(`:162-163`), GPT 3댓글 1라벨(`:318`), engine 가중 메커니즘 기존 존재(`:85`), 백테스터 공용 경로 확인(`reddit_backtester.py:201-250`).
- 결정 1: **공용 경로 통합** (백테스트+라이브 동시 반영, 뉴스 제외) — 사용자 명시.
- 결정 2: **댓글 가중<1** (기존 weighted_counts 재사용) — 사용자 선택. 값은 **설계/실측 튜닝**(기본 0.3).
- 결정 3: **수집 100/1000(공격적)** + 비용 가드 필수 격상 — 사용자 선택 + 429 실측.
- **의도 명확화(2026-06-06)**: "DD만으로 방향성을 간다" — DD(글+댓글 스레드)가 종목 방향을 주도하는 게 **의도된 동작**. 단일 DD 트리거는 리스크 아님. `COMMENT_WEIGHT<1`의 목적이 "지배 방지"→"본문 thesis 앵커링·노이즈 저항"으로 재정의. N≥10은 "충분한 DD 토론" 게이트로 재해석.
- YAGNI: 4개 핵심 항목 v1 포함, 글당 상한·GPT 개별화·배치 최적화·뉴스·임계 자동튜닝 deferred.
