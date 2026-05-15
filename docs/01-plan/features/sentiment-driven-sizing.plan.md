# Plan: Sentiment-Driven Sizing — 감성 점수를 매매 크기·청산 폭에 반영

**Feature**: sentiment-driven-sizing
**Date**: 2026-05-04
**Status**: Plan (plan-plus enhanced)
**Branch**: `rsi_finBERT_combine`
**Method**: `/plan-plus` (Intent Discovery → Alternatives → YAGNI → Incremental Validation)

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | `sentiment ∈ [0,100]`이 연속값임에도 signals.py:39의 단순 threshold(50/30/70)만 사용해서 매매 결정. TextBlob(53~58 좁은 분포)/GPT5(넓은 분포)가 거의 같은 BUY/SELL 신호를 만들고, 백테스트 수익률이 동일 — 모델 품질 차이가 PnL에 안 드러남 |
| **Solution** | `backtester.py`에 **Cash/Shares Model**을 도입해 sentiment를 (1) 포지션 크기 (cash × POSITION_SIZE_PCT × size_factor / price), (2) 익절/손절 임계값 두 차원에 직접 반영. `config.POSITION_SIZING="sentiment"` flag로 toggle하여 기존 `"equal"`과 A/B 비교. live trading(KIS 모의투자)으로 설정 직전사 가능 |
| **Function/UX Effect** | `python main.py --backtest --sizing sentiment --model textblob/finbert/gpt5` 실행 시 모델별로 (a) trade 횟수, (b) 가중 PnL, (c) STRONG_BUY 비율, (d) equity curve, (e) 청산 사유 분포가 명시적으로 다르게 출력. 한 번 실행에 모든 모델 비교표 자동 생성. `--sizing equal`은 회귀 0 보장 (자동 비교 스크립트로 검증) |
| **Core Value** | 감성 모델 신호 강도가 백테스트 결과로 검증되는 평가 인프라 + 검증된 설정을 KIS 모의투자로 직접 전사 가능. 향후 sentiment 모델 선택(FinBERT vs GPT5)의 정량 근거 + live trading으로의 1:1 전환 경로 확보 |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | TextBlob/GPT5가 같은 BUY를 만들면 백테스트가 모델 비교 도구로 작동 못 함. 운영자 알파 입장에서는 "어떤 모델·임계값을 KIS 모의투자에 적용할지"의 정량 근거가 없음 — 감성 점수의 의사결정 영향력이 통계적으로 식별 불가능한 상태 |
| **WHO** | 미국주식 페이퍼 트레이딩 시스템 운영자(본인). 1차 사용자 역할 = **운영자 알파** (검증된 설정을 live로 전사하는 게 목적). 2차 = 모델 비교 연구자 |
| **RISK** | (1) backtester 코어 재설계 (cash/shares 모델 도입) → 회귀 위험 ★중. 자동 비교 스크립트로 완화 (2) 종목 간 자본 배분 정책(공유 풀 + MAX_POSITIONS=10)이 기존 독립 PnL과 다른 의미론 → 결과 해석 변경 (3) 손절(stop_loss) 신규 도입으로 trade 패턴 자체가 바뀜 — equal mode에서는 비활성 유지 (4) sentiment 임계값(80/60) 매핑이 임의값 → sensitivity 분석은 별도 피처로 분리 |
| **SUCCESS** | (1) `--sizing sentiment`: 모델별 STRONG_BUY 비율 차이 ≥10%p (2) Σ size_factor 차이 ≥10% (3) `--sizing equal` 회귀 0 (자동 비교 스크립트 통과) (4) 모델 비교표 한 번 실행으로 textblob/finbert/gpt5 결과 나란히 표시 (5) Equity curve 출력으로 자본 곡선 시각 비교 가능 (6) 단위 테스트로 sentiment band 경계값(60/80) 동작 검증 |
| **SCOPE** | 신규: `tests/test_sentiment_sizing.py`, `scripts/regression_check.py`. 수정: `backtester.py`(cash model + sentiment 분기 + 출력), `config.py`(sentiment-derived 상수). **Out of Scope**: 실시간 신호(`signals.py`), Reddit 신호(`wsb_signal_engine.py`), 보유 기간(hold days) 차등, 진입 강도 차등, KIS 모의투자 실주문 적용, sensitivity sweep |

