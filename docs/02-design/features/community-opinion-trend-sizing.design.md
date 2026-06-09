# Design: Community Opinion Trend Sizing — 커뮤니티 여론 트렌드 기반 사이징·리스크 관리

**Feature**: community-opinion-trend-sizing
**Date**: 2026-05-29
**Status**: Design (Option C — Pragmatic Balance)
**Plan**: `docs/01-plan/features/community-opinion-trend-sizing.plan.md`
**PRD**: 없음 (운영자 단일 사용 기능)

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 커뮤니티 여론의 *방향·지속성·합의도·관심도 변화*를 사이징/청산에 반영. 현재는 단일 entry_score 의존 + NEW_SPIKE를 매수로 봄(급등추격). |
| **WHO** | 1차 전략 연구자(백테스트 검증) → 2차 운영자(KIS 전사). |
| **RISK** | check_exit 공유로 equal 회귀 위험 → `opinion_mode` 게이팅 / 점수이력 부재 → 인메모리 score_history / 소표본 9~17일 / factor 임의값 / backtester.py·signals.py 불가침. |
| **SUCCESS** | opinion_trend·sentiment ranking 동작 + 진입게이팅·clamp·축소 정확 + **equal 회귀 0** + opinion_reversal 감지 + pytest 통과. |
| **SCOPE** | 신규: `tests/test_opinion_trend_sizing.py`, `score_history.json`. 수정: position_sizer/wsb_signal_engine/wsb_state/reddit_portfolio/reddit_backtester/config/main. **Out**: backtester.py·signals.py·뉴스 라이브·KIS 실주문·gpt5 비교·factor grid search·네이버 종토방. |

---

## 1. Overview

Plan의 **Approach A**(backtester가 인메모리로 opinion 지표를 계산·주입) 위에, opinion 입력을 **typed `OpinionMetrics` dataclass**로 캡슐화하는 **Option C(Pragmatic Balance)**를 채택한다. 신규 모듈은 만들지 않고(=plan-plus에서 연기한 B/opinion_metrics.py 회피), 계산 helper는 `wsb_state`에 둔다.

### 1.1 선택 Architecture: Option C

| 항목 | 결정 |
|------|------|
| 신규 파일 | 2개 (`tests/test_opinion_trend_sizing.py`, `data/score_history.json`) |
| 수정 파일 | 7개 (position_sizer/wsb_signal_engine/wsb_state/reddit_portfolio/reddit_backtester/config/main) |
| 신규 클래스 | `CommunityOpinionTrendSizer`, `OpinionMetrics`, `RedditTradeRecord`, `RedditBacktestResult` |
| 회귀 위험 | 🟢 낮음 (신규 sizer + `opinion_mode=False` 기본 + 기존 sizer/exit 무변경) |

### 1.2 핵심 원칙

| 원칙 | 적용 |
|------|------|
| **equal 회귀 0** | `check_exit(opinion_mode=False)` 기본값 → 기존 5단계 byte 동일. 신규 sizer는 별도 클래스. |
| **Typed 경계** | opinion 입력은 `OpinionMetrics` dataclass 단일 객체로 전달 (dict 키 오타 방지). |
| **결정성** | 백테스트는 전역 `score_history.json` 미사용, 인메모리 dict 누적. |
| **YAGNI** | 신규 모듈/엔진 클래스 없음. helper는 `wsb_state` 함수로. |
| **불가침 격리** | `RedditTradeRecord/Result`를 reddit_backtester에 신규 정의 → `backtester.py` 무수정. |

### 1.3 Discarded Options

| Option | Reject 사유 |
|--------|------------|
| A — Minimal (plain dict) | opinion 지표 키가 문자열 dict → 신규 sizer/exit 입력 타입 안전성↓, 테스트 취약 |
| B — Clean (opinion_metrics.py 모듈) | plan-plus YAGNI에서 연기. Sizer 1.x종·백테스트 1차 목적엔 과설계 |

---

## 2. Architecture (opinion_trend 모드 데이터 흐름)

