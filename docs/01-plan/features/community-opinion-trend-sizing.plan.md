# Plan: Community Opinion Trend Sizing — 커뮤니티 여론 트렌드 기반 사이징·리스크 관리

**Feature**: community-opinion-trend-sizing
**Date**: 2026-05-29
**Status**: Plan (plan-plus enhanced)
**Branch**: `rsi_finBERT_combine`
**Method**: `/plan-plus` (Intent → Alternatives → YAGNI → Incremental Validation, 사용자 승인 완료)

---

## Executive Summary

| 관점 | 내용 |
|------|------|
| **Problem** | 현재 WSB V3는 NEW_SPIKE(언급 폭증)를 매수 신호로 취급해 "급등 모멘텀 추격"처럼 작동할 여지가 있고, 의견의 *방향성·지속성·합의도·관심도 변화*를 포지션 크기/청산에 반영하지 못한다. 단일 `entry_score` 하나로만 진입/청산을 판단한다. |
| **Solution** | WSB V3 **신호 로직(BUY/STRONG_BUY)은 그대로 두고**, 그 위에 ① 7개 factor 곱으로 포지션 크기를 정하는 `CommunityOpinionTrendSizer`(`--sizing opinion_trend`), ② 의견 변화 중심으로 강화된 청산(`opinion_reversal`, 기존 5단계 구조 유지), ③ 진입 시점 의견 스냅샷 저장 + 의견 트렌드/지속성/합의도 변화를 측정하는 백테스트 지표 확장을 추가한다. velocity_state는 *가격 모멘텀이 아니라 커뮤니티 관심도 변화*로 재해석한다. |
| **Function/UX Effect** | `python main.py --backtest --source reddit --model finbert-wsb --ranking sentiment --sizing opinion_trend --from … --to …` 실행 시 종목별 final_size_factor(0.0~1.3), 진입/청산 시점 의견 상태 변화(score/consensus/neutral), exit_reason 분포, equity curve, max_drawdown, profit_factor가 비교표로 출력된다. `--sizing equal` 결과는 회귀 0. |
| **Core Value** | "여론이 한쪽으로 모이고(consensus) 며칠 유지되며(persistence) 추세가 살아있는(trend) 종목"에 더 큰 비중을 싣고, "갑작스런 단발 폭증·중립 노이즈·여론 붕괴"는 비중 축소/청산하는 **여론 기반 리스크 관리 인프라**. 급등추격이 아니라 의견 지속성에 베팅하는 전략의 정량 검증 경로 확보. |

---

## Context Anchor

| 항목 | 내용 |
|------|------|
| **WHY** | 커뮤니티(Reddit/WSB, 향후 네이버 종토방)의 여론은 *방향·지속성·합의도·관심도 변화*가 핵심 신호다. 현재 시스템은 이 차원들을 포지션 크기와 청산에 못 녹여서, 좋은 여론도 나쁜 여론도 동일 비중으로 매매되고 NEW_SPIKE를 위험이 아닌 매수로 본다. |
| **WHO** | 미국주식 페이퍼 트레이딩 시스템 운영자(본인). 1차 = 전략 연구자(여론 트렌드 가설을 백테스트로 검증), 2차 = 운영자(검증된 sizing/exit를 KIS 모의투자로 전사). |
| **RISK** | (1) `check_exit()`는 모든 sizing 모드가 공유 → opinion_reversal 확장이 equal 결과를 바꿀 위험 ★높음 → **mode-gating으로 격리**. (2) trend/persistence는 일별 점수 이력이 필요한데 현재 없음 → 신규 `score_history.json` + 백테스트 인메모리 누적(전역 파일 오염 방지). (3) 수집 데이터가 9~17일치뿐(최소 14일 경고) → 통계적 검정력 낮음, 결과는 방향성 확인용. (4) factor 수치(1.2/1.15/1.2…)는 임의 초기값 → grid search는 별도 피처. (5) `TradeRecord`/`BacktestResult`는 `backtester.py` 소속(불가침) → reddit 전용 dataclass 신규 정의. |
| **SUCCESS** | (1) `--sizing opinion_trend` 동작 (2) `--ranking sentiment` 동작 (3) opinion_score<60·neutral>0.70·consensus<1.5 → 진입 제외(0주) (4) final_size_factor ∈ [0, 1.3] clamp (5) NEW_SPIKE 단독 비중 축소 (6) opinion_reversal 강화 조건 감지 (7) **`--sizing equal` 회귀 0** (8) RedditTradeRecord/Result 신규 필드 채워지고 비교표 출력 (9) pytest 통과. |
| **SCOPE** | 신규: `tests/test_opinion_trend_sizing.py`, `score_history.json`(데이터). 수정: `position_sizer.py`(+Sizer), `wsb_signal_engine.py`(ranking·exit), `wsb_state.py`(score history·entry 스냅샷), `reddit_portfolio.py`(kwargs·스냅샷 저장), `reddit_backtester.py`(metrics·dataclass·지표·비교표), `config.py`(WSB_OPINION_*). **Out of Scope**: `backtester.py`·`signals.py`·뉴스 라이브 경로 불가침, KIS 실주문 적용, 네이버 종토방 수집기, factor grid search. |