---

## 0. User Intent Discovery (plan-plus Phase 1)

> Plan-plus 5단계 중 1단계 결과. 단순 trading 개선이 아닌 운영자 알파 입장의 평가-전사 인프라가 본질.

### 0.1 핵심 문제 (Q1: combined)
- **평가 도구의 한계** + **매매 전략의 한계** 둘 다 — 단순 threshold가 점수 차이를 흡수해서 (a) 모델 비교가 무의미해지고 (b) 좋은 점수를 가진 신호도 동일 sizing을 받아 알파를 못 만듦. 1단계는 backtester부터, 2단계는 live trading(KIS).

### 0.2 1차 사용자 (Q2: 운영자 알파)
- 검증된 설정(임계값 + 가중치 + 모델 선택)을 KIS 모의투자에 그대로 옮기는 것이 목적
- → 출력은 "어느 모델·설정이 우월한가"의 정량 비교 + live transferable 단위(달러/주식수)로 표시
- 1차 운영자(본인 단일 사용자), 2차 가상 사용자(미래 자신: 모델 재선택 시 근거 자료)

### 0.3 검증된 사실 (사용자 사전 분석)
| 항목 | 위치 | 내용 |
|------|------|------|
| BUY 조건 | `signals.py:39` | `sentiment > 50 AND 30 ≤ rsi < 50 → BUY` |
| STRONG_BUY 조건 | `signals.py:25` | `sentiment > 70 AND rsi < 30 → STRONG_BUY` |
| 진입 로직 | `backtester.py:262` | `if position is None and signal in ("BUY", "STRONG_BUY"): 진입` |
| 청산 조건 (3중) | `backtester.py:240` | `pnl >= 1.0 OR days_held >= 14 OR signal in ("SELL", "STRONG_SELL")` |
| TextBlob 분포 | (운영자 검증) | 53~58 좁은 분포 → RSI 매수 구간이면 거의 항상 BUY |
| GPT5 분포 | (운영자 검증) | 넓게 흔들림. TSLA에서 5일 NEUTRAL 차이만, 모두 보유 중 구간이라 무시 |
| 결과 | (운영자 검증) | NVDA: TextBlob/GPT5 100% 동일. TSLA: exit 날짜까지 동일 |

---

## 1. Alternatives Explored (plan-plus Phase 2)

> 3가지 접근 비교 후 **B (Cash/Shares Model)** 선택. 운영자 알파 = live transferability 우선.

| 접근 | 핵심 메커니즘 | 구현 시간 | 회귀 위험 | live 전사 | 청산 차등 |
|------|------------|---------|---------|----------|----------|
| A: Weight Multiplier | `weighted_pnl = pnl_pct × size_factor` | 6h | 낮음 | 중 | ✅ |
| **B: Cash/Shares Model (선택)** | `shares = floor(cash × POS_PCT × size × / price)` | 15h+ | 중 | **높음** | ✅ |
| C: Shadow Analysis | 별도 분석 스크립트, backtester 무변경 | 3h | 0 | 낮음 | ❌ |

**B 선택 근거**:
- 운영자 알파 1차 사용자 입장에서 "검증된 설정 → live KIS 직접 전사" 경로가 가장 짧음
- live trading은 cash/shares 단위로 작동 (KIS broker도 동일) → backtester가 같은 단위로 시뮬해야 의미 있는 비교
- weighted_pnl 같은 추상지표를 거치지 않고 dollar PnL로 직관적 해석
- equity curve(자본 곡선) 출력이 자연스럽게 가능 → 모델 간 시각적 비교 강력

**B 단점 수용**:
- backtester 코어 재설계 → 자동 비교 스크립트(scripts/regression_check.py)로 회귀 검증 자동화
- 기존 `data/backtest_snapshots/` 포맷 변경 → equal mode에서는 동일 결과 보장

---

## 2. YAGNI Review (plan-plus Phase 3)

> 핵심 기능 외 4개 선택적 기능 모두 v1에 포함 — 운영자 알파 입장에서 인프라 두텁게 까는 게 효율적이라 판단.

