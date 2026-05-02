# Design Ref: §2.8 — RedditReplayBacktester: data/reddit/YYYY-MM-DD/ replay
# Plan SC FR-21: --source reddit --from DATE --to DATE replay 백테스팅
# Plan SC NFR-06: 유효 거래일 < REDDIT_BACKTEST_MIN_DAYS 시 경고
# Plan SC NFR-07: 수수료 RedditPortfolio._calc_commission()으로 처리
import logging

import config
import wsb_state
from backtester import BacktestResult, TradeRecord, _summarize_trades, _calc_total_return, _calc_win_rate, _calc_mdd
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

        for date_str in dates:
            posts_by_symbol = RedditCollector.load_posts(date_str)
            if not posts_by_symbol:
                logger.debug(f"[{date_str}] wsb_posts.json 없음 — 스킵")
                continue

            # OHLCV: 이미 수집된 데이터 재사용 (replay이므로 API 호출 불필요)
            # wsb_signals.json에서 저장된 OHLCV를 읽거나, Polygon 호출 최소화
            ohlcv_cache = self._load_ohlcv_for_replay(posts_by_symbol, date_str)
            if not ohlcv_cache:
                logger.debug(f"[{date_str}] OHLCV 없음 — 스킵")
                continue

            # 파이프라인: top_n 계산
            top_n, signal_details = engine.run_pipeline(
                posts_by_symbol, ohlcv_cache, date_str
            )

            # 청산 신호 계산 — signal_details에서 scored + velocity_state 추출
            scored = {d["symbol"]: d for d in signal_details}
            velocity_map = {
                d["symbol"]: d.get("velocity_state", "NORMAL")
                for d in signal_details
            }

            # Design Ref: §wsb-signal-v3 §3.5 — position_scores 로드 후 check_exit 전달
            position_scores = wsb_state.load_position_scores()

            exit_signals = {}
            for symbol in list(portfolio.positions.keys()):
                sym_ohlcv = ohlcv_cache.get(symbol, {})
                should_exit, reason = engine.check_exit(
                    position={
                        "symbol": symbol,
                        "entry_price": portfolio.positions[symbol].entry_price,
                        "highest_price": portfolio.positions[symbol].highest_price,
                        "shares": portfolio.positions[symbol].shares,
                    },
                    today_ohlcv=sym_ohlcv,
                    scored=scored,
                    ohlcv_cache=ohlcv_cache,
                    position_scores=position_scores,
                    velocity_state=velocity_map.get(symbol, "NORMAL"),
                )
                if should_exit:
                    exit_signals[symbol] = reason

            wsb_state.save_position_scores(position_scores)

            # ATR cache for VolatilitySizer
            atr_cache = self._calc_atr_cache(ohlcv_cache)

            portfolio.process_day(
                date_str=date_str,
                top_n=top_n,
                exit_signals=exit_signals,
                ohlcv=ohlcv_cache,
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

    def _load_ohlcv_for_replay(
        self,
        posts_by_symbol: dict,
        date_str: str,
    ) -> dict[str, dict]:
        """
        Replay용 OHLCV 로드.
        wsb_signals.json에 저장된 OHLCV 사용. 없으면 빈 dict 반환.
        실제 실행 시 collector.get_ohlcv()를 호출할 수 있으나
        replay 의도에 맞게 저장 데이터 우선 사용.
        """
        import json, os

        signals_file = os.path.join(
            config.REDDIT_DATA_DIR, date_str, "wsb_signals.json"
        )
        ohlcv = {}

        if os.path.exists(signals_file):
            try:
                with open(signals_file, "r", encoding="utf-8") as f:
                    signals = json.load(f)
                # wsb_signals.json의 signal_details에 prev_close, ma30 저장됨
                for detail in signals.get("signal_details", []):
                    sym = detail["symbol"]
                    ohlcv[sym] = {
                        "open": detail.get("prev_close"),   # replay: open ≈ prev_close
                        "close": detail.get("prev_close"),
                        "prev_close": detail.get("prev_close"),
                    }
            except Exception as e:
                logger.debug(f"[{date_str}] wsb_signals.json 로드 실패: {e}")

        # OHLCV DataFrame도 만들기 (30MA 계산용) — 없으면 pass
        ohlcv_df_cache: dict[str, "pd.DataFrame"] = {}
        return ohlcv  # check_exit에서 ohlcv_cache도 필요 → _calc_atr_cache로 분리

    def _calc_atr_cache(self, ohlcv_cache: dict) -> dict[str, float]:
        """
        VolatilitySizer용 ATR. replay 시 근사값 없으면 빈 dict 반환.
        실시간 모드에서는 collector.get_ohlcv()로 계산.
        """
        return {}  # replay에서는 Equal/Sentiment 사용 권장


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
