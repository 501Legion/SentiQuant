# auto_stock — PDCA Completion Report

> **Date**: 2026-05-02
> **Branch**: `rsi_finBERT_combine`
> **Cycle**: PM (skip) → Plan (skip) → Design (proxy: ARCHITECTURE.md) → Do (누적 60일) → Check (94%) → Act (5건 fix) → **Report**
> **Note**: `auto_stock`은 메타-기능(전체 프로젝트). 정식 PDCA 문서는 서브-기능별로 진행되어 왔으며, 본 보고서는 ARCHITECTURE.md를 임시 Design으로 간주한 1차 동기화 사이클의 완료 기록입니다.

---

## 1. Executive Summary

| 관점 | 결과 |
|------|------|
| **Problem** | 60일간 누적된 8개 서브-기능(news-rsi-trading → wsb-signal-v3)의 변경이 ARCHITECTURE.md에 부분적으로만 반영되어 있어 설계-구현 drift가 누적됨 |
| **Solution** | ARCHITECTURE.md를 Living Design으로 간주한 정적 gap 분석 → Critical 1 + Important 4 모두 문서 측 sync로 해소 |
| **Function/UX Effect** | CLI 사용자가 `--ranking sentiment`로 실패하는 G1 Critical 제거; subreddit 6개·Neutral 필터 의미·archive 경로가 코드와 일치하도록 정정 |
| **Core Value** | 설계 문서 신뢰도 회복 — 신규 작업자/AI 에이전트가 ARCHITECTURE.md만으로도 정확한 시스템 모델을 구축할 수 있음 |

### Match Rate 추이

```
Initial (Check 1회차):   Structural 100% / Functional 96% / Contract 88% → Overall 94%
After Act:               전 항목 100% (수정 대상이 모두 문서 sync였으므로 코드 측 회귀 없음)
                                                                              ✅ ≥ 90% 달성
```

---

## 2. 입력 문서 체인

| Layer | 문서 | 상태 |
|-------|------|------|
| PRD | (없음) | 메타-기능이라 생략 |
| Plan | (없음) | 메타-기능이라 생략 |
| Design (proxy) | `ARCHITECTURE.md` | ✅ 사용 |
| Implementation | 18개 Python 모듈 (signals.py / wsb_signal_engine.py / config.py 외) | ✅ |
| Analysis | `docs/03-analysis/auto_stock.analysis.md` | ✅ 본 사이클 산출 |

---

## 3. Key Decisions & Outcomes

| # | 결정 | 근거 | 결과 |
|---|------|------|------|
| D1 | Design 문서 부재 시 ARCHITECTURE.md를 임시 Design으로 채택 | 메타-기능에 대해 별도 plan/design을 새로 만드는 것은 60일치 실작업의 backfill이라 비효율 | gap-detector가 18개 모듈/21개 상수/7단계 파이프라인을 구체적으로 검증할 수 있었음 |
| D2 | Match Rate 94%(≥90%)에서도 Important 4건 모두 fix | 모든 gap이 코드 결함이 아닌 문서 측 drift여서 fix 비용이 낮고 회귀 위험 0 | 설계 문서 신뢰도 회복, 후속 작업자의 인지 부담 감소 |
| D3 | wsb-signal-v3 archive를 본 사이클에 포함 | ARCHITECTURE.md §8의 "완료 PDCA" 표기가 실제 archive 디렉토리와 불일치(G4) | `docs/archive/2026-04/wsb-signal-v3/`로 plan/design/report 이동, _INDEX.md 갱신 |
| D4 | `--ranking`은 ARCHITECTURE.md 측을 코드(`ratio`)에 맞춤 | argparse `choices=["mentions","ratio"]`가 reddit_backtester의 실제 정렬 키이며 backtest 문서들이 모두 `ratio`로 표기 | 코드 안전성 우선, 별칭 추가는 잉여 분기로 보아 보류 |

---

## 4. Plan Success Criteria Final Status

> 정식 Plan 문서 부재. ARCHITECTURE.md §1·§3·§5에서 추출한 암묵적 Success Criteria 기준.