```
RedditReplayBacktester.run()                       [reddit_backtester.py]
  opinion_history: dict[str, list[dict]] = {}      # 인메모리, 전역파일 미오염
  equity_curve: list[(date, total_value)] = []
  for date in dates:
     top_n, signal_details = engine.run_pipeline(posts, df_cache, date)   # ranking="sentiment" 지원
     scored = {sym: detail}
     # (1) 오늘 스냅샷 누적 + 지표 계산
     for sym, d in scored.items():
         opinion_history[sym].insert(0, {date, score, bullish, bearish, neutral, neutral_ratio})  # trim
     opinion_metrics: dict[str, OpinionMetrics] = {
         sym: OpinionMetrics(
            opinion_score = d["score"],
            sentiment_trend = wsb_state.compute_sentiment_trend([h["score"] for h in opinion_history[sym]], LOOKBACK),
            persistence_days = wsb_state.compute_persistence_days(opinion_history[sym]),
            consensus_ratio = wsb_state.compute_consensus_ratio(d["bullish"], d["bearish"]),
            neutral_ratio = d["neutral_ratio"],
            velocity_state = d["velocity_state"],
            atr = atr_cache.get(sym), prev_close = today_ohlcv[sym].get("prev_close"))
         for sym, d in scored.items() }
     # (2) 청산 (opinion_trend일 때만 opinion_mode=True → equal/sentiment는 False=회귀0)
     for sym in positions:
         engine.check_exit(position, today_ohlcv[sym], scored, df_cache, position_scores,
                           velocity_state=…, opinion_mode=is_opinion, opinion=opinion_metrics.get(sym))
     # (3) 매수/평가 (Sizer가 OpinionMetrics 소비, 진입 스냅샷 저장)
     portfolio.process_day(top_n, exit_signals, today_ohlcv, sizer, scored,
                           atr_cache, position_scores, opinion_metrics=opinion_metrics)
     equity_curve.append((date, portfolio.total_value))
  → RedditBacktestResult(equity_curve, MDD, profit_factor, exposure, turnover, avg_*change, trades=[RedditTradeRecord…])
```

`is_opinion = (sizing == "opinion_trend")`. equal/sentiment/volatility → `opinion_mode=False`, sizer는 `opinion` kwarg 무시.

---

## 3. Components / 책임

| 파일 | 신규/수정 | 책임 |
|------|-----------|------|
| `config.py` | 수정 | `WSB_OPINION_*` 상수 + `WSB_USE_PROFIT_TARGET=False` |
| `wsb_state.py` | 수정 | `compute_sentiment_trend / compute_persistence_days / compute_consensus_ratio` + score_history I/O(라이브용) + `upsert_position_score` 스냅샷 필드 확장 |
| `position_sizer.py` | 수정 | `CommunityOpinionTrendSizer(PositionSizer)` + `get_sizer("opinion_trend")` |
| `wsb_signal_engine.py` | 수정 | `_rank` `"sentiment"` 분기 + `check_exit(..., opinion_mode=False, opinion=None)` |
| `reddit_portfolio.py` | 수정 | `process_day(..., opinion_metrics=None)` + 매수 시 진입 스냅샷 저장 + `total_value` 노출 |
| `reddit_backtester.py` | 수정 | `OpinionMetrics`·`RedditTradeRecord`·`RedditBacktestResult` 정의 + 인메모리 history·지표 + MDD/profit_factor/exposure/turnover/avg_*change + `print_reddit_comparison` 갱신 + ranking 검증 + opinion_mode 배선 |
| `main.py` | 수정 | `--sizing` choices += `opinion_trend`, `--ranking` choices += `sentiment` |
| `tests/test_opinion_trend_sizing.py` | 신규 | Plan FR-16 |

**무수정**: `backtester.py`, `signals.py`, `sentiment_provider.py`, `collector.py`, `kis_broker.py`, `trader.py`, `scheduler.py`, `app.py`.

---

## 4. Interfaces / 계약 (★ 사용자 지정 2개 경계 확정)

### 4.1 opinion_metrics 전달 시그니처