---

## A. User Intent Discovery (plan-plus Phase 1)

> 사용자가 제공한 상세 스펙에서 이미 의도가 명확 → 추가 질문 없이 정리.

- **핵심 문제 (Q1)**: 급등 모멘텀 추격이 아니라 *커뮤니티 여론의 방향·지속성·합의도·관심도 변화*를 매매 판단(특히 사이징·청산)에 반영하는 것. NEW_SPIKE는 매수가 아니라 위험 신호로 본다.
- **1차 사용자 (Q2)**: 전략 연구자(여론 트렌드 가설을 백테스트로 검증) → 2차 운영자(검증된 sizing/exit를 KIS 모의투자로 전사).
- **성공 기준 (Q3)**: `--sizing opinion_trend`·`--ranking sentiment` 동작 + 진입 게이팅/clamp/축소 규칙 정확 + **equal 회귀 0** + opinion_reversal 감지 + pytest 통과.

## B. Alternatives Explored (plan-plus Phase 2)

> opinion 지표 계산·score 이력의 배치 3안 비교 → **A 선택**.

| 접근 | 메커니즘 | Pros | Cons |
|------|----------|------|------|
| **A: Sizer중심·backtester (선택)** | replay가 인메모리 score_history 누적·지표 계산 → process_day/check_exit로 주입. wsb_state는 얇은 helper만 | blast radius 최소, 결정성↑, equal 무변경 | reddit_backtester.py 비대 |
| B: wsb_state 상태 저장소 | wsb_state가 영속 history·지표 소유 | 라이브 재사용, 단일 진실원천 | 전역파일 오염/비결정성(기존 문제), 침습적 |
| C: 신규 opinion_metrics.py | 전용 모듈이 지표/이력 담당 | 관심사 분리, 재사용 깔끔 | 파일 추가, 현 범위엔 과설계 |

**A 선택 근거**: 확정 D2(인메모리)·D7(mode-gating)과 정합, equal 회귀 위험 최소, replay가 이미 시간순 순회라 지표 1회 계산이 자연스러움. (라이브 전사 본격화 시 C로 리팩터링 여지)

## C. YAGNI Review (plan-plus Phase 3)

> 코어는 무조건 포함, 부가 분석은 multiSelect로 선별.

**v1 포함 (코어)**: CommunityOpinionTrendSizer+7factor, 진입 게이팅(0주), opinion_reversal(mode-gated), RedditTradeRecord 핵심필드, sentiment ranking, equal 회귀 0, final_equity/return_pct/win_rate/total_trades, exit_reason_dist, 비교표 기본, pytest.

**v1 포함 (부가, 사용자 선택)**:
- ✅ ① equity_curve + max_drawdown + profit_factor
- ✅ ③ 의견변화 진단지표(avg_score_change / avg_consensus_change / avg_neutral_ratio_change)
- ✅ ② exposure_pct + turnover