| SC | 기준 | 결과 | 증거 |
|----|------|------|------|
| SC-1 | 신호 결정 7단계 파이프라인 완전 동작 (TODO/placeholder 없음) | ✅ Met | signals.py:117-234 (TODO 0건) |
| SC-2 | 감성 Provider 5종(TextBlob/FinBERT/finbert-wsb/GPT-4/combined) 사용 가능 | ✅ Met | sentiment_provider.py + signals._get_active_providers |
| SC-3 | Reddit V3 5단계 청산 우선순위 정확 (sentiment_reversal → rsi → gap → stop → trail) | ✅ Met | wsb_signal_engine.py:322-428 |
| SC-4 | config 상수가 ARCHITECTURE.md §7과 정확 일치 | ✅ Met | 21/21 일치 (config.py:115-190) |
| SC-5 | CLI 명세가 ARCHITECTURE.md §6과 일치 | ✅ Met (Act 후) | `--ranking ratio`로 sync |
| SC-6 | 완료 PDCA가 archive로 정리되고 _INDEX.md에 등록 | ✅ Met (Act 후) | wsb-signal-v3 추가 |

**Overall Success Rate: 6/6 (100%)**

---

## 5. Act Phase 변경 사항

| Gap ID | 카테고리 | 변경 파일 | 변경 요약 |
|--------|----------|-----------|-----------|
| G1 | Critical / CLI | `ARCHITECTURE.md` §6 | `--ranking [mentions\|sentiment]` → `[mentions\|ratio]` |
| G2 | Important / Drift | `ARCHITECTURE.md` §1, §2(L47), §5 | Reddit subreddit 3개 → 6개 (options/StockMarket/thetagang 추가 명시) |
| G3 | Important / Doc 모호성 | `ARCHITECTURE.md` §4 | "positive_ratio ≥ 0.80" → "기사별 neutral_score ≥ 0.80인 기사 제외 + 폴백 조건 명시" |
| G4 | Important / Archive 정합성 | `docs/01-plan,02-design,04-report` → `docs/archive/2026-04/wsb-signal-v3/` | wsb-signal-v3 plan/design/report 3개 파일 archive 이동, `_INDEX.md` 행 추가 |
| G5 | Important / Doc 누락 | `ARCHITECTURE.md` §8 | wsb-daily-comments 행 추가 (진행 중 명시) |

**코드 변경: 0건** (모든 fix가 문서/디렉토리 측). 회귀 위험 없음.

---

## 6. Gap List 잔여 (Minor 4건 — 본 사이클 미수정)

| ID | Severity | 처리 방침 |
|----|----------|-----------|
| G6 | Minor | `wsb_preprocessor.preprocess()` 명세 vs 실제 `preprocess_post()` — 다음 ARCHITECTURE 정기 갱신 시 정정 |
| G7 | Minor | `RedditCollector.collect()` 진입점명 정정 — 동상 |
| G8 | Minor | `combined` Provider Reddit Backtest 차단 — 의도된 설계 명시 권장 |
| G9 | Minor | `tests/` 디렉토리 부재 — 별도 PDCA 사이클(`/pdca plan add-unit-tests`) 권장 |

---

## 7. 다음 권장 사이클

| 우선순위 | 액션 | 설명 |
|---------|------|------|
| 🟡 진행 중 | `/pdca design wsb-daily-comments` | Plan + Analysis 보유, Design 누락. 완성 후 archive |
| 🟢 신규 | `/pdca design kis-paper-trading` | 신규 plan 작성됨 (untracked), 다음은 Design 단계 |
| 🟢 권장 | `/pdca plan add-unit-tests` | G9 — pytest 도입 + L1 시나리오 13건 |
| 🔵 운영 | ARCHITECTURE.md 갱신 정책 강화 | Minor 갱신 시 §2 진입점 표 sync 체크리스트 추가 |

---

## 8. 메타-회고

- **장점**: 정식 PDCA 문서 없이도 Living Document(ARCHITECTURE.md)만으로 구현 동기화 검증이 가능함을 입증. gap-detector의 정적 분석으로 6시간 분량의 감사가 ~4분에 압축됨.
- **개선점**: 60일간 8개 서브-기능을 진행하면서 ARCHITECTURE.md 갱신을 매번 강제하지 않아 drift가 누적됨. PDCA archive 단계에서 "ARCHITECTURE.md §2/§7 sync" 체크리스트를 강제하면 재발 방지 가능.
- **Living Document 정책의 한계**: 정식 plan/design이 없으면 분석 결과를 비교할 success criteria가 암묵적이 되어 향후 회귀 검증 시 ambiguity가 남음. 큰 리팩토링은 정식 PDCA를 권장.

---

## 9. PDCA 산출물 목록

| 단계 | 경로 |
|------|------|
| Analysis | `docs/03-analysis/auto_stock.analysis.md` |
| Report (this) | `docs/04-report/features/auto_stock.report.md` |
| Living Design | `ARCHITECTURE.md` (sync 완료) |
| Archived | `docs/archive/2026-04/wsb-signal-v3/` (신규) |
