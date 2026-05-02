# Plan: WSB Signal V3 — Reddit 매수/매도 신호 전면 개편

**Feature**: wsb-signal-v3
**Date**: 2026-04-22
**Status**: Plan

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 기존 WSBSignalEngine은 30MA 진입 필터로 모멘텀 종목을 과도하게 차단하고, 컨센서스 반전(bearish>bullish×1.5)만으로 청산 판단해 감성 약화·RSI 과매수·Gap Down 등 선행 신호를 놓친다. 매수 스코어 기준(BUY>50%)도 너무 낮아 노이즈 신호가 많다 |
| **Solution** | 30MA 삭제, 중립 비율 필터 추가, 매수 기준 상향(BUY>55), Mention Velocity 보정 도입, 5단계 우선순위 청산 조건으로 교체. Market Filter는 뉴스 모델과 동일하게 재사용 |
| **Function UX Effect** | signals.json에 velocity/velocity_state 추가, 청산 reason이 stop_loss/trailing_stop/sentiment_reversal/rsi_overbought/gap_down으로 세분화. 로그에 Velocity 보정 명시 |
| **Core Value** | 30MA 없이도 펌핑 필터(Velocity+중립비율)로 품질 유지하면서, 선행 청산 신호(감성 역전·RSI 과매수)로 하락 전 선제 대응 |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 30MA가 빠른 모멘텀 종목 진입을 막고, 청산이 Stop-Loss 이후에야 발동되어 손실이 커짐. Velocity 보정으로 관심 식은 종목 자동 필터링 |
| **WHO** | Reddit 기반 페이퍼 트레이딩 시스템 운영자 |
| **RISK** | Mention 이력 없는 신규 종목의 Velocity 계산 불가 / 감성 역전 2일 연속 조건의 데이터 지속성 필요 / ATR 도입 시 Polygon API 추가 호출 |
| **SUCCESS** | 30MA 필터 제거 후 진입 종목 수 증가 확인 / 감성 역전 청산 reason 로그 출력 / velocity_state 필드 signals.json 저장 |
| **SCOPE** | `wsb_signal_engine.py` 주요 재작성, `config.py` 상수 추가, `data/mention_history.json` + `data/position_scores.json` 신규, `reddit_portfolio.py` 청산 로직 연동 |

---

## 1. 기능 요구사항

### FR-01~03: 감성 점수 공식 및 중립 필터 (유지 + 신규)

| ID | 요구사항 |
|----|----------|
| FR-01 | 점수 공식은 Signal V2와 동일: `score = positive / (positive + negative) × 100` — **변경 없음** |
| FR-02 | 총 게시글 대비 중립 비율 > 70%이면 해당 종목 신호를 NEUTRAL로 강제 설정 (`neutral / total > 0.7`) |
| FR-03 | FR-02 적용 시 로그: `[중립 필터] {symbol}: 중립비율={ratio:.0%} → NEUTRAL` |

**기존 vs 신규 중립 필터 비교:**

| 구분 | 기존 (Signal V2) | 신규 (V3) |
|------|-----------------|-----------|
| 단위 | 기사별 FinBERT neutral 확률 ≥ 80% → 해당 기사 제외 | 종목별 neutral/total > 70% → 종목 신호 NEUTRAL |
| 목적 | 기사 노이즈 제거 | 전체 감성 신뢰도 저하 시 신호 억제 |
| 공존 | ✅ 두 필터 모두 적용 (FR-01 → FR-02 순) |

---

### FR-04~06: 30MA 필터 제거

| ID | 요구사항 |
|----|----------|
| FR-04 | `WSBSignalEngine._filter_ma30()` 메서드 삭제 |
| FR-05 | `run_pipeline()`에서 `_filter_ma30()` 호출 제거 |
| FR-06 | `config.py`의 `MA_ENTRY_PERIOD`, `MA_BREAKDOWN_GRACE_DAYS` 는 유지 (청산 로직에서 미사용이나 호환성 보존) |

---

### FR-07~09: 매수 신호 결정 (기준 강화)

| ID | 요구사항 |
|----|----------|
| FR-07 | `STRONG_BUY`: score > 70 AND rsi < 30 |
| FR-08 | `BUY`: score > 55 AND 30 ≤ rsi < 50 |
| FR-09 | 그 외 모두 `NEUTRAL` (SELL/STRONG_SELL 제외 — Reddit 모델은 매도 신호 생성하지 않음) |

**기존 vs 신규 매수 기준:**

| 신호 | 기존 (bullish ratio 기반) | 신규 (score + RSI) |
|------|--------------------------|-------------------|
| STRONG_BUY | score > 70 AND rsi < 30 | 동일 |
| BUY | score > **50** AND 30 ≤ rsi < 50 | score > **55** AND 동일 RSI |
| NEUTRAL | 그 외 | 그 외 (더 엄격) |

