# Design Ref: §wsb-signal-v3 §3 — WSBSignalEngine V3: Velocity/NeutralFilter/SignalV3 + 5단계 청산
# Plan SC: SC-01 30MA 제거, SC-02 중립필터, SC-03~SC-11 매수/매도 신호 기준
import logging
from datetime import date, datetime

import pandas as pd

import config
import wsb_state
from sentiment_provider import SentimentProvider

logger = logging.getLogger(__name__)


class WSBSignalEngine:
    """
    Reddit 게시글 → Top N 종목 선정 전체 파이프라인 (V3).

    단계:
      1. _score_posts(): 종목별 감성 점수 + bullish/bearish/neutral count
      2. _apply_neutral_filter(): neutral/total > 0.7 → NEUTRAL 강제
      3. _apply_velocity(): Mention Velocity 계산 → velocity_state
      4. _determine_signal_v3(): 매수 신호 결정 (Velocity 보정 매트릭스)
      5. _filter_consensus(): bullish/bearish >= 1.5 필터
      6. _rank(): mentions 또는 ratio 기준 정렬 → TOP_N 선정
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
        # Design Ref: §wsb-signal-v3 §3.6 — _score → neutral_filter → velocity → signal_v3 → consensus → rank

        Returns:
            (top_n_symbols, signal_details)
            signal_details: 각 종목별 처리 결과 dict
        """
        if not posts_by_symbol:
            logger.warning("WSBSignalEngine: 입력 게시글 없음")
            return [], []

        mention_history = wsb_state.load_mention_history()

        scored = self._score_posts(posts_by_symbol)
        neutral_overrides = self._apply_neutral_filter(scored)

        # velocity + signal_v3 계산 (중립 필터 통과 종목에만 신호 부여)
        velocity_map: dict[str, tuple[float | None, str]] = {}
        signal_map: dict[str, str] = {}
        for symbol, data in scored.items():
            velocity, velocity_state = self._apply_velocity(
                symbol, data["mentions"], mention_history
            )
            velocity_map[symbol] = (velocity, velocity_state)

            if symbol in neutral_overrides:
                signal_map[symbol] = "NEUTRAL"
            else:
                # ohlcv_cache에서 RSI 추출 (없으면 50 중립 폴백)
                ohlcv = ohlcv_cache.get(symbol)
                rsi = 50.0
                if ohlcv is not None and not ohlcv.empty:
                    import indicators
                    rsi_val, _ = indicators.get_latest_rsi(symbol, ohlcv)
                    if rsi_val is not None:
                        rsi = rsi_val
                signal_map[symbol] = self._determine_signal_v3(
                    data["score"], rsi, velocity_state
                )

        # 컨센서스 필터: neutral_filtered + NEUTRAL 신호 종목 제외
        eligible = [
            s for s in scored
            if s not in neutral_overrides and signal_map.get(s) != "NEUTRAL"
        ]
        passed_consensus = self._filter_consensus(
            {s: scored[s] for s in eligible}
        )
        top_n = self._rank(passed_consensus, scored)

        # mention_history 업데이트 및 저장
        for symbol, data in scored.items():
            wsb_state.update_mention_entry(mention_history, symbol, data["mentions"])
        wsb_state.save_mention_history(mention_history)

        signal_details = []
        for symbol, data in scored.items():
            velocity, velocity_state = velocity_map[symbol]
            total = data["bullish"] + data["bearish"] + data["neutral"]
            neutral_ratio = round(data["neutral"] / total, 4) if total > 0 else 0.0
            signal_details.append({
                "symbol": symbol,
                "bullish": data["bullish"],
                "bearish": data["bearish"],
                "neutral": data["neutral"],
                "ratio": data["ratio"],
                "mentions": data["mentions"],
                "score": data["score"],
                "velocity": velocity,
                "velocity_state": velocity_state,
                "neutral_ratio": neutral_ratio,
                "neutral_filtered": symbol in neutral_overrides,
                "signal": signal_map.get(symbol, "NEUTRAL"),
                "passed_consensus": symbol in passed_consensus,
                "in_top_n": symbol in top_n,
                "rank": top_n.index(symbol) + 1 if symbol in top_n else None,
            })

        logger.info(
            f"WSBSignalEngine V3 결과: 입력={len(posts_by_symbol)}, "
            f"중립필터={len(neutral_overrides)}, "
            f"컨센서스통과={len(passed_consensus)}, "
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

    def _apply_neutral_filter(
        self,
        scored: dict[str, dict],
    ) -> dict[str, str]:
        """
        종목별 neutral/total 비율이 WSB_NEUTRAL_RATIO_MAX 초과 시 NEUTRAL 강제.
        # Plan SC-02: neutral/total > 0.7 → NEUTRAL
        # Design Ref: §wsb-signal-v3 §3.2

        Returns:
            {symbol: "NEUTRAL"} — 필터 적용된 종목만 포함. 통과 종목은 제외.
        """
        neutral_overrides: dict[str, str] = {}
        for symbol, data in scored.items():
            total = data["bullish"] + data["bearish"] + data["neutral"]
            if total == 0:
                continue
            neutral_ratio = data["neutral"] / total
            if neutral_ratio > config.WSB_NEUTRAL_RATIO_MAX:
                neutral_overrides[symbol] = "NEUTRAL"
                logger.info(
                    f"[중립 필터] {symbol}: 중립비율={neutral_ratio:.0%} → NEUTRAL"
                )
        return neutral_overrides

    def _apply_velocity(
        self,
        symbol: str,
        today_mentions: int,
        history: dict[str, list[int]],
    ) -> tuple[float | None, str]:
        """
        Mention Velocity 계산 및 velocity_state 반환.
        # Design Ref: §wsb-signal-v3 §3.3
        # Plan SC FR-03: HIGH_MOMENTUM/NORMAL/DECLINING/NEW_SPIKE/NEW_IGNORE

        Returns:
            (velocity, velocity_state)
        """
        past = history.get(symbol, [])

        if not past:
            if today_mentions >= config.WSB_NEW_SPIKE_MIN_MENTIONS:
                return None, "NEW_SPIKE"
            return None, "NEW_IGNORE"

        avg = sum(past) / len(past)
        if avg == 0:
            return None, "NORMAL"
        velocity = today_mentions / avg

        if velocity > config.WSB_VELOCITY_HIGH_THRESHOLD:
            return velocity, "HIGH_MOMENTUM"
        if velocity < config.WSB_VELOCITY_LOW_THRESHOLD:
            return velocity, "DECLINING"
        return velocity, "NORMAL"

    def _determine_signal_v3(
        self,
        score: float,
        rsi: float,
        velocity_state: str,
    ) -> str:
        """
        Velocity 보정 매트릭스 기반 매수 신호 결정.
        # Design Ref: §wsb-signal-v3 §3.4
        # Plan SC FR-04: Velocity 보정 매트릭스

        Returns:
            "STRONG_BUY" | "BUY" | "NEUTRAL"
        """
        adjust = config.WSB_VELOCITY_SCORE_ADJUST  # 5.0

        thresholds = {
            "HIGH_MOMENTUM": (
                config.WSB_STRONG_BUY_SCORE - adjust,  # 65
                config.WSB_BUY_SCORE - adjust,          # 50
            ),
            "NORMAL": (
                config.WSB_STRONG_BUY_SCORE,            # 70
                config.WSB_BUY_SCORE,                   # 55
            ),
            "DECLINING": (
                config.WSB_STRONG_BUY_SCORE + adjust,   # 75
                config.WSB_BUY_SCORE + adjust,           # 60
            ),
            "NEW_SPIKE": (
                config.WSB_NEW_SPIKE_SCORE,             # 65
                config.WSB_BUY_SCORE - adjust,          # 50
            ),
            "NEW_IGNORE": (float("inf"), float("inf")),
        }

        sb_threshold, buy_threshold = thresholds.get(
            velocity_state, thresholds["NORMAL"]
        )

        if score > sb_threshold and rsi < config.RSI_OVERSOLD:        # rsi < 30
            return "STRONG_BUY"
        if score > buy_threshold and config.RSI_OVERSOLD <= rsi < 50:  # 30 ≤ rsi < 50
            return "BUY"
        return "NEUTRAL"

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

    def _rank(self, symbols: list[str], scored: dict) -> list[str]:
        """
        ranking="mentions":  총 게시글 수 내림차순
        ranking="ratio":     bullish/(bullish+bearish) 내림차순
        ranking="sentiment": 감성 score 내림차순 (community-opinion-trend-sizing)
        Returns: TOP_N개 symbol 리스트 (없으면 빈 리스트)
        """
        if not symbols:
            return []

        if self.ranking == "mentions":
            key = lambda s: scored[s]["mentions"]
        elif self.ranking == "sentiment":
            # Design Ref: community-opinion-trend-sizing §4.4 — score 기준 정렬
            key = lambda s: scored[s]["score"]
        else:  # "ratio"
            key = lambda s: scored[s]["ratio"]

        sorted_syms = sorted(symbols, key=key, reverse=True)
        return sorted_syms[: config.TOP_N]

    def check_exit(
        self,
        position: dict,
        today_ohlcv: dict,
        scored: dict[str, dict],
        ohlcv_cache: dict[str, pd.DataFrame],
        position_scores: dict[str, dict],
        velocity_state: str = "NORMAL",
        holding_days: int = 0,
        opinion_mode: bool = False,
        opinion=None,
    ) -> tuple[bool, str]:
        """
        5단계 우선순위 청산 조건 체크.
        # Design Ref: §wsb-signal-v3 §3.5 — 5단계 청산 우선순위
        # Plan SC FR-09~FR-14: 감성역전 → RSI과매수 → Gap Down → Stop-Loss → Trailing Stop

        Args:
            position: {"symbol", "entry_price", "highest_price", "shares"}
            today_ohlcv: {"close", "open", "prev_close", "rsi"}
            scored: 오늘 Reddit 감성 점수
            ohlcv_cache: OHLCV DataFrame (미사용, 하위호환 유지)
            position_scores: wsb_state.load_position_scores() 결과 (in-place 수정됨)
            velocity_state: 오늘 velocity 상태 (RSI 유예 판단용)
            holding_days: 보유 일수 (미사용, 하위호환 유지)

        Returns:
            (should_exit, reason)
            reason: "sentiment_reversal" | "rsi_overbought" | "rsi_hold"
                    | "gap_down" | "stop_loss" | "trailing_stop" | ""
        """
        symbol = position["symbol"]
        entry_price = position["entry_price"]
        highest_price = position["highest_price"]
        close = today_ohlcv.get("close")
        open_price = today_ohlcv.get("open")
        prev_close = today_ohlcv.get("prev_close")

        if close is None or entry_price <= 0:
            return False, ""

        pnl_pct = (close - entry_price) / entry_price * 100
        drawdown = (close - highest_price) / highest_price * 100 if highest_price > 0 else 0.0

        ps = position_scores.get(symbol, {})
        entry_score = ps.get("entry_score")
        sym_scored = scored.get(symbol, {})
        today_score = sym_scored.get("score")

        # 1. 감성/의견 역전
        if opinion_mode and opinion is not None:
            # opinion_reversal (강화) — Design Ref: community-opinion-trend-sizing §6
            _reason = self._opinion_reversal(symbol, opinion, ps, sym_scored)
            if _reason:
                return True, _reason
        elif entry_score is not None and today_score is not None:
            # 기존 sentiment_reversal (opinion_mode=False → 회귀 0)
            reversal_threshold = entry_score * config.WSB_SENTIMENT_REVERSAL_RATIO
            today_below = today_score < reversal_threshold
            if today_below and ps.get("yesterday_below", False):
                logger.info(
                    f"[{symbol}] 감성 역전 청산: score={today_score:.1f}"
                    f" < entry×0.6={reversal_threshold:.1f} (2일 연속)"
                )
                return True, "sentiment_reversal"
            wsb_state.upsert_position_score(
                position_scores, symbol, yesterday_below=today_below
            )

        # 2. RSI 과매수 × Velocity 교차
        rsi = today_ohlcv.get("rsi")
        if rsi is not None and rsi > config.WSB_RSI_EXIT_OVERBOUGHT:
            rsi_held = ps.get("rsi_held", False)
            if not rsi_held and velocity_state == "HIGH_MOMENTUM" and config.WSB_RSI_HOLD_ONCE:
                wsb_state.upsert_position_score(
                    position_scores, symbol, rsi_held=True
                )
                logger.info(
                    f"[{symbol}] RSI 과매수 1회 유예: rsi={rsi:.1f},"
                    f" velocity={velocity_state}"
                )
                return False, "rsi_hold"
            logger.info(
                f"[{symbol}] RSI 과매수 청산: rsi={rsi:.1f}"
                f" > {config.WSB_RSI_EXIT_OVERBOUGHT}"
            )
            return True, "rsi_overbought"

        # 3. Gap Down: 시가 / 전일 종가 <= WSB_GAP_DOWN_PCT(-5%)
        if open_price is not None and prev_close is not None and prev_close > 0:
            gap_pct = (open_price - prev_close) / prev_close * 100
            if gap_pct <= config.WSB_GAP_DOWN_PCT:
                logger.info(
                    f"[{symbol}] Gap Down 청산: gap={gap_pct:.2f}%"
                    f" <= {config.WSB_GAP_DOWN_PCT}%"
                )
                return True, "gap_down"

        # 4. Stop-Loss: pnl <= -7.0%
        if pnl_pct <= config.STOP_LOSS_PCT:
            logger.info(
                f"[{symbol}] Stop-Loss 발동: pnl={pnl_pct:.2f}%"
                f" <= {config.STOP_LOSS_PCT}%"
            )
            return True, "stop_loss"

        # 5. Trailing Stop: 최고점 대비 -5% AND 현재 수익 > 0%
        if drawdown <= config.TRAILING_STOP_PCT and pnl_pct > 0:
            logger.info(
                f"[{symbol}] Trailing Stop: drawdown={drawdown:.2f}%"
                f" <= {config.TRAILING_STOP_PCT}%, pnl={pnl_pct:.2f}%"
            )
            return True, "trailing_stop"

        return False, ""

    def _opinion_reversal(self, symbol, opinion, ps: dict, sym_scored: dict) -> str | None:
        """
        opinion_mode 청산 1단계 — 의견 변화 기반 역전 감지.
        # Design Ref: community-opinion-trend-sizing §6
        우선순위: neutral 급증 > consensus 붕괴 > 감성/의견 역전(score 하락·trend down·bearish 급증).
        Returns: 청산 사유 문자열 ("neutral_spike"|"consensus_break"|"sentiment_reversal") 또는 None.
        """
        # neutral 급증
        if opinion.neutral_ratio > config.WSB_OPINION_NEUTRAL_EXIT_RATIO:
            logger.info(f"[{symbol}] opinion_reversal(neutral_spike): neutral={opinion.neutral_ratio:.2f}")
            return "neutral_spike"
        # consensus 붕괴 (bullish/bearish <= 1.0)
        if opinion.consensus_ratio <= 1.0:
            logger.info(f"[{symbol}] opinion_reversal(consensus_break): consensus={opinion.consensus_ratio:.2f}")
            return "consensus_break"
        # opinion_score 역전
        entry_score = ps.get("entry_score")
        cur_score = opinion.opinion_score
        if entry_score and cur_score < entry_score * config.WSB_OPINION_REVERSAL_RATIO:
            logger.info(
                f"[{symbol}] opinion_reversal(score): {cur_score:.1f}"
                f" < entry×{config.WSB_OPINION_REVERSAL_RATIO}={entry_score*config.WSB_OPINION_REVERSAL_RATIO:.1f}"
            )
            return "sentiment_reversal"
        # 추세 하락
        if opinion.sentiment_trend == "DOWN":
            logger.info(f"[{symbol}] opinion_reversal(trend_down)")
            return "sentiment_reversal"
        # bearish 급증 (entry 대비 2배 이상)
        entry_bear = ps.get("entry_bearish_count")
        cur_bear = sym_scored.get("bearish")
        if entry_bear is not None and cur_bear is not None:
            if cur_bear >= max(entry_bear, 1) * 2 and cur_bear >= 2:
                logger.info(f"[{symbol}] opinion_reversal(bearish_surge): {cur_bear} >= {max(entry_bear,1)*2}")
                return "sentiment_reversal"
        return None