```python
# reddit_backtester.py
@dataclass
class OpinionMetrics:
    opinion_score: float          # = signal_details["score"] (0~100)
    sentiment_trend: str          # "UP" | "FLAT" | "DOWN"
    persistence_days: int
    consensus_ratio: float        # bullish/bearish (bearish=0 → 큰 값 처리)
    neutral_ratio: float
    velocity_state: str           # 관심도 변화 (NEW_SPIKE/HIGH_MOMENTUM/NORMAL/DECLINING/NEW_IGNORE)
    atr: float | None = None
    prev_close: float | None = None

# reddit_portfolio.py — 기존 시그니처에 opinion_metrics만 추가(키워드, 기본 None)
def process_day(self, date_str, top_n, exit_signals, ohlcv, sizer,
                scored=None, atr_cache=None, position_scores=None,
                opinion_metrics: dict[str, "OpinionMetrics"] | None = None) -> dict: ...
    # 매수 분기:
    #   shares = sizer.calc_shares(self.cash, open_price,
    #               bullish_ratio=…, atr=…, prev_close=…,   # 기존 (equal/sentiment/volatility용)
    #               opinion=opinion_metrics.get(symbol) if opinion_metrics else None)
    # → EqualSizer/SentimentSizer/VolatilitySizer는 **kwargs로 opinion 무시 (회귀 0)

# position_sizer.py
class CommunityOpinionTrendSizer(PositionSizer):
    def calc_shares(self, total_cash, open_price, **kwargs) -> int:
        opinion: OpinionMetrics | None = kwargs.get("opinion")
        if opinion is None or open_price <= 0: return 0
        # 게이팅 → 7 factor 곱 → clamp[0,1.3] → floor(total_cash×EQUAL_POSITION_PCT×f/open)
```

> 핵심: **단일 `opinion` 키로 OpinionMetrics 객체를 전달.** 기존 sizer는 `**kwargs`로 흡수 → equal/sentiment/volatility 시그니처·동작 불변.

### 4.2 RedditTradeRecord / RedditBacktestResult ↔ 기존 BacktestResult 경계

```python
# reddit_backtester.py (신규 — backtester.py 무수정)
@dataclass
class RedditTradeRecord:
    symbol: str; entry_date: str; exit_date: str
    entry_price: float; exit_price: float; shares: int
    dollar_pnl: float; pnl_pct: float; holding_days: int
    exit_reason: str; size_factor: float
    entry_score: float; exit_score: float; score_change: float
    entry_consensus_ratio: float; exit_consensus_ratio: float; consensus_change: float
    entry_neutral_ratio: float; exit_neutral_ratio: float; neutral_ratio_change: float
    entry_velocity_state: str; exit_velocity_state: str
    opinion_trend_at_entry: str; opinion_trend_at_exit: str
    persistence_days: int

@dataclass
class RedditBacktestResult:
    strategy_key: str
    final_equity: float; final_return_pct: float
    max_drawdown: float; profit_factor: float
    win_rate: float; total_trades: int; avg_holding_days: float
    exposure_pct: float; turnover: float                 # YAGNI ② 포함
    equity_curve: list[tuple[str, float]]                # YAGNI ① 포함
    exit_reason_dist: dict[str, int]
    avg_entry_score: float; avg_score_change: float       # YAGNI ③ 포함
    avg_consensus_change: float; avg_neutral_ratio_change: float
    trades: list[RedditTradeRecord]
```

**경계 규칙**:
- `RedditReplayBacktester.run()` 반환 타입을 `BacktestResult` → **`RedditBacktestResult`로 교체**. `backtester.BacktestResult/TradeRecord`는 import 유지하되 reddit replay에서는 미사용(레거시 호환 위해 제거하지 않음).
- `print_reddit_comparison()` / `run_all_reddit_strategies()`를 `RedditBacktestResult` 기준으로 갱신 (Plan FR-15 컬럼).
- **모든 sizing 모드가 `RedditBacktestResult`를 반환** — equal/sentiment는 opinion 전용 필드(avg_*change 등)를 0/기본값으로 채움. **거래 outcome(pnl/trades)은 equal에서 byte 동일** (회귀 0의 대상).
- `backtester.py`는 한 줄도 수정하지 않음.

### 4.3 check_exit 확장 (mode-gated)

```python
def check_exit(self, position, today_ohlcv, scored, ohlcv_cache, position_scores,
               velocity_state="NORMAL", holding_days=0,
               opinion_mode: bool = False, opinion: "OpinionMetrics | None" = None) -> tuple[bool, str]:
    # opinion_mode=False → 기존 5단계 그대로 (sentiment_reversal→rsi_overbought→gap_down→stop_loss→trailing_stop)
    # opinion_mode=True  → 1단계를 opinion_reversal로 확장 (§6), 2~5단계 동일
```