---

### FR-10~14: Mention Velocity 신호 보정

| ID | 요구사항 |
|----|----------|
| FR-10 | `data/mention_history.json`에 종목별 7일 멘션 수 이력 저장 (`{"NVDA": [10, 8, 12, 9, 11, 10, 9]}`) |
| FR-11 | velocity = today_mentions / 7일 평균. 이력 없으면 NEW 케이스로 분기 |
| FR-12 | velocity > 2.0 → score 임계값 -5 완화 (모멘텀 강함): STRONG_BUY 기준 65, BUY 기준 50 |
| FR-13 | velocity < 0.5 → score 임계값 +5 강화 (관심 식음): STRONG_BUY 기준 75, BUY 기준 60 |
| FR-14 | 신규 종목 (이력 없음, 오늘 첫 등장): today_mentions ≥ 20 → `NEW_SPIKE` 케이스. today_mentions < 20 → 무시 (NEUTRAL) |

**Velocity 보정 매트릭스:**

| velocity_state | 조건 | STRONG_BUY 기준 | BUY 기준 |
|---------------|------|----------------|---------|
| HIGH_MOMENTUM | velocity > 2.0 | score > **65** | score > **50** |
| NORMAL | 0.5 ≤ velocity ≤ 2.0 | score > **70** | score > **55** |
| DECLINING | velocity < 0.5 | score > **75** | score > **60** |
| NEW_SPIKE | 신규 + mentions ≥ 20 | score > **65** | score > **50** AND 30 ≤ rsi < 50 |
| NEW_IGNORE | 신규 + mentions < 20 | NEUTRAL | NEUTRAL |

> **NEW_SPIKE BUY 근거**: 신규 종목 급등 시 STRONG_BUY 뿐 아니라 BUY 진입 기회도 허용. score > 50은 HIGH_MOMENTUM BUY와 동일 기준 적용 (펌핑 필터는 mentions ≥ 20 조건이 담당).

**결과 저장:** signals.json에 `velocity`, `velocity_state` 필드 추가

---

### FR-15: Market Filter 연동 (뉴스 모델과 동일)

| ID | 요구사항 |
|----|----------|
| FR-15 | 기존 `market_filter.apply_market_filter()` 재사용 — Reddit 신호 생성 후 동일하게 적용 |

```
QQQ RSI > 70 → BUY/STRONG_BUY → NEUTRAL
QQQ RSI < 30 → BUY/STRONG_BUY → NEUTRAL
```

---

### FR-16~20: 매도 조건 전면 개편 (우선순위 순)

| ID | 요구사항 | 유형 |
|----|----------|------|
| FR-16 | **감성 역전**: 오늘 score < entry_score × 0.6 이고 어제도 동일 조건 (2일 연속) → 청산 (`sentiment_reversal`) | 선행 |
| FR-17 | **RSI 과매수 (기본)**: 종목 RSI > 70 AND velocity_state ≠ HIGH_MOMENTUM → 즉시 청산 (`rsi_overbought`) | 선행 |
| FR-17a | **RSI 과매수 (유예)**: 종목 RSI > 70 AND velocity_state == HIGH_MOMENTUM → 당일 청산 보류, 다음 날 RSI 재확인 (`rsi_hold`) | 선행 (유예) |
| FR-18 | **Gap Down**: 시초가(open) < 전일 종가(prev_close) × 0.95 → 당일 청산 (`gap_down`) | 당일 |
| FR-19 | **Stop-Loss**: 현재가 < entry_price × (1 + STOP_LOSS_PCT/100) → 청산 (`stop_loss`) | 후행 |
| FR-20 | **Trailing Stop**: 최고점 대비 현재가 하락 ≥ |TRAILING_STOP_PCT| % AND 현재 수익 > 0% → 청산 (`trailing_stop`) | 후행 |

**우선순위 로직 (check_exit 실행 순서):**
```python
1. 감성 역전 (2일 연속) → True 이면 즉시 반환

2. RSI 과매수 × Velocity 교차 확인:
   if rsi > 70 and velocity_state == "HIGH_MOMENTUM":
       return False, "rsi_hold"   # 청산 유예 — 다음 날 재확인
   elif rsi > 70:
       return True, "rsi_overbought"

3. Gap Down (open < prev_close × 0.95)
4. Stop-Loss
5. Trailing Stop
```

> **FR-17a 유예 설계 근거**: WSB 모멘텀 종목은 RSI 70 돌파 후에도 추가 상승하는 경향이 있음. HIGH_MOMENTUM(velocity > 2.0)이면 시장 관심이 집중된 상태이므로 1일 유예 후 RSI 재확인. 단, 유예는 **1회만** 허용 — `position_scores.json`에 `rsi_held: true` 플래그 저장, 다음 날 HIGH_MOMENTUM 여부 무관하게 RSI > 70이면 청산.

