# Design Ref: §2.5 — WSBSignalEngine: Consensus/30MA/Ranking 파이프라인 + exit logic
# Plan SC FR-08: Consensus 필터(1.5배), 30MA 필터, Ranking(mentions/ratio), Top N 선정
# Plan SC FR-09: 청산 조건 우선순위 — Stop-Loss → Trailing Stop → 컨센서스 반전 → 30MA → 수익 조건
import logging
from datetime import date, datetime

import pandas as pd

import config
from sentiment_provider import SentimentProvider

logger = logging.getLogger(__name__)


class WSBSignalEngine:
    """
    Reddit 게시글 → Top N 종목 선정 전체 파이프라인.

    단계:
      1. _score_posts(): 종목별 감성 점수 + bullish/bearish count
      2. _filter_consensus(): bullish/bearish >= 1.5 필터
      3. _filter_ma30(): 전일 종가 < 30MA 진입 필터
      4. _rank(): mentions 또는 ratio 기준 정렬 → TOP_N 선정
    """

    def __init__(self, provider: SentimentProvider, ranking: str = "mentions"):
        """
        Args:
            provider: FinBERTProvider | GPTProvider
            ranking: "mentions" | "ratio"
        """
        self.provider = provider
        self.ranking = ranking

    def run_pipeline(
        self,
        posts_by_symbol: dict[str, list[dict]],
        ohlcv_cache: dict[str, pd.DataFrame],
        date_str: str = None,
    ) -> tuple[list[str], list[dict]]:
        """
        완전한 파이프라인 실행.

        Returns:
            (top_n_symbols, signal_details)
            signal_details: 각 종목별 처리 결과 dict
        """
        if not posts_by_symbol:
            logger.warning("WSBSignalEngine: 입력 게시글 없음")
            return [], []

        scored = self._score_posts(posts_by_symbol)
        passed_consensus = self._filter_consensus(scored)
        passed_ma = self._filter_ma30(passed_consensus, ohlcv_cache)
        top_n = self._rank(passed_ma, scored)

        signal_details = []
        for symbol, data in scored.items():
            ohlcv = ohlcv_cache.get(symbol)
            ma30 = None
            if ohlcv is not None and not ohlcv.empty:
                import indicators
                ma30 = indicators.get_ma(ohlcv, config.MA_ENTRY_PERIOD)
            prev_close = float(ohlcv["close"].iloc[-1]) if ohlcv is not None and not ohlcv.empty else None

            signal_details.append({
                "symbol": symbol,
                "bullish": data["bullish"],
                "bearish": data["bearish"],
                "neutral": data["neutral"],
                "ratio": data["ratio"],
                "mentions": data["mentions"],
                "ma30": ma30,
                "prev_close": prev_close,
                "passed_consensus": symbol in passed_consensus,
                "passed_ma": symbol in passed_ma,
                "in_top_n": symbol in top_n,
                "rank": top_n.index(symbol) + 1 if symbol in top_n else None,
            })

        logger.info(
            f"WSBSignalEngine 결과: 입력={len(posts_by_symbol)}, "
            f"컨센서스통과={len(passed_consensus)}, "
            f"30MA통과={len(passed_ma)}, "
            f"Top{config.TOP_N}={top_n}"
        )
        return top_n, signal_details

    def _score_posts(
        self,
        posts_by_symbol: dict[str, list[dict]],
    ) -> dict[str, dict]:
        """
        종목별 감성 점수 + bullish/bearish/neutral count 계산.

        Returns:
            {"NVDA": {"bullish": 5, "bearish": 2, "neutral": 3,
                      "ratio": 0.71, "mentions": 7, "score": 71.4}}
        """
        result = {}
        for symbol, posts in posts_by_symbol.items():
            try:
                score, details = self.provider.score(posts)
            except Exception as e:
                logger.warning(f"[{symbol}] 감성 분석 실패: {e} — 중립 처리")
                score, details = 50.0, []

            # bullish/bearish/neutral count (GPTProvider label 사용)
            bullish = sum(1 for d in details if d.get("label") == "bullish"
                          or d.get("finbert_label") == "positive")
            bearish = sum(1 for d in details if d.get("label") == "bearish"
                          or d.get("finbert_label") == "negative")
            neutral = len(details) - bullish - bearish
            total = bullish + bearish
            ratio = bullish / total if total > 0 else 0.0

            result[symbol] = {
                "bullish": bullish,
                "bearish": bearish,
                "neutral": neutral,
                "ratio": round(ratio, 4),
                "mentions": len(posts),
                "score": score,
            }
            logger.debug(
                f"[{symbol}] bullish={bullish}, bearish={bearish},"
                f" ratio={ratio:.2f}, score={score:.1f}"
            )
        return result

    def _filter_consensus(self, scored: dict[str, dict]) -> list[str]:
        """
        WSB_CONSENSUS_RATIO(1.5) 기준 필터.
        # Plan SC FR-08: bullish/bearish >= 1.5 또는 bearish=0이면 bullish >= 2

        Returns: 통과한 symbol 리스트
        """
        passed = []
        for symbol, data in scored.items():
            b = data["bullish"]
            br = data["bearish"]
            if br == 0:
                ok = b >= 2
            else:
                ok = (b / br) >= config.WSB_CONSENSUS_RATIO
            if ok:
                passed.append(symbol)
            else:
                logger.debug(
                    f"[{symbol}] 컨센서스 미통과: bullish={b}, bearish={br}"
                )
        return passed

    def _filter_ma30(
        self,
        symbols: list[str],
        ohlcv_cache: dict[str, pd.DataFrame],
    ) -> list[str]:
        """
        30MA 진입 필터: prev_close < MA30 → 통과.
        # Plan SC FR-08: 이미 급등 종목 추격 매수 방지

        MA30 계산 실패 시 통과 (데이터 부족은 보수적 허용).
        """
        import indicators
        passed = []
        for symbol in symbols:
            ohlcv = ohlcv_cache.get(symbol)
            if ohlcv is None or ohlcv.empty or len(ohlcv) < 2:
                logger.debug(f"[{symbol}] OHLCV 없음 — 30MA 필터 통과(보수적)")
                passed.append(symbol)
                continue

            ma30 = indicators.get_ma(ohlcv, config.MA_ENTRY_PERIOD)
            prev_close = float(ohlcv["close"].iloc[-1])

            if ma30 is None:
                logger.debug(f"[{symbol}] MA30 계산 불가 — 통과(보수적)")
                passed.append(symbol)
            elif prev_close < ma30:
                passed.append(symbol)
                logger.debug(f"[{symbol}] 30MA 통과: prev_close={prev_close:.2f} < ma30={ma30:.2f}")
            else:
                logger.debug(
                    f"[{symbol}] 30MA 미통과: prev_close={prev_close:.2f} >= ma30={ma30:.2f}"
                )
        return passed

    def _rank(self, symbols: list[str], scored: dict) -> list[str]:
        """
        ranking="mentions": 총 게시글 수 내림차순
        ranking="ratio":    bullish/(bullish+bearish) 내림차순
        Returns: TOP_N개 symbol 리스트 (없으면 빈 리스트)
        """
        if not symbols:
            return []

        if self.ranking == "mentions":
            sorted_syms = sorted(
                symbols,
                key=lambda s: scored[s]["mentions"],
                reverse=True,
            )
        else:  # "ratio"
            sorted_syms = sorted(
                symbols,
                key=lambda s: scored[s]["ratio"],
                reverse=True,
            )

        return sorted_syms[: config.TOP_N]

    def check_exit(
        self,
        position: dict,
        today_ohlcv: dict,
        scored: dict[str, dict],
        ohlcv_cache: dict[str, pd.DataFrame],
        holding_days: int = 0,
    ) -> tuple[bool, str]:
        """
        보유 포지션 청산 조건 체크 (우선순위순).
        # Design Ref: §3.2 — 청산 우선순위
        # Plan SC FR-09: Stop-Loss → Trailing Stop → 컨센서스 반전 → 30MA → 수익 조건

        Args:
            position: {"symbol", "entry_price", "highest_price", "shares"}
            today_ohlcv: {"close": float, "prev_close": float}
            scored: 오늘 Reddit 감성 점수 (없으면 컨센서스 반전 체크 불가)
            ohlcv_cache: OHLCV DataFrame (30MA 계산용)
            holding_days: 보유 일수

        Returns:
            (should_exit, reason)
        """
        symbol = position["symbol"]
        entry_price = position["entry_price"]
        highest_price = position["highest_price"]
        close = today_ohlcv.get("close")

        if close is None or entry_price <= 0:
            return False, ""

        pnl_pct = (close - entry_price) / entry_price * 100
        drawdown = (close - highest_price) / highest_price * 100 if highest_price > 0 else 0.0

        # 1. Stop-Loss: pnl <= -7.0%
        if pnl_pct <= config.STOP_LOSS_PCT:
            logger.info(
                f"[{symbol}] Stop-Loss 발동: pnl={pnl_pct:.2f}%"
                f" <= {config.STOP_LOSS_PCT}%"
            )
            return True, "stop_loss"

        # 2. Trailing Stop: 최고점 대비 -5% AND 현재 수익 > 0%
        if drawdown <= config.TRAILING_STOP_PCT and pnl_pct > 0:
            logger.info(
                f"[{symbol}] Trailing Stop: drawdown={drawdown:.2f}%"
                f" <= {config.TRAILING_STOP_PCT}%, pnl={pnl_pct:.2f}%"
            )
            return True, "trailing_stop"

        # 3. 컨센서스 반전: bearish > bullish × 1.5
        sym_data = scored.get(symbol)
        if sym_data:
            b, br = sym_data["bullish"], sym_data["bearish"]
            if br > b * config.WSB_SELL_RATIO:
                logger.info(
                    f"[{symbol}] 컨센서스 반전: bearish={br} > bullish={b} × {config.WSB_SELL_RATIO}"
                )
                return True, "consensus_reversal"

        # 4. 30MA 하향 돌파: 종가 < 30MA AND 보유 >= MA_BREAKDOWN_GRACE_DAYS(5일)
        if holding_days >= config.MA_BREAKDOWN_GRACE_DAYS:
            ohlcv = ohlcv_cache.get(symbol)
            if ohlcv is not None and not ohlcv.empty:
                import indicators
                ma30 = indicators.get_ma(ohlcv, config.MA_ENTRY_PERIOD)
                if ma30 is not None and close < ma30:
                    logger.info(
                        f"[{symbol}] 30MA 하향 돌파: close={close:.2f} < ma30={ma30:.2f}"
                        f" (보유 {holding_days}일)"
                    )
                    return True, "ma30_breakdown"

        # 5. 수익 조건: NEUTRAL + 순수익 > 1%
        if pnl_pct > 1.0 and sym_data:
            score = sym_data.get("score", 50.0)
            if config.SENTIMENT_NEUTRAL_LOW <= score <= config.SENTIMENT_NEUTRAL_HIGH:
                logger.info(
                    f"[{symbol}] 수익 조건 청산: pnl={pnl_pct:.2f}%, score={score:.1f} (NEUTRAL)"
                )
                return True, "profit_take"

        return False, ""