**Out of Scope (연기)**:
- ❌ ④ gpt5 비교 대상 (OpenAI API 비용) → v1 비교는 로컬 finbert/finbert-wsb 3전략. 향후 추가
- ❌ factor grid search, 네이버 종토방 수집기, KIS 실주문 적용

## 1. 핵심 결정 (Checkpoint 2 확정)

| # | 결정 | 내용 |
|---|------|------|
| D1 | **opinion_score 정의** | `opinion_score = 기존 sentiment score(0~100, signal_details["score"])`. trend·persistence·consensus·neutral·attention·risk는 **별도 곱셈 factor**. (점수에 다 합치면 factor와 이중계산) |
| D2 | **점수 이력 저장** | 신규 `data/score_history.json` `{symbol:[{date,score,bullish,bearish,neutral,neutral_ratio},…]}`(최신→과거). **백테스트 replay에서는 전역 파일을 건드리지 않고 인메모리 dict로 누적** → 결정성·오염 방지. |
| D3 | **ranking sentiment** | `WSBSignalEngine._rank`에 `"sentiment"`(score 내림차순) 추가 + `main.py --ranking` choices 확장 + `reddit_backtester` 검증 허용. |
| D4 | **dataclass 확장** | `backtester.py` 불가침이므로 `reddit_backtester.py`에 `RedditTradeRecord`/`RedditBacktestResult` **신규 정의**. 기존 `BacktestResult` import는 유지(레거시 호환). |
| D5 | **모델 비교 대상** | `gpt4`는 시스템에 없으므로 `gpt5`(=`config.GPT_MODEL_ALIAS`)로 간주 — 단 plan-plus YAGNI로 **gpt5 비교는 v1 Out of Scope(연기)**. v1 비교는 finbert/finbert-wsb 3전략. 데이터 9~17일 소표본 인지. |
| D6 | **진입 게이팅 위치** | `opinion_score<60`/`neutral_ratio>0.70`/`consensus_ratio<1.5`/`NEW_SPIKE+persistence부족` 등은 **Sizer가 0 또는 축소 factor 반환**으로 처리. `wsb_signal_engine`의 BUY/STRONG_BUY 신호 로직은 불변. |
| D7 | **equal 회귀 격리** | `check_exit()`의 opinion_reversal 강화는 **`opinion_mode` 인자(default False)로 게이팅**. False면 기존 5단계 동작과 byte 동일 → equal/sentiment/volatility 회귀 0. |

---

## 2. 기존 데이터 흐름 (구현 Step 1 사전 정리)

```
RedditReplayBacktester.run() (reddit_backtester.py)
  └ 날짜 루프:
      posts_by_symbol            ← RedditCollector.load_posts(date)
      df_cache / today_ohlcv     ← _slice_cache / _today_cache (OHLCV)
      top_n, signal_details      ← WSBSignalEngine.run_pipeline(...)
         signal_details[symbol] = {score, bullish, bearish, neutral, ratio,
                                    mentions, velocity_state, neutral_ratio,
                                    signal, passed_consensus, in_top_n, ...}
      scored = {symbol: signal_details}
      position_scores            ← wsb_state.load_position_scores()
      exit_signals               ← engine.check_exit(...) (5단계)
      portfolio.process_day(top_n, exit_signals, today_ohlcv, sizer,
                            scored, atr_cache, position_scores)
         └ 매수: sizer.calc_shares(cash, open_price,
                   bullish_ratio=…, atr=…, prev_close=…)
         └ Position(symbol, entry_date, entry_price, shares, highest_price)
      summary/trade_log          → BacktestResult (backtester.py 소속)
```