**감성 역전 데이터 지속성:**
- `data/position_scores.json` 스키마:
```json
{
  "NVDA": {
    "entry_score": 72.0,
    "yesterday_below": true,
    "rsi_held": false
  }
}
```
- `yesterday_below`: 전날 score < entry_score × 0.6 여부 (감성 역전 2일 연속 판단용)
- `rsi_held`: RSI 과매수 유예 사용 여부 (true이면 다음 날 velocity 무관하게 RSI > 70 즉시 청산)
- 매일 신호 계산 후 position_scores 업데이트

---

### FR-21 (선택): ATR 기반 동적 Stop-Loss

| ID | 요구사항 |
|----|----------|
| FR-21 | [선택 구현] ATR(14) 계산 후 stop_loss_pct = max(-7%, -2.5 × ATR%). 단, 이번 구현에서는 FR-19 고정 -7% 유지. ATR 연동은 다음 피처로 분리 |

> **이번 구현 제외**: ATR 계산은 `indicators.py`에 `calculate_atr()` 함수만 추가하고, Stop-Loss 연동은 `wsb-atr-stop` 피처로 분리한다.

---

## 2. 변경 대상 파일

| 파일 | 유형 | 주요 변경 |
|------|------|-----------|
| `wsb_signal_engine.py` | 수정 | `_filter_ma30()` 삭제, `_determine_signal_v3()` 신규, `_apply_neutral_filter()` 신규, `_apply_velocity()` 신규, `check_exit()` 전면 재작성 |
| `config.py` | 수정 | Velocity 임계값, 신호 기준 상수, Gap Down 임계값, 감성 역전 비율 추가 |
| `indicators.py` | 수정 | `calculate_atr()` 추가 (미연동, 준비만) |
| `data/mention_history.json` | 신규 | 종목별 7일 멘션 이력 |
| `data/position_scores.json` | 신규 | 포지션별 entry_score + yesterday_below 플래그 |

**변경 없는 파일:** `signals.py`, `market_filter.py`, `collector.py`, `sentiment_provider.py`, `trader.py`, `portfolio.py`

---

## 3. 새 config 상수

```python
# --- WSB Signal V3: 매수 기준 ---
WSB_STRONG_BUY_SCORE = 70.0          # 기본 STRONG_BUY score 임계값
WSB_BUY_SCORE = 55.0                 # 기본 BUY score 임계값 (기존 50 → 55 강화)
WSB_NEUTRAL_RATIO_MAX = 0.70         # neutral/total 초과 시 NEUTRAL 강제

# --- WSB Signal V3: Mention Velocity ---
WSB_VELOCITY_LOOKBACK_DAYS = 7       # 7일 평균 멘션
WSB_VELOCITY_HIGH_THRESHOLD = 2.0   # HIGH_MOMENTUM 기준
WSB_VELOCITY_LOW_THRESHOLD = 0.5    # DECLINING 기준
WSB_VELOCITY_SCORE_ADJUST = 5.0     # 보정 점수 (±5)
WSB_NEW_SPIKE_MIN_MENTIONS = 20     # 신규 종목 NEW_SPIKE 최소 멘션
WSB_NEW_SPIKE_SCORE = 65.0          # NEW_SPIKE 적용 score 기준

# --- WSB Signal V3: 매도 조건 ---
WSB_SENTIMENT_REVERSAL_RATIO = 0.60  # entry_score × 0.6 미만 시 감성 역전
WSB_RSI_EXIT_OVERBOUGHT = 70.0       # 청산 RSI 과매수 기준
WSB_GAP_DOWN_PCT = -5.0              # Gap Down 임계값 (%)

# --- WSB Signal V3: RSI 과매수 유예 ---
WSB_RSI_HOLD_ONCE = True             # HIGH_MOMENTUM 시 RSI 과매수 1회 유예 허용

# --- 데이터 파일 ---
MENTION_HISTORY_FILE = "data/mention_history.json"
POSITION_SCORES_FILE = "data/position_scores.json"
```

---

## 4. signals.json 스키마 변경 (Reddit 모드)

```json
{
  "NVDA": {
    "score": 68.5,
    "rsi": 28.3,
    "velocity": 2.4,
    "velocity_state": "HIGH_MOMENTUM",
    "neutral_ratio": 0.32,
    "neutral_filtered": false,
    "market_rsi": 58.3,
    "market_filter_applied": false,
    "signal": "STRONG_BUY",
    "signal_original": "STRONG_BUY",
    "timestamp": "2026-04-22T16:30:00"
  }
}
```

---

## 5. check_exit() 청산 로직 비교