### 4.4 wsb_state helper

```python
def compute_sentiment_trend(scores: list[float], lookback: int) -> str   # "UP"|"FLAT"|"DOWN", 부족 시 "FLAT"
def compute_persistence_days(history: list[dict]) -> int                  # bullish>bearish 연속일수
def compute_consensus_ratio(bullish: int, bearish: int) -> float          # bearish=0 → bullish≥2면 큰 값, else 0
# score_history I/O (라이브 전용, 백테스트는 인메모리)
def load_score_history() -> dict; def save_score_history(h: dict) -> None
def update_score_entry(h, symbol, entry: dict, max_days) -> dict
```

---

## 5. config 상수 (Plan §5와 동일)

`WSB_OPINION_SCORE_*`, `WSB_OPINION_FACTOR_*`, `WSB_OPINION_TREND_*`, `WSB_OPINION_PERSISTENCE_*`, `WSB_OPINION_CONSENSUS_*`, `WSB_OPINION_NEUTRAL_ENTRY_MAX(0.70)`, `WSB_OPINION_NEUTRAL_EXIT_RATIO(0.75)`, `WSB_OPINION_REVERSAL_RATIO(0.65)`, `WSB_OPINION_NEW_SPIKE_FACTOR(0.5)`, `WSB_OPINION_HIGH_ATTENTION_FACTOR(1.1)`, `WSB_OPINION_DECLINING_FACTOR(0.6)`, `WSB_OPINION_SIZE_FACTOR_MIN(0.0)/MAX(1.3)`, `WSB_USE_PROFIT_TARGET=False`.

---

## 6. Sizer 공식 & opinion_reversal

```
# 게이팅 (0 반환)
if opinion_score < 60 or neutral_ratio > 0.70 or consensus_ratio < 1.5: return 0

sentiment_factor   = 1.2 if ≥80 elif 1.0 if ≥70 elif 0.7        (≥60)
trend_factor       = 1.15(UP) / 1.0(FLAT) / 0.5(DOWN)
persistence_factor = 1.2(≥3d) / 1.0(2d) / 0.6(1d)
consensus_factor   = 1.2(≥2.0) / 1.0(≥1.5)
neutral_factor     = 0.7(0.50~0.70) / 1.0(≤0.50)
attention_factor   = 1.1(안정증가) / 1.0(NORMAL) / 0.5(NEW_SPIKE) / 0.6(DECLINING)
risk_factor        = 0.5~0.8(ATR과대) / 1.0(데이터없음)

final = clamp(Π factors, 0.0, 1.3)
shares = floor(total_cash × EQUAL_POSITION_PCT × final / open_price)
# NEW_SPIKE & persistence<2 → attention_factor 0.5 (큰 비중 진입 금지, 0은 아님)

# opinion_reversal (opinion_mode=True, check_exit 1단계). 하나라도 참이면 청산:
opinion_score < entry_score×0.65  OR  2일 연속 score 하락
 OR  bullish/bearish ≤ 1.0  OR  neutral_ratio > 0.75
 OR  bearish_count ≥ entry_bearish_count × 2
# 2~5단계(rsi_overbought/gap_down/stop_loss/trailing_stop) 유지. WSB_USE_PROFIT_TARGET=False.
```

---

## 7. 진입 스냅샷 (position_scores 확장, FR-09)

매수 시 `position_scores[symbol]`에 저장: `entry_score, entry_bullish_count, entry_bearish_count, entry_neutral_count, entry_neutral_ratio, entry_consensus_ratio, entry_velocity_state, entry_opinion_trend, entry_persistence_days, size_factor, stop_loss_pct, trailing_stop_pct`. 기존 `yesterday_below/rsi_held`와 공존. 청산 시 현재 opinion과 비교(§6).

---

## 8. Test Plan (tests/test_opinion_trend_sizing.py)

