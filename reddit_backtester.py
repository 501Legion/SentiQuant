# Design Ref: §2.8 — RedditReplayBacktester: data/reddit/YYYY-MM-DD/ replay
# Plan SC FR-21: --source reddit --from DATE --to DATE replay 백테스팅
# Plan SC NFR-06: 유효 거래일 < REDDIT_BACKTEST_MIN_DAYS 시 경고
# Plan SC NFR-07: 수수료 RedditPortfolio._calc_commission()으로 처리
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import config
import indicators
import wsb_state
from backtester import (
    BacktestResult,
    TradeRecord,
    _summarize_trades,
    _calc_total_return,
    _calc_win_rate,
    _calc_mdd,
    _get_ohlcv_snapshot,
)
from position_sizer import get_sizer
from reddit_collector import RedditCollector
from reddit_portfolio import RedditPortfolio
from wsb_signal_engine import WSBSignalEngine
from sentiment_provider import get_provider

logger = logging.getLogger(__name__)


# Design Ref: community-opinion-trend-sizing §4.1 — Sizer/check_exit로 전달되는 의견 지표 묶음.
# position_sizer는 순환 import 방지를 위해 이 타입을 duck-typing으로 소비한다.
@dataclass
class OpinionMetrics:
    opinion_score: float          # = signal_details["score"] (0~100, 기존 sentiment score)
    sentiment_trend: str          # "UP" | "FLAT" | "DOWN"
    persistence_days: int         # bullish>bearish 연속 유지 일수
    consensus_ratio: float        # bullish/bearish (bearish=0 → strong 처리)
    neutral_ratio: float
    velocity_state: str           # 관심도 변화 (NEW_SPIKE/HIGH_MOMENTUM/NORMAL/DECLINING/NEW_IGNORE)
    atr: float | None = None
    prev_close: float | None = None


# Design Ref: community-opinion-trend-sizing §4.2 — Reddit 전용 (backtester.py 불가침)
@dataclass
class RedditTradeRecord:
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    shares: int
    dollar_pnl: float
    pnl_pct: float
    holding_days: int
    exit_reason: str
    size_factor: float = 1.0
    entry_score: float = 0.0
    exit_score: float = 0.0
    score_change: float = 0.0
    entry_consensus_ratio: float = 0.0
    exit_consensus_ratio: float = 0.0
    consensus_change: float = 0.0
    entry_neutral_ratio: float = 0.0
    exit_neutral_ratio: float = 0.0
    neutral_ratio_change: float = 0.0
    entry_velocity_state: str = ""
    exit_velocity_state: str = ""
    opinion_trend_at_entry: str = ""
    opinion_trend_at_exit: str = ""
    persistence_days: int = 0


@dataclass
class RedditBacktestResult:
    strategy_key: str
    final_equity: float
    final_return_pct: float
    max_drawdown: float
    profit_factor: float
    win_rate: float
    total_trades: int
    avg_holding_days: float
    exposure_pct: float
    turnover: float
    equity_curve: list = field(default_factory=list)          # [(date, total_value)]
    exit_reason_dist: dict = field(default_factory=dict)
    avg_entry_score: float = 0.0
    avg_score_change: float = 0.0
    avg_consensus_change: float = 0.0
    avg_neutral_ratio_change: float = 0.0
    trades: list = field(default_factory=list)                # list[RedditTradeRecord]