| 우선순위 | 기존 | 신규 |
|---------|------|------|
| 1 | Stop-Loss (-7%) | 감성 역전 (2일 연속 score < entry×0.6) |
| 2 | Trailing Stop (-5%, 수익 중) | RSI 과매수 (rsi > 70) |
| 3 | 컨센서스 반전 (bearish > bullish×1.5) | Gap Down (open < prev_close × 0.95) |
| 4 | 30MA 하향 돌파 (5일 유예) | Stop-Loss (-7%) |
| 5 | 수익 조건 (NEUTRAL + pnl > 1%) | Trailing Stop (-5%, 수익 중) |

---

## 6. 리스크

| 리스크 | 영향도 | 대응 |
|--------|--------|------|
| mention_history.json 없는 첫 실행 | 중 | 이력 없으면 NEW 케이스 분기, 멘션 < 20이면 NEUTRAL |
| position_scores.json 동기화 실패 | 중 | FileNotFoundError → yesterday_below=False 폴백 |
| 감성 역전 조건이 너무 민감할 수 있음 | 중 | 2일 연속 조건 + 0.6 비율로 완충, config으로 조정 가능 |
| RSI 과매수 청산이 추세 종목에서 조기 청산 | 낮음 | HIGH_MOMENTUM일 때 1회 유예(FR-17a). 유예 후에도 RSI > 70이면 청산 — 무제한 유예 방지 |
| RSI 유예 후 추가 손실 | 낮음 | rsi_held 플래그로 유예 1회 한정. 유예 허용 시 Stop-Loss(-7%)가 후방 방어선 역할 |
| ATR 미구현으로 고변동성 종목 Stop-Loss 부정확 | 낮음 | 다음 피처(wsb-atr-stop)로 분리 명시 |

---

## 7. 성공 기준

| SC | 기준 |
|----|------|
| SC-01 | 30MA 필터 없이 파이프라인 실행 완료 (기존 passed_ma 단계 제거 확인) |
| SC-02 | `neutral/total > 0.7` 조건 충족 종목에서 `[중립 필터]` 로그 출력 |
| SC-03 | BUY score 기준 55 적용 확인 — score 52 종목은 NEUTRAL 처리 |
| SC-04 | signals.json에 `velocity`, `velocity_state` 필드 존재 |
| SC-05 | 감성 역전 2일 연속 조건 충족 시 `sentiment_reversal` reason 청산 로그 |
| SC-06 | RSI > 70 AND HIGH_MOMENTUM → `rsi_hold` 로그 출력, 다음 날 청산 확인 |
| SC-07 | RSI > 70 AND HIGH_MOMENTUM + rsi_held=true (유예 소진) → `rsi_overbought` 청산 |
| SC-08 | RSI > 70 AND NOT HIGH_MOMENTUM → 즉시 `rsi_overbought` 청산 |
| SC-09 | Gap Down (-5%) 시뮬레이션 시 `gap_down` reason 청산 |
| SC-10 | NEW_SPIKE BUY 조건 (score > 50 + rsi 30~50) 충족 시 BUY 신호 생성 확인 |
| SC-11 | mention_history.json 일별 업데이트 확인 |

---

## 8. 구현 순서 (Module Map)

| Module | 파일 | 작업 | 예상 난이도 |
|--------|------|------|------------|
| M1 | `config.py` | 신규 상수 추가 (Velocity, 신호 기준, Gap Down, 파일 경로) | 낮음 |
| M2 | `wsb_signal_engine.py` | `_filter_ma30()` 삭제 + `_apply_neutral_filter()` 추가 | 낮음 |
| M3 | `wsb_signal_engine.py` | `_determine_signal_v3()` — 새 매수 기준 + Velocity 보정 | 중간 |
| M4 | `wsb_signal_engine.py` | `_update_mention_history()` — 7일 이력 저장/로드 | 낮음 |
| M5 | `wsb_signal_engine.py` | `check_exit()` 전면 재작성 — 5단계 우선순위 청산 | 중간 |
| M6 | `wsb_signal_engine.py` | `_update_position_scores()` — entry_score + yesterday_below 관리 | 낮음 |
| M7 | `indicators.py` | `calculate_atr()` 추가 (미연동, 준비만) | 낮음 |
| M8 | `reddit_portfolio.py` | check_exit() 연동 확인 + gap_down을 위한 open 가격 전달 | 중간 |

---

## 9. 미래 확장

| 확장 | 방법 |
|------|------|
| ATR 기반 동적 Stop-Loss | `wsb-atr-stop` 피처: `calculate_atr()` + WSB_ATR_MULTIPLIER config |
| Velocity 가중 포지션 사이징 | HIGH_MOMENTUM 시 position_size +2%, DECLINING 시 -2% |
| 감성 역전 1일 조건 | WSB_SENTIMENT_REVERSAL_DAYS = 1 로 config 조정 (긴급 청산 모드) |
