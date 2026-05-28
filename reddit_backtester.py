# Design Ref: §2.8 — RedditReplayBacktester: data/reddit/YYYY-MM-DD/ replay
# Plan SC FR-21: --source reddit --from DATE --to DATE replay 백테스팅
# Plan SC NFR-06: 유효 거래일 < REDDIT_BACKTEST_MIN_DAYS 시 경고
# Plan SC NFR-07: 수수료 RedditPortfolio._calc_commission()으로 처리
import logging
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
        if ranking not in ("mentions", "ratio"):
            raise ValueError(f"ranking은 mentions/ratio: {ranking}")
        if sizing not in ("equal", "sentiment", "volatility"):
            raise ValueError(f"sizing은 equal/sentiment/volatility: {sizing}")

        self.model = model
        self.ranking = ranking
        self.sizing = sizing
        self.from_date = from_date
        self.to_date = to_date
        self.strategy_key = f"{model}_{ranking}_{sizing}"

    def run(self) -> BacktestResult:
        """
        replay 백테스팅 실행.

        1. 날짜 폴더 목록 수집
        2. NFR-06: 유효 날짜 < REDDIT_BACKTEST_MIN_DAYS → 경고
        3. 날짜별 wsb_posts.json 로드 → WSBSignalEngine → RedditPortfolio
        4. commission 포함 BacktestResult 반환
        """
        dates = RedditCollector.discover_dates(self.from_date, self.to_date)

        if len(dates) < config.REDDIT_BACKTEST_MIN_DAYS:
            logger.warning(
                f"[RedditBacktest] 유효 거래일 {len(dates)}일 < "
                f"최소 {config.REDDIT_BACKTEST_MIN_DAYS}일 — "
                f"결과 신뢰도 낮음"
            )

        if not dates:
            logger.error("[RedditBacktest] 데이터 없음 — 빈 결과 반환")
            return BacktestResult(
                model=self.strategy_key,
                total_return_pct=0.0,
                trade_count=0,
                win_rate_pct=0.0,
                mdd_pct=0.0,
                per_symbol={},
            )

        provider = get_provider(self.model)
        engine = WSBSignalEngine(provider, ranking=self.ranking)
        sizer = get_sizer(self.sizing)
        portfolio = RedditPortfolio(self.strategy_key)

        logger.info(
            f"[RedditBacktest] 시작 — {self.strategy_key}"
            f" ({self.from_date} ~ {self.to_date}, {len(dates)}일)"
        )

        # 전체 기간 OHLCV 사전 수집 (snapshot 캐시) — replay 첫 실행만 Polygon 호출
        posts_by_date = {d: RedditCollector.load_posts(d) for d in dates}
        all_symbols = {s for posts in posts_by_date.values() for s in posts}
        self._prefetch_ohlcv(all_symbols)

        for date_str in dates:
            posts_by_symbol = posts_by_date.get(date_str) or {}
            if not posts_by_symbol:
                logger.debug(f"[{date_str}] wsb_posts.json 없음 — 스킵")
                continue

            # RSI/신호용: 종목별 date_str 이하 OHLCV DataFrame 슬라이스
            df_cache = self._slice_cache(posts_by_symbol.keys(), date_str)
            if not df_cache:
                logger.debug(f"[{date_str}] OHLCV 없음 — 스킵")
                continue

            # 파이프라인: top_n 계산
            top_n, signal_details = engine.run_pipeline(
                posts_by_symbol, df_cache, date_str
            )

            # 청산 신호 계산 — signal_details에서 scored + velocity_state 추출
            scored = {d["symbol"]: d for d in signal_details}
            velocity_map = {
                d["symbol"]: d.get("velocity_state", "NORMAL")
                for d in signal_details
            }

            # 체결/청산용: date_str 당일 스칼라 OHLCV (보유 종목 포함, 거래일만)
            exec_symbols = set(posts_by_symbol) | set(portfolio.positions)
            today_ohlcv = self._today_cache(exec_symbols, date_str)

            # Design Ref: §wsb-signal-v3 §3.5 — position_scores 로드 후 check_exit 전달
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
                )
                if should_exit:
                    exit_signals[symbol] = reason

            wsb_state.save_position_scores(position_scores)

            # ATR cache for VolatilitySizer
            atr_cache = self._calc_atr_cache(today_ohlcv)

            portfolio.process_day(
                date_str=date_str,
                top_n=top_n,
                exit_signals=exit_signals,
                ohlcv=today_ohlcv,
                sizer=sizer,
                scored=scored,
                atr_cache=atr_cache,
                position_scores=position_scores,
            )

        # BacktestResult 변환 (portfolio.trade_log 기반)
        summary = portfolio.get_summary()
        sell_trades = [t for t in portfolio.trade_log if t["type"] == "sell"]

        # TradeRecord 형식으로 변환 (기존 print_comparison과 호환)
        trade_records = [
            TradeRecord(
                symbol=t["symbol"],
                entry_date=t["entry_date"],
                entry_price=t["entry_price"],
                exit_date=t["date"],
                exit_price=t["price"],
                pnl_pct=t["pnl_pct"],
                is_win=t["net_pnl"] > 0,
            )
            for t in sell_trades
        ]

        # 종목별 집계
        per_symbol: dict[str, list] = {}
        for tr in trade_records:
            per_symbol.setdefault(tr.symbol, []).append(tr)
        per_symbol_summary = {s: _summarize_trades(trs) for s, trs in per_symbol.items()}

        logger.info(
            f"[RedditBacktest] 완료 — {self.strategy_key}"
            f" | 수익률={summary['total_return_pct']:+.1f}%"
            f" | 거래={summary['total_trades']}회"
            f" | 승률={summary['win_rate']:.1f}%"
            f" | MDD={summary['mdd_pct']:.1f}%"
        )

        return BacktestResult(
            model=self.strategy_key,
            total_return_pct=summary["total_return_pct"],
            trade_count=summary["total_trades"],
            win_rate_pct=summary["win_rate"],
            mdd_pct=summary["mdd_pct"],
            per_symbol=per_symbol_summary,
            trades=trade_records,
        )

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
) -> dict[str, BacktestResult]:
    """
    12가지 Reddit 전략 (2모델 × 2랭킹 × 3sizing) 순차 실행.
    # Plan SC: 전략별 수익률 비교 출력 기반 데이터

    Returns: {strategy_key: BacktestResult}
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


def print_reddit_comparison(results: dict[str, BacktestResult]) -> None:
    """Reddit 12가지 전략 비교 출력."""
    if not results:
        print("Reddit 백테스팅 결과 없음")
        return

    print(f"\n{'='*65}")
    print(f"  Reddit Forward Testing 결과")
    print(f"{'='*65}")

    for key, result in sorted(results.items()):
        sign = "+" if result.total_return_pct >= 0 else ""
        print(
            f"  {key:<35}"
            f" | 수익률: {sign}{result.total_return_pct:.1f}%"
            f" | 거래: {result.trade_count:>3}회"
            f" | 승률: {result.win_rate_pct:.1f}%"
            f" | MDD: {result.mdd_pct:.1f}%"
        )

    print(f"{'='*65}\n")