class RedditReplayBacktester:
    """
    data/reddit/YYYY-MM-DD/ 폴더에 저장된 데이터를 순서대로 읽어 백테스팅 replay.
    실시간 API 호출 없음 — 저장된 wsb_posts.json만 사용.

    사용 예:
        replayer = RedditReplayBacktester(
            model="finbert", ranking="mentions", sizing="equal",
            from_date="2026-04-17", to_date="2026-05-17"
        )
        result = replayer.run()
    """

    def __init__(
        self,
        model: str,           # "finbert" | "gpt5"
        ranking: str,         # "mentions" | "ratio"
        sizing: str,          # "equal" | "sentiment" | "volatility"
        from_date: str,       # "YYYY-MM-DD"
        to_date: str,         # "YYYY-MM-DD"
    ):
        # Plan SC SC-01: finbert-wsb 모델 옵션 추가
        if model not in ("finbert", "finbert-wsb", config.GPT_MODEL_ALIAS):
            raise ValueError(
                f"Reddit 모델은 finbert/finbert-wsb/{config.GPT_MODEL_ALIAS} 지원: {model}"
            )
        if ranking not in ("mentions", "ratio", "sentiment"):
            raise ValueError(f"ranking은 mentions/ratio/sentiment: {ranking}")
        if sizing not in ("equal", "sentiment", "volatility", "opinion_trend"):
            raise ValueError(f"sizing은 equal/sentiment/volatility/opinion_trend: {sizing}")

        self.model = model
        self.ranking = ranking
        self.sizing = sizing
        self.from_date = from_date
        self.to_date = to_date
        self.strategy_key = f"{model}_{ranking}_{sizing}"

    def run(self) -> "RedditBacktestResult":
        """
        replay 백테스팅 실행.
        community-opinion-trend-sizing: 인메모리 opinion_history + OpinionMetrics 주입,
        opinion_mode 청산 배선, RedditBacktestResult(지표 확장) 반환.
        """
        dates = RedditCollector.discover_dates(self.from_date, self.to_date)

        if len(dates) < config.REDDIT_BACKTEST_MIN_DAYS:
            logger.warning(
                f"[RedditBacktest] 유효 거래일 {len(dates)}일 < "
                f"최소 {config.REDDIT_BACKTEST_MIN_DAYS}일 — 결과 신뢰도 낮음"
            )
        if not dates:
            logger.error("[RedditBacktest] 데이터 없음 — 빈 결과 반환")
            return self._empty_result()

        provider = get_provider(self.model)
        engine = WSBSignalEngine(provider, ranking=self.ranking)
        sizer = get_sizer(self.sizing)
        portfolio = RedditPortfolio(self.strategy_key)
        is_opinion = (self.sizing == "opinion_trend")

        logger.info(
            f"[RedditBacktest] 시작 — {self.strategy_key}"
            f" ({self.from_date} ~ {self.to_date}, {len(dates)}일, opinion={is_opinion})"
        )

        # 전체 기간 OHLCV 사전 수집 (snapshot 캐시) — replay 첫 실행만 Polygon 호출
        posts_by_date = {d: RedditCollector.load_posts(d) for d in dates}
        all_symbols = {s for posts in posts_by_date.values() for s in posts}
        self._prefetch_ohlcv(all_symbols)

        opinion_history: dict[str, list[dict]] = {}      # 인메모리 (NFR-04: 전역파일 미오염)
        self._metrics_log: dict[tuple, OpinionMetrics] = {}
        equity_curve: list[tuple[str, float]] = []
        invested_days = 0
        processed_days = 0

        for date_str in dates:
            posts_by_symbol = posts_by_date.get(date_str) or {}
            if not posts_by_symbol:
                continue
            df_cache = self._slice_cache(posts_by_symbol.keys(), date_str)
            if not df_cache:
                continue

            top_n, signal_details = engine.run_pipeline(posts_by_symbol, df_cache, date_str)
            scored = {d["symbol"]: d for d in signal_details}
            velocity_map = {d["symbol"]: d.get("velocity_state", "NORMAL") for d in signal_details}
            today_ohlcv = self._today_cache(set(posts_by_symbol) | set(portfolio.positions), date_str)
            atr_cache = self._calc_atr_cache(today_ohlcv)

            # opinion_history 누적 + OpinionMetrics 계산 (인메모리)
            for sym, d in scored.items():
                wsb_state.update_score_entry(opinion_history, sym, {
                    "date": date_str, "score": d["score"],
                    "bullish": d["bullish"], "bearish": d["bearish"],
                    "neutral": d["neutral"], "neutral_ratio": d["neutral_ratio"],
                })
            opinion_metrics: dict[str, OpinionMetrics] = {}
            for sym, d in scored.items():
                hist = opinion_history.get(sym, [])
                om = OpinionMetrics(
                    opinion_score=d["score"],
                    sentiment_trend=wsb_state.compute_sentiment_trend([h["score"] for h in hist]),
                    persistence_days=wsb_state.compute_persistence_days(hist),
                    consensus_ratio=wsb_state.compute_consensus_ratio(d["bullish"], d["bearish"]),
                    neutral_ratio=d["neutral_ratio"],
                    velocity_state=velocity_map.get(sym, "NORMAL"),
                    atr=atr_cache.get(sym),
                    prev_close=today_ohlcv.get(sym, {}).get("prev_close"),
                )
                opinion_metrics[sym] = om
                self._metrics_log[(sym, date_str)] = om

            position_scores = wsb_state.load_position_scores()
            exit_signals = {}
            for symbol in list(portfolio.positions.keys()):
                sym_ohlcv = today_ohlcv.get(symbol, {})
                should_exit, reason = engine.check_exit(
                    position={
                        "symbol": symbol,
                        "entry_price": portfolio.positions[symbol].entry_price,
                        "highest_price": portfolio.positions[symbol].highest_price,
                        "shares": portfolio.positions[symbol].shares,
                    },
                    today_ohlcv=sym_ohlcv,
                    scored=scored,
                    ohlcv_cache=df_cache,
                    position_scores=position_scores,
                    velocity_state=velocity_map.get(symbol, "NORMAL"),
                    opinion_mode=is_opinion,
                    opinion=opinion_metrics.get(symbol),
                )
                if should_exit:
                    exit_signals[symbol] = reason
            wsb_state.save_position_scores(position_scores)

            day_result = portfolio.process_day(
                date_str=date_str,
                top_n=top_n,
                exit_signals=exit_signals,
                ohlcv=today_ohlcv,
                sizer=sizer,
                scored=scored,
                atr_cache=atr_cache,
                position_scores=position_scores,
                opinion_metrics=opinion_metrics,
            )
            equity_curve.append((date_str, day_result["total_value"]))
            processed_days += 1
            if portfolio.positions:
                invested_days += 1

        return self._build_result(portfolio, equity_curve, invested_days, processed_days)

    def _empty_result(self) -> "RedditBacktestResult":
        return RedditBacktestResult(
            strategy_key=self.strategy_key,
            final_equity=config.INITIAL_CASH, final_return_pct=0.0,
            max_drawdown=0.0, profit_factor=0.0, win_rate=0.0,
            total_trades=0, avg_holding_days=0.0, exposure_pct=0.0, turnover=0.0,
        )

    def _build_result(self, portfolio, equity_curve, invested_days, processed_days):
        """trade_log + _metrics_log + equity_curve → RedditBacktestResult."""
        sells = [t for t in portfolio.trade_log if t["type"] == "sell"]
        buys = [t for t in portfolio.trade_log if t["type"] == "buy"]
        date_index = {d: i for i, (d, _) in enumerate(equity_curve)}

        records: list[RedditTradeRecord] = []
        for t in sells:
            sym, ed, xd = t["symbol"], t["entry_date"], t["date"]
            em = self._metrics_log.get((sym, ed))
            xm = self._metrics_log.get((sym, xd)) or em
            e_score = em.opinion_score if em else 0.0
            x_score = xm.opinion_score if xm else e_score
            e_cons = em.consensus_ratio if em else 0.0
            x_cons = xm.consensus_ratio if xm else e_cons
            e_neut = em.neutral_ratio if em else 0.0
            x_neut = xm.neutral_ratio if xm else e_neut
            holding = (date_index.get(xd, 0) - date_index.get(ed, 0)) \
                if (ed in date_index and xd in date_index) else 0
            records.append(RedditTradeRecord(
                symbol=sym, entry_date=ed, exit_date=xd,
                entry_price=t.get("entry_price", 0.0), exit_price=t.get("price", 0.0),
                shares=t.get("shares", 0), dollar_pnl=t.get("net_pnl", 0.0),
                pnl_pct=t.get("pnl_pct", 0.0), holding_days=holding,
                exit_reason=t.get("reason", ""), size_factor=t.get("size_factor", 1.0),
                entry_score=round(e_score, 1), exit_score=round(x_score, 1),
                score_change=round(x_score - e_score, 2),
                entry_consensus_ratio=round(e_cons, 3), exit_consensus_ratio=round(x_cons, 3),
                consensus_change=round(x_cons - e_cons, 3),
                entry_neutral_ratio=round(e_neut, 3), exit_neutral_ratio=round(x_neut, 3),
                neutral_ratio_change=round(x_neut - e_neut, 3),
                entry_velocity_state=(em.velocity_state if em else ""),
                exit_velocity_state=(xm.velocity_state if xm else ""),
                opinion_trend_at_entry=(em.sentiment_trend if em else ""),
                opinion_trend_at_exit=(xm.sentiment_trend if xm else ""),
                persistence_days=(em.persistence_days if em else 0),
            ))

        equities = [v for _, v in equity_curve]
        final_equity = equities[-1] if equities else config.INITIAL_CASH
        final_return_pct = round((final_equity - config.INITIAL_CASH) / config.INITIAL_CASH * 100, 2)
        n = len(records)
        wins = [r for r in records if r.dollar_pnl > 0]
        win_rate = round(len(wins) / n * 100, 1) if n else 0.0
        avg_holding = round(sum(r.holding_days for r in records) / n, 1) if n else 0.0
        exposure_pct = round(invested_days / processed_days * 100, 1) if processed_days else 0.0
        turnover = round(sum(b.get("trade_value", 0.0) for b in buys) / config.INITIAL_CASH, 2)
        avg_entry_score = round(sum(r.entry_score for r in records) / n, 1) if n else 0.0
        avg_score_change = round(sum(r.score_change for r in records) / n, 2) if n else 0.0
        avg_cons_change = round(sum(r.consensus_change for r in records) / n, 3) if n else 0.0
        avg_neut_change = round(sum(r.neutral_ratio_change for r in records) / n, 3) if n else 0.0

        result = RedditBacktestResult(
            strategy_key=self.strategy_key,
            final_equity=round(final_equity, 2), final_return_pct=final_return_pct,
            max_drawdown=self._max_drawdown(equities), profit_factor=self._profit_factor(records),
            win_rate=win_rate, total_trades=n, avg_holding_days=avg_holding,
            exposure_pct=exposure_pct, turnover=turnover,
            equity_curve=equity_curve, exit_reason_dist=dict(Counter(r.exit_reason for r in records)),
            avg_entry_score=avg_entry_score, avg_score_change=avg_score_change,
            avg_consensus_change=avg_cons_change, avg_neutral_ratio_change=avg_neut_change,
            trades=records,
        )
        logger.info(
            f"[RedditBacktest] 완료 — {self.strategy_key} | 수익률={final_return_pct:+.1f}%"
            f" | 거래={n}회 | 승률={win_rate:.1f}% | MDD={result.max_drawdown:.1f}%"
            f" | PF={result.profit_factor:.2f}"
        )
        return result

    @staticmethod
    def _max_drawdown(equities: list) -> float:
        if not equities:
            return 0.0
        peak = equities[0]
        mdd = 0.0
        for v in equities:
            peak = max(peak, v)
            if peak > 0:
                mdd = min(mdd, (v - peak) / peak * 100)
        return round(mdd, 2)

    @staticmethod
    def _profit_factor(records) -> float:
        gains = sum(r.dollar_pnl for r in records if r.dollar_pnl > 0)
        losses = -sum(r.dollar_pnl for r in records if r.dollar_pnl < 0)
        if losses <= 0:
            return 99.99 if gains > 0 else 0.0   # 손실 없음 (sentinel)
        return round(gains / losses, 2)

    def _prefetch_ohlcv(self, symbols: set[str]) -> None:
        """
        전략 universe 종목의 OHLCV를 from_date-100일 ~ to_date 범위로 1회 수집.
        backtester._get_ohlcv_snapshot 재사용 → CSV 스냅샷 캐시(재실행 시 오프라인).
        RSI 계산 버퍼 확보를 위해 시작일을 100일 앞당긴다.
        무료 플랜 rate limit 대응: 캐시 미스(실제 API 호출) 때만 throttle.
        """
        import os
        import time
        from backtester import _snapshot_path

        start_dt = datetime.strptime(self.from_date, "%Y-%m-%d")
        ohlcv_start = (start_dt - timedelta(days=100)).strftime("%Y-%m-%d")

        self._ohlcv_full: dict[str, "pd.DataFrame"] = {}
        for symbol in sorted(symbols):
            snap = _snapshot_path("ohlcv", f"{symbol}_{ohlcv_start}_{self.to_date}.csv")
            cached = os.path.exists(snap)
            try:
                df = _get_ohlcv_snapshot(symbol, ohlcv_start, self.to_date)
            except Exception as e:
                logger.warning(f"[{symbol}] OHLCV 수집 실패: {e} — 제외")
                df = None
            if df is not None and not df.empty:
                self._ohlcv_full[symbol] = df
            if not cached:
                time.sleep(config.REDDIT_BACKTEST_FETCH_THROTTLE)

    def _slice_cache(self, symbols, date_str: str) -> dict:
        """run_pipeline용: 종목별 date_str 이하 OHLCV DataFrame 슬라이스."""
        cache = {}
        for symbol in symbols:
            full = self._ohlcv_full.get(symbol)
            if full is None or full.empty:
                continue
            sliced = full[full["date"] <= date_str]
            if not sliced.empty:
                cache[symbol] = sliced
        return cache

    def _today_cache(self, symbols, date_str: str) -> dict[str, dict]:
        """
        process_day/check_exit용: date_str 당일 스칼라 OHLCV.
        date_str가 거래일이 아니면(주말/휴장) 해당 종목 제외 → 당일 체결 없음.
        prev_close는 직전 거래일 종가, rsi는 date_str 까지의 슬라이스로 계산.
        """
        cache: dict[str, dict] = {}
        for symbol in symbols:
            full = self._ohlcv_full.get(symbol)
            if full is None or full.empty:
                continue
            sliced = full[full["date"] <= date_str].reset_index(drop=True)
            if sliced.empty:
                continue
            last = sliced.iloc[-1]
            if last["date"] != date_str:
                continue  # date_str는 거래일 아님 — 당일 체결 데이터 없음
            prev_close = (
                float(sliced.iloc[-2]["close"]) if len(sliced) >= 2 else None
            )
            rsi, _ = indicators.get_latest_rsi(symbol, sliced)
            cache[symbol] = {
                "open": float(last["open"]),
                "close": float(last["close"]),
                "prev_close": prev_close,
                "rsi": rsi,
            }
        return cache

    def _calc_atr_cache(self, ohlcv_cache: dict) -> dict[str, float]:
        """
        VolatilitySizer용 ATR. replay 시 미산출 — Equal/Sentiment sizing 권장.
        """
        return {}