### 필수 (cash model 채택으로 자동 포함)
- ✅ Cash/shares 추적
- ✅ sentiment → size_factor 매핑 (1.5/1.0/0.5)
- ✅ sentiment → profit_target 매핑 (2.5/1.0/0.5%)
- ✅ sentiment → stop_loss 매핑 (-3/-2/-1%)
- ✅ POSITION_SIZING flag toggle (equal/sentiment)
- ✅ 종목 간 자본 배분 정책 (공유 풀 + MAX_POSITIONS=10)
- ✅ TradeRecord 확장 (size_factor, exit_reason, entry_sentiment, shares, dollar_pnl)
- ✅ 모델별 신호/매매 통계 출력

### 선택적 → 사용자 선택으로 모두 v1 포함
- ✅ Equity curve 출력 (cash model이라 거의 free)
- ✅ 단위 테스트 (`tests/test_sentiment_sizing.py`)
- ✅ 회귀 자동 비교 스크립트 (`scripts/regression_check.py`)
- ✅ 모델 vs 모델 자동 비교 표

### 명시적 제외 (별도 피처로 분리)
- ❌ 진입 강도 차등(entry threshold) → `sentiment-driven-entry`
- ❌ 보유 기간 차등(hold days) → `sentiment-driven-hold`
- ❌ Sensitivity sweep (임계값 grid search) → `sizing-grid-search`
- ❌ live signals.py 적용 → `live-sentiment-sizing`
- ❌ Reddit/wsb_signal_engine 적용 → `wsb-sentiment-sizing`
- ❌ Trade-level JSON export → 필요 시점에 별도 추가

---

## 3. Incremental Design Validation (plan-plus Phase 4)

> Architecture / Components / Data Flow + Capital Allocation 모두 사용자 승인 완료.

### 3.1 Architecture Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                backtester._backtest_single_model()               │
│                                                                  │
│  1. signals_by_date    ← run_signal_engine(model, period)        │
│  2. sentiment_by_date  ← extract_sentiment(signals_by_date)      │
│  3. ohlcv_by_date      ← collect_prices()                        │
│  4. portfolio          ← Portfolio(cash=INITIAL_CASH)            │
│  5. equity_history     ← []                                      │
│                                                                  │
│  6. for date in trading_days:                                    │
│       a. for sym in held_positions:                              │
│            check exit (profit/stop/timeout/signal)               │
│            if exit: cash += shares × exit_price - commission     │
│       b. for sym in BUY/STRONG_BUY signals (if not held):        │
│            if len(positions) >= MAX_POSITIONS: skip              │
│            size_factor    ← calc_size_factor(sentiment, mode)    │
│            exit_thresh    ← calc_exit_thresh(sentiment, mode)    │
│            shares = floor(cash × POS_PCT × size_factor / price)  │
│            cash -= shares × entry_price + commission             │
│       c. equity_history.append(date, cash + holdings_value)      │
│                                                                  │
│  7. return BacktestResult(trades, equity_curve,                  │
│                           signal_stats, exit_reason_dist)        │
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 종목 간 자본 배분 정책 (Q4-2 결정)

**선택**: 공유 풀 + MAX_POSITIONS 제한 (live trading과 동일 동작)
- 모든 종목이 동일 cash pool에서 출금
- 동시 보유는 `config.MAX_POSITIONS=10` (이미 정의됨)
- 한도 도달 시 신규 BUY 신호 skip + 로그 출력
- **이유**: KIS 모의투자도 단일 계좌·공유 cash 구조 → 직접 전사 가능

### 3.3 Components / Modules

| 신규/수정 | 위치 | 책임 |
|---|---|---|
| `Portfolio` (dataclass) | backtester.py | `cash`, `positions: dict[symbol, Position]` |
| `Position` (dataclass) | backtester.py | `shares, entry_price, entry_date, entry_sentiment, profit_target, stop_loss, days_held` |
| `EquityPoint` (dataclass) | backtester.py | `(date, cash, holdings_value, total_equity)` |
| `_calc_size_factor(sentiment, mode)` | backtester.py | `1.5 / 1.0 / 0.5` 매핑. equal → 1.0 고정 |
| `_calc_exit_thresholds(sentiment, mode)` | backtester.py | `(profit_pct, stop_pct)`. equal → `(1.0, None)` |
| `_calc_shares(cash, price, size_factor, position_pct)` | backtester.py | `floor()` 정수 주식수 |
| `BacktestResult` 확장 | backtester.py | + `equity_curve`, `signal_stats`, `exit_reason_dist` |
| `format_model_comparison(results)` | backtester.py | 모델별 결과 나란히 표 |
| `tests/test_sentiment_sizing.py` | tests/ (신규) | 단위 테스트 |
| `scripts/regression_check.py` | scripts/ (신규) | equal mode 회귀 자동 비교 |

