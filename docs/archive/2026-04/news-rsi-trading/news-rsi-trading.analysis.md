# Analysis: News-RSI Stock Trading System

**Feature**: news-rsi-trading
**Date**: 2026-04-01
**Status**: Check — Completed

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 뉴스 감성 + RSI 결합 전략의 실효성을 실제 시장 조건에서 검증 |
| **WHO** | 알고리즘 트레이딩 연구자/개발자 (개인 사용) |
| **RISK** | NewsAPI 무료 티어 한도, Polygon.io 속도 제한, 스케줄러 프로세스 중단 |
| **SUCCESS** | 정규장 타이밍에 맞춰 신호 자동 생성 + 가상 포트폴리오 실시간 추적 |
| **SCOPE** | 미국 주식(US), Python, 페이퍼 트레이딩 (실제 주문 없음) |

---

## 1. 분석 요약

| 항목 | 결과 |
|------|------|
| 분석 날짜 | 2026-04-01 |
| 분석 방법 | Static Analysis (Structural + Functional + Contract) |
| **최종 Match Rate** | **100%** (수정 후) |
| 발견된 이슈 | Critical 1건 (수정 완료), Minor 1건 (수정 완료) |

---

## 2. Match Rate

### 2.1 수정 전

| 축 | 가중치 | 점수 | 비고 |
|----|--------|------|------|
| Structural | 0.2 | 100% | 11/11 파일 전체 존재 |
| Functional | 0.4 | 91% | BUY RSI 상한값 오류 1건 |
| Contract | 0.4 | 100% | 데이터 스키마 완전 일치 |
| **Overall** | — | **96.4%** | — |

### 2.2 수정 후 (Final)

| 축 | 가중치 | 점수 | 비고 |
|----|--------|------|------|
| Structural | 0.2 | 100% | 전체 파일 존재 |
| Functional | 0.4 | 100% | Critical 버그 수정 완료 |
| Contract | 0.4 | 100% | 데이터 스키마 완전 일치 |
| **Overall** | — | **100%** | — |

---

## 3. 발견된 이슈

### 3.1 [Critical - 수정 완료] BUY 신호 RSI 상한값 오류

| 항목 | 내용 |
|------|------|
| **파일** | `signals.py:39` |
| **설계 명세** | BUY 조건: `30 ≤ RSI < 50` (Plan FR-03) |
| **버그 코드** | `config.RSI_NEUTRAL_LOW + 20` = 40 + 20 = 60 |
| **영향** | RSI 50~60 구간에서 잘못된 BUY 신호 발생 (Neutral 구간 침범) |
| **수정 코드** | `config.RSI_OVERBOUGHT - 20` = 70 - 20 = 50 ✅ |

```python
# Before (wrong)
config.RSI_OVERSOLD <= rsi < config.RSI_NEUTRAL_LOW + 20   # 30 ~ 60

# After (correct)
config.RSI_OVERSOLD <= rsi < config.RSI_OVERBOUGHT - 20    # 30 ~ 50
```

### 3.2 [Minor - 수정 완료] 주석 불일치

| 항목 | 내용 |
|------|------|
| **파일** | `trader.py:165` |
| **내용** | 주석에 "10%"라고 명시되어 있으나 `POSITION_SIZE_PCT = 0.20` (20%)로 변경됨 |
| **수정** | 하드코딩된 비율 텍스트 제거, config 참조로 설명 변경 |

---

## 4. 성공 기준 (Success Criteria) 최종 상태

| SC | 기준 | 상태 | 근거 |
|----|------|------|------|
| SC-01 | 매 거래일 신호 자동 생성 | ✅ Met | `scheduler.py:signal_calculation_job` |
| SC-02 | 정규장 타이밍 포트폴리오 업데이트 | ✅ Met | `scheduler.py:order_processing_job` |
| SC-03 | 14일 보유 기간 조정 로직 | ✅ Met | `trader.py:_process_sell_signal` (L124~129) |
| SC-04 | 거래 이력 저장 + 손익 계산 | ✅ Met | `portfolio.py:record_trade, print_portfolio_report` |
| SC-05 | `.env` 변경으로 종목 즉시 교체 | ✅ Met | `config.py:SYMBOLS` |
| SC-06 | NYSE 휴장일 스케줄러 제외 | ✅ Met | `scheduler.py:is_trading_day` |

**성공 기준 달성률: 6/6 (100%)**

---

## 5. 설계 결정 이행 확인

| 설계 결정 | 이행 여부 | 근거 |
|-----------|-----------|------|
| Option C (실용적 모듈 분리) | ✅ | 7개 파일, 단방향 의존성 유지 |
| Wilder's EWM RSI | ✅ | `indicators.py:ewm(alpha=1/period)` |
| APScheduler BlockingScheduler | ✅ | `scheduler.py:BlockingScheduler` |
| Atomic write (portfolio.json) | ✅ | `portfolio.py:tempfile + os.replace` |
| 뉴스 없을 시 sentiment=50.0 | ✅ | `indicators.py:calculate_sentiment_score` |
| POSITION_SIZE_PCT = 0.20 | ✅ | `config.py` (사용자 변경 반영) |
| PLTR 종목 추가 | ✅ | `config.py:SYMBOLS, COMPANY_NAMES` |

---

## 6. 보안 참고사항

`.env.example`에 실제 API 키가 포함되어 있습니다. `.gitignore`에 `.env.example`을 추가하거나,
실제 키를 `.env` 파일로만 관리하고 `.env.example`은 빈 값으로 유지하는 것을 권장합니다.

---

## 7. 결론

구현이 설계 명세를 100% 충족합니다. Critical 버그 1건(BUY RSI 상한값)이 수정되었으며,
모든 성공 기준이 달성되었습니다. 리포트 단계로 진행 가능합니다.