| 필요한 값 | 현재 위치 | 비고 |
|-----------|-----------|------|
| `opinion_score` | `signal_details["score"]` | 그대로 사용 (D1) |
| `bullish/bearish/neutral/neutral_ratio/ratio` | `signal_details` | 그대로 사용 |
| `consensus_ratio` | (신규) `bullish/max(bearish,1)` | wsb_state helper |
| `velocity_state` | `signal_details["velocity_state"]` | 관심도 변화로 해석 (D-개념) |
| `sentiment_trend`, `persistence_days` | **없음** | `score_history`(D2) 기반 신규 helper |
| `entry_score` | `position_scores["entry_score"]` | 이미 저장됨, 스냅샷 확장 필요 |
| 일별 점수 이력 | **없음** | 신규 (D2) |

---

## 3. 기능 요구사항

### FR-01~03: Opinion 지표 계산 (wsb_state.py / score history)

| ID | 요구사항 |
|----|----------|
| FR-01 | `score_history.json` I/O: `load_score_history()`, `save_score_history()`, `update_score_entry(history, symbol, {date,score,bullish,bearish,neutral,neutral_ratio})` (최신→과거, maxlen = `WSB_OPINION_TREND_LOOKBACK_DAYS`+버퍼). 백테스트는 파일 대신 in-memory dict를 인자로 받아 사용(D2). |
| FR-02 | `compute_sentiment_trend(score_series: list[float], lookback) -> "UP"|"FLAT"|"DOWN"`: 최근 N일 score 추세. 데이터 부족 시 `"FLAT"`. |
| FR-03 | `compute_persistence_days(series: list[dict]) -> int`: bullish>bearish가 연속 유지된 일수. `compute_consensus_ratio(bullish, bearish) -> float`: `bullish/bearish` (bearish=0이면 bullish≥2일 때 큰 값, 아니면 0). |

### FR-04~05: CommunityOpinionTrendSizer (position_sizer.py)

| ID | 요구사항 |
|----|----------|
| FR-04 | `CommunityOpinionTrendSizer(PositionSizer)` 추가 + `get_sizer("opinion_trend")` 등록. 기존 equal/sentiment/volatility는 **불변**. `main.py --sizing` choices에 `opinion_trend` 추가. |
| FR-05 | `calc_shares(total_cash, open_price, **kwargs)`:<br>kwargs = `opinion_score, sentiment_trend, persistence_days, consensus_ratio, neutral_ratio, velocity_state, atr, prev_close`.<br>**게이팅(0 반환)**: `opinion_score<WSB_OPINION_SCORE_LOW(60)` OR `neutral_ratio>WSB_OPINION_NEUTRAL_EXIT_RATIO?`(진입은 0.70 기준) OR `consensus_ratio<WSB_OPINION_CONSENSUS_MIN_RATIO(1.5)`.<br>그 외 7 factor 곱 → clamp[0.0, 1.3] → `shares = floor(total_cash × EQUAL_POSITION_PCT × final_size_factor / open_price)`. |

**7 factor (config 기본값, §6):**
| factor | 규칙 |
|--------|------|
| sentiment_factor | ≥80:1.2 / 70–80:1.0 / 60–70:0.7 / <60: 진입제외 |
| trend_factor | UP:1.15 / FLAT:1.0 / DOWN:0.5 |
| persistence_factor | ≥3일:1.2 / 2일:1.0 / 1일:0.6 |
| consensus_factor | ≥2.0:1.2 / 1.5–2.0:1.0 / <1.5:0.5(또는 게이팅) |
| neutral_factor | >0.70:진입제외 / 0.50–0.70:0.7 / ≤0.50:1.0 |
| attention_factor | 안정 증가:1.1 / NORMAL:1.0 / NEW_SPIKE:0.5 / DECLINING:0.6 |
| risk_factor | ATR/변동성 과대:0.5–0.8 / 데이터없음:1.0 |

### FR-06: 진입 보강 조건 (Sizer 내 처리, 신호엔진 불변)

| ID | 요구사항 |
|----|----------|
| FR-06 | NEW_SPIKE 단독(`velocity_state=="NEW_SPIKE"` & `persistence_days < WSB_OPINION_PERSISTENCE_MIN_DAYS(2)`) → attention_factor 0.5 적용으로 큰 비중 진입 금지(0은 아님, 소액 허용). 최근 trend가 DOWN이면 trend_factor 0.5로 축소. |

