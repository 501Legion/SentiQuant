# Report: Comment-Aware Sentiment

**Feature**: comment-aware-sentiment
**완료일**: 2026-06-06
**최종 Match Rate**: 96% (Check) · iteration 0 · Act 튜닝 1건 반영
**아키텍처**: Option C — score() 댓글 확장 + 휴면 location 가중 인프라 활성화
**PDCA 문서**: [Plan](../../01-plan/features/comment-aware-sentiment.plan.md) · [Design](../../02-design/features/comment-aware-sentiment.design.md) · [Analysis](../../03-analysis/comment-aware-sentiment.analysis.md)

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | FinBERT-WSB가 댓글을 완전히 무시(제목+본문만 분류) + 일반 글 댓글 상위 3개만 수집. 종목당 글 1~6개 → `NEUTRAL_FILTER_MIN_ARTICLES=10` 항상 미달 → 컨센서스 미형성 → 매매 0(2026-06-06 실측: 입력 50, 통과 0). |
| **Solution** | 댓글을 본문 동급 개별 감성 데이터포인트(가중<1)로 집계. `score()` 단일 확장 지점에서 본문+댓글 개별 분류, 휴면 중이던 `location` 가중(본문 1.0/댓글 0.5)을 `labeled_posts` 전파로 활성화. 백테스트·라이브 공용 경로. |
| **Function UX Effect** | 종목당 유효 데이터포인트 N = 글+댓글로 증가 → `≥10` 충족률↑. 단일 DD 스레드도 댓글로 N을 채우고 매수 게이트 통과(임계 1로 튜닝). 뉴스 경로 무영향. |
| **Core Value** | "글 수가 적어 못 산다"는 구조적 한계 해소. 여론의 실제 무게중심(댓글)을 신호에 반영. |

### 1.3 Value Delivered (실제 결과)

| 관점 | 지표 | 결과 |
|------|------|------|
| **Problem 해소** | 댓글 감성 반영 | ✅ 본문+댓글 개별 라벨 분류 (이전: 댓글 0% 반영) |
| **Solution 충실도** | 가중 활성화 | ✅ 휴면 가중 켜짐 (본문 1.0 / 댓글 0.5), `compute_weighted_counts` 재사용 — 신규 엔진 0 |
| **데이터포인트 N** | 게이트 충족 메커니즘 | ✅ `n_valid` 댓글 포함 (TC-05), 단일 DD 매수 통과 (TC-08) |
| **품질/안정성** | 테스트 | ✅ 신규 8 + 전체 회귀 = **131 passed, 0 failed** |

---

## 2. 구현 요약

| 모듈 | 파일 | 변경 |
|------|------|------|
| M1 Config | `config.py:36-44` | 수집량(100/1000)·`DD_FLAIRS`·품질/비용 가드 상수 |
| M2 Collector | `reddit_collector.py:129-293` | flair DD 판별 + 댓글 대량수집 + 품질필터 + 비용가드(replace_more·timeout·DD글수 상한) |
| M3 Provider | `sentiment_provider.py` | `_expand_articles()` + `score()` 재작성 — 본문+댓글 개별 분류, detail에 `location`/`source_quality_weight` |
| M4 Engine | `wsb_signal_engine.py` | `_score_posts`가 `labeled_posts` 구성 + `run_pipeline signal_details` 전파 |
| M4 배선 | `reddit_backtester.py:291`, `agent_gate.py:88` | `build_daily_snapshot(labeled_posts=…)` → 가중 활성화 |
| M5 Tests | `tests/test_comment_aware_sentiment.py` | TC-03a/b·04·05·06a/b·07·08 (8건) |
| Act 튜닝 | `config.py:309` | `COMMUNITY_MIN_DAILY_MENTIONS` 3→1 (단일 DD 트리거) |

**변경 5파일 + 신규 1파일.** 설계 이탈 없음. 라이브 스냅샷 호출처가 `agent_gate.evaluate_candidate`로 이동한 점만 설계 문서와 미세 차이(구현이 더 정확).

---

## 3. Key Decisions & Outcomes

| 결정 | 출처 | 준수 | 결과 |
|------|------|:----:|------|
| D1 확장 지점 = `score()` | Design | ✅ | 단일 지점 수정으로 백테스트/라이브 자동 공용 — 회귀 0 |
| D2 가중 활성화 = `labeled_posts` 전파 | Design | ✅ | 휴면 가중 켜짐, 본문/댓글 차등 반영 (TC-04) |
| D3 N 게이트 댓글 포함 | Design | ✅ | `n_valid` 댓글 포함으로 폴백 회피 (TC-05) |
| D5 댓글 가중 0.5 재사용 | Design | ✅ | 신규 상수 0 |
| D6 뉴스 무영향 | Design | ✅ | `top_comments` 없는 article 확장 0 (TC-07) |
| D7 스키마 불변 | Design | ✅ | `top_comments: list[str]` 유지 |
| Open-2 → MIN_MENTIONS 튜닝 | Plan(Act 이연) | ✅ | 3→1, 단일 DD 매수 통과 (TC-08), 회귀 0 |

---

## 4. Success Criteria Final Status

| SC | 기준 | 상태 | 근거 |
|----|------|:----:|------|
| SC-01 | 일반 100 / DD 1000 수집 | ✅ (code) | `reddit_collector.py:217` |
| SC-02 | flair DD 판별 | ✅ | `_is_dd_flair():129-135` |
| SC-03 | 본문+댓글 개별 분류 | ✅ | TC-03a/b |
| SC-04 | 본문1.0·댓글0.5 가중, N=글+댓글 | ✅ | TC-04 |
| SC-05 | 품질 필터 | ✅ | `_is_quality_comment():138-148` |
| SC-06 | 비용 가드 | ✅ | replace_more·timeout·DD글수 상한 |
| SC-07 | 공용 경로, 뉴스 무영향 | ✅ | TC-06a/b, TC-07 |
| SC-08 | 댓글 포함 N↑ 실측(전/후) | ⏳ | **라이브 수집 1회로 확인 예정**(코드 준비 완료) |
| SC-09 | 신규 + 전체 테스트 통과 | ✅ | 131 passed, 0 failed |

**성공률: 8/9 충족** (SC-08은 코드 완비·실측만 잔여 — 라이브 데이터 필요).

---

## 5. 잔여 항목 (후속)

| # | 내용 | 처리 |
|---|------|------|
| G1 / SC-08 | 댓글 포함 N↑ 실측(전/후 비교) | 실제 Reddit 수집 1회 구동(NFR-04 변동 기록) |
| G3 / Open-3 | 과거 `wsb_posts.json` 댓글 3개만 보존 | 신규 수집분부터 100/1000(설계 수용) |
| 관찰 | `COMMENT_WEIGHT 0.5`·`MIN_MENTIONS 1` 실측 적정성 | 라이브 누적 후 재튜닝 검토 |

---

## 6. 결론

댓글을 개별 감성 데이터포인트로 집계하는 메커니즘(수집→분류→가중→게이트)이 백테스트·라이브 공용 경로에 단일 반영됐고, 단일 DD가 매수로 이어지는 종단 동작까지 확보했다. 전체 131개 테스트 무결, 설계 이탈 없음. 잔여는 라이브 데이터로만 확인 가능한 실측(SC-08) 1건으로, 실수집 1회 구동 시 닫힌다.

**다음**: `/pdca archive comment-aware-sentiment` (문서 아카이브) 또는 실수집 1회 → SC-08 최종 확인.