---

## 4. 기능 요구사항

### FR-01~04: Cash/Shares Model

| ID | 요구사항 |
|----|----------|
| FR-01 | `Portfolio` dataclass — `cash: float`, `positions: dict[str, Position]`. `INITIAL_CASH=$100,000`로 초기화 |
| FR-02 | `Position` dataclass — `shares, entry_price, entry_date, entry_sentiment, profit_target, stop_loss, days_held` |
| FR-03 | `_calc_shares(cash, price, size_factor, position_pct) -> int` — `floor(cash × position_pct × size_factor / price)`. 0 이하 반환 시 진입 skip |
| FR-04 | 매수 시 cash 차감 = `shares × entry_price + _calc_commission(shares × entry_price)`. 매도 시 cash 가산 = `shares × exit_price - _calc_commission(shares × exit_price)` |

### FR-05~07: Sizing 차등화

| ID | 요구사항 |
|----|----------|
| FR-05 | `config.py`에 sentiment → size_factor 매핑 상수 추가 (`SENTIMENT_SIZE_HIGH_THRESHOLD=80`, `SENTIMENT_SIZE_MID_THRESHOLD=60`, `SENTIMENT_SIZE_HIGH=1.5`, `SENTIMENT_SIZE_MID=1.0`, `SENTIMENT_SIZE_LOW=0.5`) |
| FR-06 | `_calc_size_factor(sentiment, mode) -> float`:<br>• mode="equal" → 항상 1.0<br>• mode="sentiment" → sentiment≥80=HIGH, 60≤s<80=MID, s<60=LOW |
| FR-07 | 진입 시점 sentiment 기록 — `Position.entry_sentiment`. 청산 시 동일 값으로 임계값 결정 |

### FR-08~10: Exit 차등화

| ID | 요구사항 |
|----|----------|
| FR-08 | `config.py`에 익절 임계값 상수 추가 (`PROFIT_TARGET_HIGH_PCT=2.5`, `PROFIT_TARGET_MID_PCT=1.0`, `PROFIT_TARGET_LOW_PCT=0.5`) |
| FR-09 | `config.py`에 손절 임계값 상수 추가 (`STOP_LOSS_HIGH_PCT=-3.0`, `STOP_LOSS_MID_PCT=-2.0`, `STOP_LOSS_LOW_PCT=-1.0`) |
| FR-10 | `_calc_exit_thresholds(entry_sentiment, mode) -> (profit_pct, stop_pct)`:<br>• mode="equal" → `(1.0, None)` (기존 동작, 손절 비활성)<br>• mode="sentiment" → sentiment band 별 (profit, stop) 튜플 |

### FR-11: Toggle Flag

| ID | 요구사항 |
|----|----------|
| FR-11 | `config.POSITION_SIZING` 옵션이 이미 정의 (`"equal"|"sentiment"|"volatility"`). backtester는 이 값을 mode로 받아 분기:<br>• `"equal"` (default) → 기존 동작 100% 보존 (size=1.0, profit=1.0%, stop 비활성, timeout 14일)<br>• `"sentiment"` → FR-05~10 활성화<br>• `"volatility"` → 본 Plan 범위 외 (NotImplementedError 또는 equal로 폴백) |

### FR-12: 자본 배분 (공유 풀 + MAX_POSITIONS)

| ID | 요구사항 |
|----|----------|
| FR-12 | 동시 보유 종목 수가 `config.MAX_POSITIONS=10`에 도달하면 신규 BUY 신호 skip + INFO 로그 출력. cash가 0 이하일 때도 skip |

### FR-13: TradeRecord 확장