### FR-07~08: opinion_reversal 청산 (wsb_signal_engine.check_exit, mode-gated)

| ID | 요구사항 |
|----|----------|
| FR-07 | `check_exit(..., opinion_mode: bool = False)` 인자 추가. **False면 기존 5단계 동작과 동일(회귀 0).** 5단계 우선순위(sentiment_reversal→rsi_overbought→gap_down→stop_loss→trailing_stop)는 유지. |
| FR-08 | `opinion_mode=True`일 때 1단계(sentiment_reversal)를 **opinion_reversal**로 확장 — 아래 중 하나면 청산:<br>• `opinion_score < entry_score × WSB_OPINION_REVERSAL_RATIO(0.65)`<br>• 2일 연속 opinion_score 하락<br>• `bullish/bearish ≤ 1.0`<br>• `neutral_ratio > WSB_OPINION_NEUTRAL_EXIT_RATIO(0.75)`<br>• `bearish_count ≥ entry_bearish_count × 2`.<br>stop_loss/trailing_stop은 보조 유지. `WSB_USE_PROFIT_TARGET=False`(고정 익절 비활성, 현재도 미사용이라 동작 변화 없음). |

### FR-09: 진입 시점 의견 스냅샷 (wsb_state / reddit_portfolio)

| ID | 요구사항 |
|----|----------|
| FR-09 | 매수 시 `position_scores[symbol]`에 스냅샷 저장: `entry_score, entry_bullish_count, entry_bearish_count, entry_neutral_count, entry_neutral_ratio, entry_consensus_ratio, entry_velocity_state, entry_opinion_trend, entry_persistence_days, size_factor, stop_loss_pct, trailing_stop_pct`. `upsert_position_score()` 확장. 청산 시 현재 의견과 비교에 사용(FR-08). |

### FR-10: process_day → Sizer kwargs 연결 (reddit_portfolio.py)

| ID | 요구사항 |
|----|----------|
| FR-10 | `process_day`가 `opinion_metrics: dict[symbol, dict]`(trend/persistence/consensus 등, reddit_backtester가 계산해 전달)를 받아 Sizer kwargs로 전달. 매수 성공 시 FR-09 스냅샷 기록. 기존 equal/sentiment 경로는 kwargs 무시로 불변. |

### FR-11~13: 백테스트 지표 확장 (reddit_backtester.py)

| ID | 요구사항 |
|----|----------|
| FR-11 | `RedditTradeRecord`(신규 dataclass): `symbol, entry_date, exit_date, entry_price, exit_price, shares, dollar_pnl, pnl_pct, holding_days, exit_reason, size_factor, entry_score, exit_score, score_change, entry_consensus_ratio, exit_consensus_ratio, consensus_change, entry_neutral_ratio, exit_neutral_ratio, neutral_ratio_change, entry_velocity_state, exit_velocity_state, opinion_trend_at_entry, opinion_trend_at_exit, persistence_days`. |
| FR-12 | `RedditBacktestResult`(신규 dataclass): `final_equity, final_return_pct, max_drawdown, profit_factor, win_rate, total_trades, avg_holding_days, exposure_pct, turnover, equity_curve, exit_reason_dist, avg_entry_score, avg_score_change, avg_consensus_change, avg_neutral_ratio_change`. equity_curve는 `process_day`의 일별 total_value 누적으로 생성. |
| FR-13 | replay 루프에서 in-memory `score_history` 누적 → 종목별 opinion_metrics 계산 → process_day/check_exit에 전달. 청산 시점 의견 상태를 RedditTradeRecord에 기록. |

### FR-14: ranking sentiment (wsb_signal_engine / main.py)

| ID | 요구사항 |
|----|----------|
| FR-14 | `_rank`에 `"sentiment"`(score 내림차순) 분기 추가. `main.py --ranking` choices = `[mentions, ratio, sentiment]`. `reddit_backtester`/`RedditReplayBacktester` 검증에 `sentiment` 허용. |

