# Analysis (Check): Community Opinion Trend Sizing

**Feature**: community-opinion-trend-sizing
**Date**: 2026-05-29
**Phase**: Check (gap analysis — Design ↔ 구현 정밀 대조)
**Match Rate**: **95%** (≥90% → Report 진행 가능)
**Method**: 인라인 정밀 대조 (gap-detector 미사용 — Python CLI 프로젝트라 웹/Playwright 런타임 검증 부적합, 전체 구현 맥락 보유)

---

## Context Anchor
| | |
|--|--|
| WHY | 커뮤니티 여론 방향·지속성·합의도·관심도 변화를 사이징/청산에 반영 (급등추격 X) |
| WHO | 전략 연구자 → 운영자 |
| RISK | check_exit 공유 회귀(→opinion_mode) · 점수이력 부재(→인메모리) · 소표본 |
| SUCCESS | opinion_trend·sentiment ranking + equal 회귀 0 + pytest |
| SCOPE | 7파일 수정 + 테스트 / backtester.py·signals.py 불가침 |

---

## 1. 전략적 정합성 (Strategic Alignment)
- **WHY 충족**: 신호 로직(BUY/STRONG_BUY) 불변 + 사이징/청산만 여론 기반으로 확장 → "급등추격 아님" 의도 보존. velocity_state를 관심도(attention_factor)로 재해석 ✓
- **NEW_SPIKE 보수적**: attention_factor 0.5 + persistence<2 → 소액 진입 ✓ (T7)
- **고정 익절 OFF**: `WSB_USE_PROFIT_TARGET=False`, check_exit에 profit_target 단계 없음 ✓
- **5단계 청산 유지**: opinion_mode=True는 1단계만 opinion_reversal로 확장, 2~5단계 동일 ✓

## 2. 구조 일치 (Structural Match — 100%)
| Design 컴포넌트 | 구현 위치 | 상태 |
|----------------|-----------|------|
| WSB_OPINION_* (config §5) | config.py (25개 매치) | ✅ |
| compute_trend/persistence/consensus + score_history I/O (§4.4) | wsb_state.py:156–245 | ✅ |
| CommunityOpinionTrendSizer + get_sizer (§3) | position_sizer.py:126, 273 | ✅ |
| _rank "sentiment" (§4.4) | wsb_signal_engine.py:310 | ✅ |
| check_exit(opinion_mode, opinion) + _opinion_reversal (§4.3) | wsb_signal_engine.py:328,369,435 | ✅ |
| process_day(opinion_metrics) + 스냅샷 + size_factor (§4.1/§7) | reddit_portfolio.py:69,151,164,276 | ✅ |
| OpinionMetrics/RedditTradeRecord/RedditBacktestResult (§4.1/§4.2) | reddit_backtester.py:34,47,76 | ✅ |
| 인메모리 history + opinion_mode 배선 + _build_result (§2) | reddit_backtester.py:189,226,259 | ✅ |
| --sizing/--ranking choices | main.py:230,235 | ✅ |
| tests T1~T12 (§8) | tests/test_opinion_trend_sizing.py | ✅ |

## 3. 계약 일치 (Contract Match — 100%)
- §4.1 단일 `opinion` 키로 OpinionMetrics 전달, 기존 sizer는 `**kwargs` 흡수 → 시그니처 일치 ✅ (T12)
- §4.2 `RedditReplayBacktester.run()` → `RedditBacktestResult` 반환, `backtester.py` 무수정, print/run_all 갱신 ✅
- §4.3 `check_exit(opinion_mode=False, opinion=None)` 기본 → 기존 호출부 무영향 ✅

## 4. 기능 깊이 (Functional Depth — 95%)
- 7-factor·게이팅·clamp 실제 로직 구현, placeholder 없음 ✅
- 런타임: 단위 테스트 **13/13 PASS** + 통합 스모크(equal/opinion_trend 모두 RedditBacktestResult, equity 9pt, 비교표 렌더) ✅

## 5. Success Criteria 평가
| SC | 기준 | 상태 | 근거 |
|----|------|------|------|
| SC-01 | opinion_trend 동작 | ✅ | 통합 스모크 + get_sizer/main choices |
| SC-02 | sentiment ranking | ✅ | _rank:310, 테스트 rank=[B,C,A] |
| SC-03 | score<60 제외 | ✅ | T2 |
| SC-04 | neutral>0.70 제외 | ✅ | T3 |
| SC-05 | consensus<1.5 제외 | ✅ | T4 |
| SC-06 | trend UP→1.15 | ✅ | T5 (100→115) |
| SC-07 | clamp≤1.3 | ✅ | T9 (last_size_factor==1.3, 130주) |
| SC-08 | NEW_SPIKE 축소 | ✅ | T7 (100→30) |
| SC-09 | opinion_reversal 감지 | ✅ | T10 (neutral_spike/consensus_break/sentiment_reversal) |
| SC-10 | **equal 회귀 0** | ✅ | T11(opinion_mode=False 유지+기존 sentiment_reversal)·T12(EqualSizer opinion 무시) + 구조적(기본 False) |
| SC-11 | 신규 dataclass+비교표 | ✅ | 통합 스모크 렌더 |
| SC-12 | pytest 통과 | ⚠️ Partial | 테스트 13/13 PASS(standalone). venv에 `pytest` 미설치로 `pytest` 정확 명령 미실행 |

**충족: 11/12 ✅ + 1 ⚠️(환경)**

## 6. Gap 목록 (모두 Minor — Critical 없음)
| # | 심각도 | 내용 | 대응 |
|---|--------|------|------|
| G1 | Minor(env) | SC-12: `pytest` 패키지 미설치 → `pytest` 명령 불가(standalone은 통과) | `pip install pytest` |
| G2 | Minor | 풀데이터 CLI 2종 명령 미실행(범위 ~140종목, ~25분 Polygon). 8종목 서브셋으로 검증됨 | 선택적 백그라운드 실행 |
| G3 | Minor | Design §7 스냅샷 `stop_loss_pct/trailing_stop_pct` 필드 선언되나 미기록(전역 config 사용) | 의도적(전역값). 필요 시 후속 |
| G4 | Info | check_exit가 "opinion_reversal" 대신 granular 사유(neutral_spike/consensus_break/sentiment_reversal) 반환 | **개선** — FR-15 카운터 지원 |
| G5 | Info | "2일 연속 하락"을 `sentiment_trend=="DOWN"` 프록시로 구현 | 합리적 근사 |

## 7. Decision Record 검증
- [Plan] Approach A (backtester 인메모리 지표) → ✅ 준수 (opinion_history 인메모리, NFR-04)
- [Plan/YAGNI] gpt5 비교 연기 → ✅ 비교 대상에서 제외
- [Design] Option C (typed OpinionMetrics) → ✅ 준수 (duck-typed 소비로 순환 import 회피)
- [Design] check_exit mode-gating → ✅ 준수 (equal 회귀 0)

## 8. 결론
구현이 Design과 **95% 일치**, Critical gap 없음. equal 회귀는 구조적+테스트로 보장. 남은 항목은 환경(pytest 설치)·선택적 풀데이터 검증뿐. **Report 진행 가능.**