| ID | 요구사항 |
|----|----------|
| FR-13 | `TradeRecord`에 신규 필드:<br>• `shares: int`<br>• `dollar_pnl: float` (= shares × (exit - entry) - commission)<br>• `size_factor: float`<br>• `entry_sentiment: float | None`<br>• `exit_reason: Literal["profit", "stop", "timeout", "signal"]` |

### FR-14: 출력 통계

| ID | 요구사항 |
|----|----------|
| FR-14 | `BacktestResult`에 신규 필드:<br>• `equity_curve: list[EquityPoint]`<br>• `signal_stats: dict` (BUY 횟수, STRONG_BUY 횟수, BUY 비율 등)<br>• `exit_reason_dist: dict` (profit/stop/timeout/signal 비율) |

### FR-15: Equity Curve 출력

| ID | 요구사항 |
|----|----------|
| FR-15 | 백테스트 종료 시 `equity_curve`를 콘솔 출력 (ASCII 차트 또는 표). 추가로 `data/backtest_equity_curve_{model}.json`에 저장 |

### FR-16: 모델 비교 표

| ID | 요구사항 |
|----|----------|
| FR-16 | `format_model_comparison(results: dict[str, BacktestResult]) -> str` — 모든 모델 결과를 한 표로 나란히 표시. 컬럼: model, 총 수익률, 거래 횟수, 승률, BUY/STRONG_BUY 비율, 청산 사유 분포, 최종 equity |

### FR-17: 단위 테스트

| ID | 요구사항 |
|----|----------|
| FR-17 | `tests/test_sentiment_sizing.py`:<br>• `_calc_size_factor` 경계값(50, 60, 80, 90) 테스트<br>• `_calc_exit_thresholds` 경계값 테스트<br>• `_calc_shares` cash 부족·정수 floor 테스트<br>• Position 진입/청산 시뮬 (cash 차감, 수수료 적용)<br>• MAX_POSITIONS 한도 도달 시 skip 동작 테스트<br>• equal mode 회귀 (FR-11 default 동작) 테스트 |

### FR-18: 회귀 자동 비교 스크립트

| ID | 요구사항 |
|----|----------|
| FR-18 | `scripts/regression_check.py`:<br>• 인자: `--baseline data/backtest_snapshots/<old>.json --current data/backtest_snapshots/<new>.json`<br>• equal mode 결과를 entry/exit/pnl 단위까지 비교, 차이 발견 시 exit code 1 + diff 출력<br>• Plan SC-03 자동 검증 |

---

## 5. 변경 대상 파일

| 파일 | 유형 | 주요 변경 |
|------|------|-----------|
| `config.py` | 수정 | sentiment-derived 상수 11개 추가 (size 5 + exit 6) |
| `backtester.py` | 대규모 수정 | Portfolio/Position/EquityPoint dataclass + cash model + sentiment 분기 + 출력 포맷 + 모델 비교 표 (~400 LOC 추가/변경) |
| `tests/__init__.py` | 신규 | 빈 파일 (테스트 디렉토리 마커) |
| `tests/test_sentiment_sizing.py` | 신규 | FR-17 단위 테스트 (~200 LOC) |
| `scripts/__init__.py` | 신규 | 빈 파일 |
| `scripts/regression_check.py` | 신규 | FR-18 회귀 비교 스크립트 (~80 LOC) |
| `requirements.txt` | (선택) | pytest 추가 (이미 있을 수 있음, M0에서 확인) |

**변경 없는 파일**: `signals.py`, `sentiment_provider.py`, `wsb_signal_engine.py`, `reddit_*`, `kis_broker.py`, `trader.py`, `app.py`, `scheduler.py`, `main.py`(--sizing 인자는 이미 존재)

---

## 6. 새 config 상수 / 환경변수