### FR-15: 비교표 (reddit_backtester.py)

| ID | 요구사항 |
|----|----------|
| FR-15 | opinion 전략용 비교표 출력: 컬럼 = `model, ranking, sizing, final_equity, return_pct, max_drawdown, profit_factor, win_rate, total_trades, avg_holding_days, avg_entry_score, avg_score_change, avg_consensus_change, sentiment_reversal_count, consensus_break_count, neutral_spike_exit_count, stop_loss_count, trailing_stop_count`. 비교 대상(v1): `finbert-wsb+sentiment+equal`, `finbert-wsb+sentiment+opinion_trend`, `finbert+sentiment+opinion_trend`. (`gpt5`는 Out of Scope — 연기) |

### FR-16: 테스트 (tests/test_opinion_trend_sizing.py)

| ID | 요구사항 |
|----|----------|
| FR-16 | opinion_score≥80→high factor / <60→0(제외) / 3일 상승→trend↑ / 3일 하락→trend↓ / consensus 약→축소 / neutral>0.70→제외 / NEW_SPIKE 단독→축소 / DECLINING→축소 / final_size_factor≤1.3 / opinion_reversal(score↓·consensus붕괴·neutral급증) 감지 / **equal sizing 회귀(opinion_mode=False 시 기존 동작 동일)**. |

### NFR

| ID | 요구사항 |
|----|----------|
| NFR-01 | 급등추격 금지 — max factor 1.3 제한, NEW_SPIKE는 축소. |
| NFR-02 | `backtester.py`·`signals.py`·뉴스 라이브 경로 무수정. |
| NFR-03 | equal/sentiment/volatility 기존 결과 회귀 0 (opinion_mode·신규 sizer로 격리). |
| NFR-04 | 백테스트 결정성 — 전역 score_history 파일 미오염(인메모리). |

---

## 4. 변경 대상 파일

| 파일 | 유형 | 주요 변경 |
|------|------|-----------|
| `config.py` | 수정 | `WSB_OPINION_*` 상수 + `WSB_USE_PROFIT_TARGET=False` (§6) |
| `position_sizer.py` | 수정 | `CommunityOpinionTrendSizer` + `get_sizer("opinion_trend")` |
| `wsb_state.py` | 수정 | score_history I/O + trend/persistence/consensus helper + entry 스냅샷 필드 |
| `wsb_signal_engine.py` | 수정 | `_rank` sentiment 분기 + `check_exit(opinion_mode=…)` 확장 |
| `reddit_portfolio.py` | 수정 | `process_day` opinion_metrics kwargs + 진입 스냅샷 저장 |
| `reddit_backtester.py` | 수정 | RedditTradeRecord/RedditBacktestResult + in-memory score_history + 지표(max_dd/profit_factor/equity_curve) + 비교표 + ranking 검증 |
| `main.py` | 수정 | `--sizing` choices += opinion_trend, `--ranking` choices += sentiment |
| `tests/test_opinion_trend_sizing.py` | 신규 | FR-16 |

**무수정**: `backtester.py`, `signals.py`, `sentiment_provider.py`, `collector.py`, `kis_broker.py`, `trader.py`, `scheduler.py`, `app.py`.

---

## 5. config 상수 (§8)