| ID | 시나리오 |
|----|----------|
| T1 | opinion_score 85 → sentiment_factor 1.2 반영 |
| T2 | opinion_score 55 → 0주(진입 제외) |
| T3 | neutral_ratio 0.75 → 0주 |
| T4 | consensus_ratio 1.2 → 0주 |
| T5 | sentiment_trend UP(3일 상승) → trend_factor 1.15 |
| T6 | sentiment_trend DOWN → trend_factor 0.5 |
| T7 | NEW_SPIKE & persistence 1 → attention 0.5 축소 |
| T8 | DECLINING → 0.6 축소 |
| T9 | 모든 factor 최대여도 final ≤ 1.3 |
| T10 | opinion_reversal: score<entry×0.65 / bull≤bear / neutral>0.75 / bearish 2배 각각 감지 |
| T11 | **회귀**: `opinion_mode=False`에서 check_exit 결과가 기존과 동일 (대표 케이스) |
| T12 | EqualSizer/SentimentSizer가 `opinion` kwarg 받아도 기존 동작 동일 |

---

## 9. 리스크 & 대응

| 리스크 | 대응 |
|--------|------|
| check_exit 공유 회귀 | `opinion_mode` 기본 False + T11 회귀 테스트 |
| 반환타입 교체로 `--report-reddit` 깨짐 | `print_reddit_comparison`/`run_all_reddit_strategies` 동시 갱신, equal outcome 불변 |
| 소표본(9~17일) | trend(3d)/persistence 계산 가능하나 결과는 방향성 확인용, 14일 경고 유지 |
| 전역 score_history 오염 | 백테스트 인메모리 누적 |
| factor 임의값 | 초기값, grid search 별도 피처 |

---

## 10. Discarded / Out of Scope

gpt5 비교, factor grid search, opinion_metrics.py 전용 모듈, 네이버 종토방 수집기, KIS 실주문 적용, backtester.py/signals.py 수정.

---

## 11. Implementation Guide

### 11.1 구현 원칙
- `// Design Ref: §4.1` 등 핵심 결정에 주석. `// Plan SC: SC-10` 회귀 지점 표시.
- 각 모듈 완료마다 `pytest tests/test_opinion_trend_sizing.py` 부분 실행.

### 11.2 의존성
- 추가 설치 없음 (torch/transformers/optimum는 venv에 존재). `pytest`는 requirements에 존재.

### 11.3 Session Guide (Module Map — `/pdca do --scope`)

| scope key | Module | 파일 | 작업 | 의존 |
|-----------|--------|------|------|------|
| **module-1** | M1 | config.py | WSB_OPINION_* 상수 | — |
| **module-1** | M2 | wsb_state.py | trend/persistence/consensus helper + score_history I/O + 스냅샷 필드 | M1 |
| **module-2** | M3 | reddit_backtester.py | `OpinionMetrics` dataclass 정의 | M1 |
| **module-2** | M4 | position_sizer.py | CommunityOpinionTrendSizer + get_sizer 등록 | M1,M3 |
| **module-2** | M5 | wsb_signal_engine.py | `_rank` sentiment + `check_exit(opinion_mode)` | M3 |
| **module-3** | M6 | reddit_portfolio.py | process_day(opinion_metrics) + 스냅샷 저장 + total_value | M3,M4 |
| **module-3** | M7 | reddit_backtester.py | RedditTradeRecord/Result + 인메모리 history + 지표 + 비교표 + ranking 검증 | M3,M5,M6 |
| **module-3** | M8 | main.py | --sizing/--ranking choices | M4,M5 |
| **module-4** | M9 | tests/test_opinion_trend_sizing.py | T1~T12 | M4,M5 |
| **module-4** | M10 | 검증 | 두 백테스트 명령 + pytest + equal 회귀 확인 | 전체 |

**권장 세션 분할**: module-1(상수+helper) → module-2(dataclass+sizer+engine) → module-3(portfolio+backtester+main) → module-4(테스트+검증).

**검증 명령**
```
python main.py --backtest --source reddit --model finbert-wsb --ranking sentiment --sizing opinion_trend --from 2026-05-13 --to 2026-05-29
python main.py --backtest --source reddit --model finbert-wsb --ranking sentiment --sizing equal --from 2026-05-13 --to 2026-05-29
pytest tests/test_opinion_trend_sizing.py
```