```python
# --- Sentiment-Driven Sizing (sentiment-driven-sizing) ---

# Position size factor (sentiment-driven)
SENTIMENT_SIZE_HIGH_THRESHOLD = 80.0   # sentiment >= 80 → HIGH
SENTIMENT_SIZE_MID_THRESHOLD  = 60.0   # 60 <= sentiment < 80 → MID
SENTIMENT_SIZE_HIGH = 1.5              # 1.5x base position
SENTIMENT_SIZE_MID  = 1.0              # baseline
SENTIMENT_SIZE_LOW  = 0.5              # 0.5x base position
# 주의: 기존 SENTIMENT_SIZE_HIGH/MID/LOW(0.15/0.10/0.05)는 "% cash" 의미였음.
# Design 단계에서 의미 충돌 정리 결정 (이름 변경 vs 흡수). 일단 weight factor로 사용.

# Exit thresholds (sentiment-driven)
PROFIT_TARGET_HIGH_PCT = 2.5    # sentiment HIGH → 익절 +2.5%
PROFIT_TARGET_MID_PCT  = 1.0    # MID → +1.0% (기존 동작)
PROFIT_TARGET_LOW_PCT  = 0.5    # LOW → +0.5%

STOP_LOSS_HIGH_PCT = -3.0       # sentiment HIGH → 손절 -3%
STOP_LOSS_MID_PCT  = -2.0       # MID → -2%
STOP_LOSS_LOW_PCT  = -1.0       # LOW → -1%
```

```bash
# .env 추가 없음 — POSITION_SIZING은 main.py --sizing 인자로 충분
```

---

## 7. 인터페이스 / 계약

### 7.1 신규/수정 함수 시그니처

```python
# backtester.py (신규)
@dataclass
class Position:
    shares: int
    entry_price: float
    entry_date: str
    entry_sentiment: float | None
    profit_target: float    # +% threshold
    stop_loss: float | None # -% threshold (None=비활성)
    days_held: int

@dataclass
class Portfolio:
    cash: float
    positions: dict[str, Position]   # symbol -> Position

@dataclass
class EquityPoint:
    date: str
    cash: float
    holdings_value: float
    total_equity: float

def _calc_size_factor(sentiment: float, mode: str) -> float: ...
def _calc_exit_thresholds(sentiment: float, mode: str) -> tuple[float, float | None]: ...
def _calc_shares(cash: float, price: float, size_factor: float, position_pct: float) -> int: ...

def _backtest_single_model(
    model: str,
    signals_by_date: dict[str, dict[str, str]],   # date -> {symbol: signal}
    sentiment_by_date: dict[str, dict[str, float]],   # date -> {symbol: sentiment}  ← 신규
    ohlcv_by_date: dict,
    sizing_mode: str = "equal",   # ← 신규 (default로 회귀 보장)
) -> BacktestResult: ...

def format_model_comparison(results: dict[str, BacktestResult]) -> str: ...
```

### 7.2 TradeRecord 확장

```python
@dataclass
class TradeRecord:
    symbol: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    pnl_pct: float
    is_win: bool
    # ===== 신규 (FR-13) =====
    shares: int = 1
    dollar_pnl: float = 0.0       # shares × (exit-entry) - commission
    size_factor: float = 1.0
    entry_sentiment: float | None = None
    exit_reason: Literal["profit", "stop", "timeout", "signal"] = "timeout"
```

### 7.3 BacktestResult 확장

```python
@dataclass
class BacktestResult:
    model: str
    trades: list[TradeRecord]
    win_rate: float
    avg_pnl_pct: float
    total_trades: int
    # ===== 신규 (FR-14) =====
    equity_curve: list[EquityPoint]
    signal_stats: dict       # {"buy_count": N, "strong_buy_count": M, "buy_rate": x.xx, ...}
    exit_reason_dist: dict   # {"profit": 0.42, "stop": 0.10, "timeout": 0.30, "signal": 0.18}
    final_equity: float
    final_return_pct: float
```

### 7.4 출력 포맷 예시