```python
# --- WSB Community Opinion Trend Sizing (community-opinion-trend-sizing) ---
WSB_OPINION_SCORE_HIGH = 80.0
WSB_OPINION_SCORE_MID  = 70.0
WSB_OPINION_SCORE_LOW  = 60.0          # 미만 진입 제외
WSB_OPINION_FACTOR_HIGH = 1.2
WSB_OPINION_FACTOR_MID  = 1.0
WSB_OPINION_FACTOR_LOW  = 0.7

WSB_OPINION_TREND_LOOKBACK_DAYS = 3
WSB_OPINION_TREND_UP_FACTOR   = 1.15
WSB_OPINION_TREND_FLAT_FACTOR = 1.0
WSB_OPINION_TREND_DOWN_FACTOR = 0.5

WSB_OPINION_PERSISTENCE_MIN_DAYS    = 2
WSB_OPINION_PERSISTENCE_STRONG_DAYS = 3
WSB_OPINION_PERSISTENCE_WEAK_FACTOR   = 0.6
WSB_OPINION_PERSISTENCE_NORMAL_FACTOR = 1.0
WSB_OPINION_PERSISTENCE_STRONG_FACTOR = 1.2

WSB_OPINION_CONSENSUS_STRONG_RATIO = 2.0
WSB_OPINION_CONSENSUS_MIN_RATIO    = 1.5

WSB_OPINION_NEUTRAL_ENTRY_MAX = 0.70   # 초과 진입 제외
WSB_OPINION_NEUTRAL_EXIT_RATIO = 0.75  # 초과 청산
WSB_OPINION_REVERSAL_RATIO = 0.65

WSB_OPINION_NEW_SPIKE_FACTOR      = 0.5
WSB_OPINION_HIGH_ATTENTION_FACTOR = 1.1
WSB_OPINION_DECLINING_FACTOR      = 0.6

WSB_OPINION_SIZE_FACTOR_MIN = 0.0
WSB_OPINION_SIZE_FACTOR_MAX = 1.3

WSB_USE_PROFIT_TARGET = False
```

---

## 6. 핵심 공식

```
opinion_score = signal_details["score"]              # 0~100, 기존 sentiment score
consensus_ratio = bullish / bearish (bearish=0 → bullish≥2면 강함)

# 진입 게이팅 (Sizer)
if opinion_score < 60 or neutral_ratio > 0.70 or consensus_ratio < 1.5: shares = 0

final_size_factor = clamp(
    sentiment_factor × trend_factor × persistence_factor ×
    consensus_factor × neutral_factor × attention_factor × risk_factor,
    0.0, 1.3)
shares = floor(total_cash × EQUAL_POSITION_PCT × final_size_factor / open_price)

# opinion_reversal (opinion_mode=True, check_exit 1단계)
exit if (opinion_score < entry_score×0.65) or (2일 연속 하락)
     or (bullish/bearish ≤ 1.0) or (neutral_ratio > 0.75)
     or (bearish_count ≥ entry_bearish_count × 2)
```

---

## 7. 리스크

| 리스크 | 영향 | 대응 |
|--------|------|------|
| check_exit 공유로 equal 회귀 | **높음** | `opinion_mode` 기본 False 게이팅, FR-16 회귀 테스트 |
| 일별 점수 이력 부재 | 높음 | 신규 score_history + 인메모리 누적(D2) |
| 데이터 9~17일 소표본 | 중 | 방향성 확인용으로 한정, 14일 경고 유지 |
| factor 수치 임의값 | 중 | 초기값, grid search는 별도 피처 |
| backtester.py 불가침 vs dataclass 확장 | 중 | Reddit 전용 dataclass 신규(D4) |
| 전역 score_history 오염 | 중 | 백테스트 인메모리(NFR-04) |
| ranking sentiment 미지원부 누락 | 낮음 | main.py·engine·backtester 3곳 동시 수정(FR-14) |

---

## 8. 성공 기준

| SC | 기준 | 검증 |
|----|------|------|
| SC-01 | `--sizing opinion_trend` 명령 동작 | CLI 실행 |
| SC-02 | `--ranking sentiment` 동작 | CLI 실행 |
| SC-03 | opinion_score<60 → 0주(진입 제외) | pytest |
| SC-04 | neutral_ratio>0.70 → 진입 제외 | pytest |
| SC-05 | consensus_ratio<1.5 → 진입 제외 | pytest |
| SC-06 | 3일 상승 trend → trend_factor 1.15 | pytest |
| SC-07 | final_size_factor ≤ 1.3 clamp | pytest |
| SC-08 | NEW_SPIKE 단독 → factor 축소 | pytest |
| SC-09 | opinion_reversal 강화 조건 감지 | pytest |
| SC-10 | **`--sizing equal` 회귀 0** (opinion_mode=False) | pytest + 기존 결과 비교 |
| SC-11 | RedditTradeRecord/Result 신규 필드 채워짐 + 비교표 출력 | 백테스트 출력 |
| SC-12 | `pytest tests/test_opinion_trend_sizing.py` 통과 | pytest |