def run_all_reddit_strategies(
    from_date: str,
    to_date: str,
) -> dict[str, "RedditBacktestResult"]:
    """
    Reddit 전략 sweep (2모델 × 2랭킹 × 3sizing) 순차 실행 (--report-reddit).
    Returns: {strategy_key: RedditBacktestResult}
    """
    results = {}
    for model in ("finbert", config.GPT_MODEL_ALIAS):
        for ranking in ("mentions", "ratio"):
            for sizing in ("equal", "sentiment", "volatility"):
                key = f"{model}_{ranking}_{sizing}"
                logger.info(f"[RedditBacktest] 전략 {key} 시작...")
                replayer = RedditReplayBacktester(
                    model=model, ranking=ranking, sizing=sizing,
                    from_date=from_date, to_date=to_date,
                )
                results[key] = replayer.run()
    return results


def print_reddit_comparison(results: dict[str, "RedditBacktestResult"]) -> None:
    """
    Reddit 전략 비교 출력 (community-opinion-trend-sizing §7 / Plan FR-15).
    의견 변화·청산 사유 분포 포함.
    """
    if not results:
        print("Reddit 백테스팅 결과 없음")
        return

    print(f"\n{'='*92}")
    print("  Community Opinion Trend - 전략 비교")
    print(f"{'='*92}")
    print(
        f"  {'strategy':<34} | {'ret%':>7} | {'MDD%':>6} | {'PF':>5}"
        f" | {'win%':>5} | {'trades':>6} | {'hold':>4} | {'entryS':>6} | {'dScore':>6} | {'dCons':>6}"
    )
    print(f"  {'-'*88}")
    for key, r in sorted(results.items()):
        sign = "+" if r.final_return_pct >= 0 else ""
        print(
            f"  {key:<34} | {sign}{r.final_return_pct:>6.1f} | {r.max_drawdown:>6.1f}"
            f" | {r.profit_factor:>5.2f} | {r.win_rate:>5.1f} | {r.total_trades:>6}"
            f" | {r.avg_holding_days:>4.1f} | {r.avg_entry_score:>6.1f}"
            f" | {r.avg_score_change:>+5.1f} | {r.avg_consensus_change:>+6.2f}"
        )

    print(f"\n  청산 사유 분포 (exit_reason_dist):")
    for key, r in sorted(results.items()):
        dist = r.exit_reason_dist or {}
        parts = ", ".join(f"{k}={v}" for k, v in sorted(dist.items())) or "(거래 없음)"
        print(f"    {key:<34} | {parts}")
    print(f"{'='*92}\n")