```
╔══════════════════════════════════════════════════════════════════════════════╗
║         Model Comparison (period: 2026-02-01 ~ 2026-04-01, $100K initial)    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Model     | Trades | WinRate | AvgPnL% | StrongBuy% | FinalEq    | Return   ║
║ textblob  |   12   |  75.0%  |  +1.5%  |   8.3%     | $103,200   | +3.20%   ║
║ finbert   |   14   |  78.6%  |  +1.8%  |   21.4%    | $105,400   | +5.40%   ║
║ gpt5      |   11   |  81.8%  |  +2.1%  |   36.4%    | $107,100   | +7.10%   ║
╠══════════════════════════════════════════════════════════════════════════════╣
║ Exit Reasons (textblob/finbert/gpt5):                                         ║
║   profit:    42% / 64% / 73%                                                  ║
║   stop:      08% / 07% / 09%                                                  ║
║   timeout:   42% / 21% / 09%                                                  ║
║   signal:    08% / 07% / 09%                                                  ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

---

## 8. 리스크

| 리스크 | 영향도 | 대응 |
|--------|--------|------|
| `--sizing equal` 회귀 (기존 백테스트 결과와 어긋남) | **높음** | (1) Default 동작 명시 (size=1.0, stop 비활성, profit=1.0%) (2) `scripts/regression_check.py` 자동 검증 (FR-18) (3) 기존 `data/backtest_snapshots/`와 trade-by-trade 비교 |
| backtester 코어 재설계로 기타 호출부 영향 | 중 | `_simulate_trades_for_symbol` 시그니처 변경 → `run_all_models`, `_backtest_single_model` 호출부 동시 수정. main.py CLI는 `--sizing` 인자 이미 존재 |
| weight 매핑 수치(80/60, 1.5/1.0/0.5)가 임의값 | 중 | Plan은 초기값. Design에서 sensitivity 분석. 본격 grid search는 별도 피처(`sizing-grid-search`) |
| 손절(stop_loss) 신규 도입으로 기존 trade 패턴 자체가 바뀜 | 중 | equal mode에서는 손절 비활성. sentiment mode에서만 적용 — 의도된 분기. 회귀 0 자동 검증 |
| 종목 간 자본 공유로 종목별 PnL 해석 변경 | 중 | 출력에 "공유 풀 모드"임을 명시. Σ trades × 평균 PnL%이 아닌 final equity로 평가 |
| MAX_POSITIONS=10 한도가 의미 있는 BUY 신호를 놓침 | 낮음 | live trading과 동일 동작 → 의도된 제약. 로그로 skip 사유 기록 |
| 기존 `data/backtest_snapshots/` 포맷 변경 | 낮음 | equal mode는 동일 trade list 출력 → 스냅샷 호환. 신규 필드는 누락 시 default 값 |
| pytest 미설치 환경 | 낮음 | `requirements.txt`에 pytest 명시. `tests/`는 의존성 설치 후 실행 |

---

## 9. 성공 기준

| SC | 기준 | 검증 방법 |
|----|------|---------|
| SC-01 | `--sizing sentiment` 활성화 시 백테스트 출력에 모델별 신호 통계 표 표시 (BUY/STRONG_BUY 횟수, 가중치, equity, 청산 사유) | 콘솔 출력 검사 |
| SC-02 | 동일 기간(2026-02-01~2026-04-01) **TextBlob vs GPT5 STRONG_BUY 비율** 차이 ≥10%p | format_model_comparison 출력 |
| SC-03 | **Σ size_factor** 차이 ≥10% (모델 간 자본 노출 합) | signal_stats 검사 |
| SC-04 | **청산 사유 분포** 차이 식별 가능 (TextBlob timeout 비중 vs GPT5 profit 비중) | exit_reason_dist 검사 |
| SC-05 | **`--sizing equal` 회귀 0** | `scripts/regression_check.py` 자동 통과 (exit code 0) |
| SC-06 | TradeRecord 신규 필드 모두 채워짐 (shares, dollar_pnl, size_factor, entry_sentiment, exit_reason) | trades JSON 출력 검사 |
| SC-07 | `_calc_size_factor(50)=0.5`, `(70)=1.0`, `(85)=1.5` 단위 테스트 통과 | pytest |
| SC-08 | Equity curve 출력 (콘솔 + `data/backtest_equity_curve_{model}.json`) | 파일 존재 확인 + 콘솔 검사 |
| SC-09 | 모델 비교 표 한 번 실행으로 textblob/finbert/gpt5 결과 동시 출력 | `--model combined` 또는 모든 모델 자동 실행 |
| SC-10 | MAX_POSITIONS=10 한도 도달 시 BUY skip + 로그 출력 | 단위 테스트로 시뮬 |

---

## 10. 구현 순서 (Module Map / Session Guide)

| Module | 파일 | 작업 | 난이도 | Session |
|--------|------|------|--------|---------|
| M0 | requirements.txt | pytest 확인/추가 | 낮음 | S1 (~10시간 분량) |
| M1 | config.py | sentiment-derived 상수 11개 추가 | 낮음 | S1 |
| M2 | backtester.py | Portfolio/Position/EquityPoint dataclass + helper 함수 (`_calc_*`) | 중간 | S1 |
| M3 | backtester.py | `_backtest_single_model` cash model 재구성 (진입/청산/equity 추적) | 중간 | S1 |
| M4 | backtester.py | TradeRecord/BacktestResult 확장 + 출력 포맷 (모델 비교 표 + equity curve) | 중간 | S1 |
| M5 | tests/test_sentiment_sizing.py | 단위 테스트 (FR-17) | 낮음 | S1 |
| M6 | scripts/regression_check.py | 회귀 자동 비교 스크립트 (FR-18) | 낮음 | S1 |
| M7 | 검증 | `--sizing equal` 회귀 검증 + `--sizing sentiment` 효과 측정 | 중간 | S1 |

**권장 세션 분할**: 단일 세션 ~10시간. `module-1` (M0~M2 config + helpers, ~2시간), `module-2` (M3~M4 cash model + 출력, ~5시간), `module-3` (M5~M7 테스트 + 검증, ~3시간) 식으로 3개 sub-scope로 분할 가능.

---

## 11. Brainstorming Log (plan-plus 결정 기록)

| Phase | 결정 | 이유 |
|-------|------|------|
| 1 (Q1) | 핵심 문제 = combined (평가 + 매매) | 1단계 backtester, 2단계 live 단계적 접근 |
| 1 (Q2) | 1차 사용자 = 운영자 알파 | live 전사 가능성을 중심에 두는 설계 — Approach B 채택 명분 |
| 2 | Approach B (Cash/Shares) | live transferability 우선. 6h vs 15h 트레이드오프 수용 |
| 3 | YAGNI: 4개 선택적 기능 모두 v1 | 운영자 알파 입장에서 인프라 두텁게. equity curve는 거의 free, 회귀 자동화는 회귀 0 약속 보증 필수 |
| 4 (4-1) | Architecture/Components 그대로 진행 | 사용자 승인 |
| 4 (4-2) | 자본 배분 = 공유 풀 + MAX_POSITIONS=10 | live KIS 모의투자가 단일 계좌·공유 cash 구조 → 직접 전사 가능 |

---

## 12. 미래 확장 (Out of Scope)

| 확장 | 분리 피처명 | 설명 |
|------|------------|------|
| 진입 강도 차등(entry threshold) | `sentiment-driven-entry` | sentiment ≥ 70만 진입 / 50~70은 0.5 weight 진입 등 |
| 보유 기간 차등(hold days) | `sentiment-driven-hold` | high sentiment → 21일까지, low → 7일 timeout |
| signals.py 실시간 신호에 적용 | `live-sentiment-sizing` | KIS 모의투자 실주문에 size_factor 적용 (kis-paper-trading 완료 후 진입) |
| Reddit/wsb_signal_engine 적용 | `wsb-sentiment-sizing` | entry_score 기반 sizing 차등 |
| Sensitivity 분석 자동화 | `sizing-grid-search` | weight/threshold 매트릭스 자동 백테스트 |
| Trade-level JSON export | `backtest-export-json` | 사후 분석/시각화용 |

---

## 13. 참고 자료

- 본 Plan 트리거: 2026-05-03 운영자 분석 — TextBlob/GPT5 백테스트 결과 동일 원인 추적
- Plan-plus 5단계 진행: Intent Discovery → Alternatives → YAGNI → Incremental → Document
- 관련 코드: `signals.py:39` (BUY 조건), `backtester.py:240` (청산 조건), `backtester.py:262` (진입 조건)
- 기존 백테스트 스냅샷: `data/backtest_snapshots/` (회귀 검증 baseline)
- 기존 config 옵션: `config.POSITION_SIZING` (이미 정의: equal/sentiment/volatility)
- 기존 CLI: `main.py:85` `--sizing` argparse 인자 이미 존재
- 보충 분석: `data/trading.log` 라인 5512+ ~ 5780 (FinBERT 백테스트 rerun, 2026-05-03)
- 관련 Plan: `kis-paper-trading.plan.md` (live trading 인프라, Out of Scope이지만 본 피처 결과를 적용할 대상)