---

## 9. 구현 순서 (Module Map / Session Guide)

| Module | 파일 | 작업 | 난이도 | Session |
|--------|------|------|--------|---------|
| M1 | (분석) | Step 1 데이터 흐름 정리 (본 §2 완료) | 낮음 | S1 |
| M2 | config.py | WSB_OPINION_* 상수 (§5) | 낮음 | S1 (module-1) |
| M3 | wsb_state.py | score_history I/O + trend/persistence/consensus helper + entry 스냅샷 | 중 | S1 (module-1) |
| M4 | position_sizer.py | CommunityOpinionTrendSizer + get_sizer 등록 | 중 | S1 (module-2) |
| M5 | wsb_signal_engine.py | _rank sentiment + check_exit(opinion_mode) 확장 | 중 | S1 (module-2) |
| M6 | reddit_portfolio.py | process_day opinion_metrics kwargs + 스냅샷 저장 | 중 | S1 (module-2) |
| M7 | reddit_backtester.py | RedditTradeRecord/Result + 인메모리 history + 지표 + 비교표 + ranking 검증 | 높음 | S1 (module-3) |
| M8 | main.py | --sizing/--ranking choices 확장 | 낮음 | S1 (module-3) |
| M9 | tests/test_opinion_trend_sizing.py | FR-16 테스트 | 중 | S1 (module-3) |
| M10 | 검증 | 두 백테스트 명령 + pytest 동작, equal 회귀 확인 | 중 | S1 (module-3) |

**검증 명령(Step 9)**
```
python main.py --backtest --source reddit --model finbert-wsb --ranking sentiment --sizing opinion_trend --from YYYY-MM-DD --to YYYY-MM-DD
python main.py --backtest --source reddit --model finbert-wsb --ranking sentiment --sizing equal --from YYYY-MM-DD --to YYYY-MM-DD
pytest tests/test_opinion_trend_sizing.py
```

---

## 10. 참고

- 관련 기존 피처: `sentiment-driven-sizing.plan.md`(뉴스 경로 sizing — 본 피처는 Reddit 경로), `wsb-signal-v3`(신호 엔진).
- 핵심 코드: `wsb_signal_engine.py`(run_pipeline/check_exit), `position_sizer.py`(get_sizer), `reddit_portfolio.py`(process_day), `wsb_state.py`(mention/position_scores), `reddit_backtester.py`(replay).
- 제약 재확인: 급등추격 금지 · NEW_SPIKE 보수적 · velocity=관심도 · 고정익절 OFF · 5단계 청산 유지 · backtester.py/signals.py 불가침 · equal 회귀 0.

---

## 11. Brainstorming Log (plan-plus 결정 기록)

| Phase | 결정 | 이유 |
|-------|------|------|
| 1 (Intent) | 의도 = 여론 트렌드 기반 사이징/리스크 (급등추격 X) | 사용자 상세 스펙에서 명확 → 추가 질문 생략 |
| 2 (Alternatives) | **Approach A** (Sizer중심·backtester 오케스트레이션) | 확정 D2(인메모리)·D7(mode-gating)과 정합, equal 회귀 위험 최소 |
| 3 (YAGNI) | ①equity/MDD/profit_factor ②exposure/turnover ③의견변화 진단 = v1 포함 / ④gpt5 비교 = 연기 | 리스크·핵심가설 지표는 유지, gpt5는 API 비용으로 후순위 |
| 4 (Incremental) | 아키텍처/컴포넌트/데이터흐름 승인 | 사용자 "승인 — 문서 생성" |
