# Design Ref: §wsb-signal-v3 §3 — WSBSignalEngine V3: Velocity/NeutralFilter/SignalV3 + 5단계 청산
# Plan SC: SC-01 30MA 제거, SC-02 중립필터, SC-03~SC-11 매수/매도 신호 기준
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

import config
import wsb_state
from sentiment_provider import SentimentProvider

logger = logging.getLogger(__name__)


# ===========================================================================
# community-opinion-agent §4 — DailyOpinionSnapshot + weighted counts
# 기존 OpinionMetrics(reddit_backtester)와 duck-typing 호환(opinion_score,
# sentiment_trend, persistence_days, consensus_ratio, neutral_ratio,
# velocity_state, atr, prev_close). summary(사람용)와 query_*(검색용) 분리.
# ===========================================================================

@dataclass
class DailyOpinionSnapshot:
    date: str
    symbol: str
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    weighted_bullish_count: float = 0.0
    weighted_bearish_count: float = 0.0
    weighted_neutral_count: float = 0.0
    total_mentions: int = 0
    source_quality_score: float = 1.0
    consensus_ratio: float = 0.0
    neutral_ratio: float = 0.0
    opinion_score: float = 50.0
    velocity_state: str = "NORMAL"
    opinion_trend: str = "FLAT"
    persistence_days: int = 0
    attention_state: str = "NORMAL"
    universe_tier: str = "CORE"
    tradeability_score: float = 0.0
    is_consensus_buy: bool = False
    is_consensus_sell: bool = False
    top_reasons: list = field(default_factory=list)
    top_keywords: list = field(default_factory=list)
    summary: str = ""
    query_positive: str = ""
    query_negative: str = ""
    query_opinion_trend: str = ""
    query_risk: str = ""
    query_attention: str = ""
    query_consensus: str = ""
    # --- Sizer duck-typing 호환 / 오케스트레이터 주입 필드 ---
    atr: float | None = None
    prev_close: float | None = None
    universe_size_multiplier: float = 1.0
    cost_risk_factor: float = 1.0

    @property
    def sentiment_trend(self) -> str:
        """OpinionMetrics 호환 alias (Sizer는 sentiment_trend를 읽음)."""
        return self.opinion_trend


_KEYWORD_STOPWORDS = frozenset({
    "THE", "AND", "FOR", "ARE", "THIS", "THAT", "WITH", "FROM", "HAVE",
    "WILL", "JUST", "WHAT", "ABOUT", "INTO", "MORE", "THAN", "THEY",
    "BUY", "SELL", "HOLD", "CALLS", "PUTS", "MOON", "YOLO", "DD",
})
_KEYWORD_PATTERN = re.compile(r"[A-Za-z]{4,}")


def _location_weight(location: str) -> float:
    """mention 위치별 가중 (title > body > comment)."""
    if location == "title":
        return config.COMMUNITY_TITLE_MENTION_WEIGHT
    if location == "comment":
        return config.COMMUNITY_COMMENT_MENTION_WEIGHT
    return config.COMMUNITY_BODY_MENTION_WEIGHT


def compute_weighted_counts(labeled_posts: list[dict]) -> tuple[float, float, float, float]:
    """label·source_quality_weight·location으로 가중 카운트 계산.
    labeled_posts item: {label, source_quality_weight, location}.
    Returns (weighted_bullish, weighted_bearish, weighted_neutral, source_quality_score)."""
    wb = wbear = wneut = 0.0
    sqw_sum = 0.0
    n = len(labeled_posts)
    for p in labeled_posts:
        sqw = float(p.get("source_quality_weight", 1.0))
        w = sqw * _location_weight(p.get("location", "body"))
        label = p.get("label")
        if label == "bullish":
            wb += w
        elif label == "bearish":
            wbear += w
        else:
            wneut += w
        sqw_sum += sqw
    sqs = (sqw_sum / n) if n else 1.0
    return round(wb, 4), round(wbear, 4), round(wneut, 4), round(sqs, 4)


def _attention_state(velocity_state: str) -> str:
    return {
        "NEW_SPIKE": "SPIKE",
        "HIGH_MOMENTUM": "RISING",
        "DECLINING": "DECLINING",
    }.get(velocity_state, "NORMAL")


def _extract_keywords(texts: list[str], top_n: int = 5) -> list[str]:
    """텍스트에서 빈도 상위 키워드 추출 (YAGNI: 간단 토큰 빈도)."""
    if not texts:
        return []
    freq: dict[str, int] = {}
    for t in texts:
        for m in _KEYWORD_PATTERN.findall(t or ""):
            w = m.upper()
            if w in _KEYWORD_STOPWORDS:
                continue
            freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:top_n]]


def build_daily_snapshot(
    symbol: str,
    scored_entry: dict,
    history: list[dict],
    *,
    universe_decision=None,
    labeled_posts: list[dict] = None,
    texts: list[str] = None,
    atr: float = None,
    prev_close: float = None,
    date_str: str = "",
) -> DailyOpinionSnapshot:
    """종목별 일별 의견 스냅샷 생성 (Design Ref: §4 / Plan FR-1.3~4).
    weighted 카운트는 labeled_posts가 있으면 가중, 없으면 raw count fallback."""
    bullish = int(scored_entry.get("bullish", 0))
    bearish = int(scored_entry.get("bearish", 0))
    neutral = int(scored_entry.get("neutral", 0))
    total = int(scored_entry.get("mentions", bullish + bearish + neutral))
    opinion_score = float(scored_entry.get("score", 50.0))
    neutral_ratio = float(scored_entry.get("neutral_ratio", 0.0))
    velocity_state = scored_entry.get("velocity_state", "NORMAL")

    if labeled_posts:
        wb, wbear, wneut, sqs = compute_weighted_counts(labeled_posts)
    else:
        wb, wbear, wneut, sqs = float(bullish), float(bearish), float(neutral), 1.0

    consensus_ratio = wb / max(wbear, 1.0)
    opinion_trend = wsb_state.compute_sentiment_trend([h["score"] for h in history])
    persistence_days = wsb_state.compute_persistence_days(history)
    attention = _attention_state(velocity_state)

    universe_tier = "CORE"
    tradeability = 0.0
    universe_mult = 1.0
    if universe_decision is not None:
        universe_tier = getattr(universe_decision, "universe_tier", "CORE")
        tradeability = getattr(universe_decision, "tradeability_score", 0.0)
        universe_mult = getattr(universe_decision, "size_multiplier", 1.0)

    is_consensus_buy = (
        wb >= wbear * config.COMMUNITY_CONSENSUS_MIN_RATIO
        and neutral_ratio <= config.COMMUNITY_NEUTRAL_RATIO_MAX
        and total >= config.COMMUNITY_MIN_DAILY_MENTIONS
    )
    is_consensus_sell = (
        wbear >= wb * config.COMMUNITY_CONSENSUS_MIN_RATIO
        and total >= config.COMMUNITY_MIN_DAILY_MENTIONS
    )

    # top_reasons (사람·라우터용 근거 코드)
    reasons = []
    if consensus_ratio >= config.WSB_OPINION_CONSENSUS_STRONG_RATIO:
        reasons.append("consensus_strong")
    elif consensus_ratio >= config.COMMUNITY_CONSENSUS_MIN_RATIO:
        reasons.append("consensus_ok")
    if opinion_trend == "UP":
        reasons.append("trend_up")
    elif opinion_trend == "DOWN":
        reasons.append("trend_down")
    if neutral_ratio <= 0.5:
        reasons.append("low_noise")
    elif neutral_ratio > config.COMMUNITY_NEUTRAL_RATIO_MAX:
        reasons.append("high_noise")
    if velocity_state == "NEW_SPIKE":
        reasons.append("new_spike")
    if persistence_days >= config.COMMUNITY_OPINION_PERSISTENCE_STRONG_DAYS:
        reasons.append("persistent")

    summary = (
        f"{symbol} {date_str}: score {opinion_score:.0f}, consensus {consensus_ratio:.2f}"
        f" (bull {wb:.1f}/bear {wbear:.1f}), neutral {neutral_ratio:.0%},"
        f" trend {opinion_trend} {persistence_days}d, attention {attention},"
        f" tier {universe_tier}."
        f" {'BUY consensus' if is_consensus_buy else ('SELL consensus' if is_consensus_sell else 'no consensus')}."
    )

    return DailyOpinionSnapshot(
        date=date_str, symbol=symbol,
        bullish_count=bullish, bearish_count=bearish, neutral_count=neutral,
        weighted_bullish_count=wb, weighted_bearish_count=wbear, weighted_neutral_count=wneut,
        total_mentions=total, source_quality_score=sqs,
        consensus_ratio=round(consensus_ratio, 4), neutral_ratio=round(neutral_ratio, 4),
        opinion_score=opinion_score, velocity_state=velocity_state,
        opinion_trend=opinion_trend, persistence_days=persistence_days,
        attention_state=attention, universe_tier=universe_tier,
        tradeability_score=round(tradeability, 4),
        is_consensus_buy=is_consensus_buy, is_consensus_sell=is_consensus_sell,
        top_reasons=reasons, top_keywords=_extract_keywords(texts or []),
        summary=summary,
        query_positive=f"{symbol} bullish opinion score {opinion_score:.0f}",
        query_negative=f"{symbol} bearish risk neutral {neutral_ratio:.2f}",
        query_opinion_trend=f"{symbol} trend {opinion_trend} persistence {persistence_days}d",
        query_risk=f"{symbol} neutral {neutral_ratio:.2f} velocity {velocity_state} tier {universe_tier}",
        query_attention=f"{symbol} attention {attention} velocity {velocity_state}",
        query_consensus=f"{symbol} consensus {consensus_ratio:.2f} bull {wb:.1f} bear {wbear:.1f}",
        atr=atr, prev_close=prev_close,
        universe_size_multiplier=universe_mult,
    )


class WSBSignalEngine:
    """
    Reddit 게시글 → Top N 종목 선정 전체 파이프라인 (V3).

    단계:
      1. _score_posts(): 종목별 감성 점수(표본 수축 적용) + bullish/bearish/neutral count
      2. _apply_neutral_filter(): 방향성 멘션 < 3 또는 극단 노이즈(>0.95) → NEUTRAL 강제
      3. _apply_velocity(): Mention Velocity 계산 → velocity_state
      4. _determine_signal_v3(): 매수 신호 결정 (Velocity 보정 매트릭스, RSI<70만 허용)
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
                "score_raw": data.get("score_raw", data["score"]),
                "velocity": velocity,
                "velocity_state": velocity_state,
                "neutral_ratio": neutral_ratio,
                "neutral_filtered": symbol in neutral_overrides,
                "signal": signal_map.get(symbol, "NEUTRAL"),
                "passed_consensus": symbol in passed_consensus,
                "in_top_n": symbol in top_n,
                "rank": top_n.index(symbol) + 1 if symbol in top_n else None,
                # 라이브 경로 전파: community_live가 scored_entry로 evaluate_candidate에 전달
                # → build_daily_snapshot 댓글 가중 활성화 (M4 / Open-1)
                "labeled_posts": data.get("labeled_posts", []),
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

            # bullish/bearish/neutral count (GPTProvider label / FinBERT finbert_label)
            # + labeled_posts: 댓글 가중(<1) 활성화용 (Design Ref: §7.3 / D2)
            # Plan SC: SC-04 본문(1.0)·댓글(0.5) 가중 합산, N=글+댓글
            labeled_posts = []
            for d in details:
                if d.get("label") == "bullish" or d.get("finbert_label") == "positive":
                    lab = "bullish"
                elif d.get("label") == "bearish" or d.get("finbert_label") == "negative":
                    lab = "bearish"
                else:
                    lab = "neutral"
                labeled_posts.append({
                    "label": lab,
                    "location": d.get("location", "body"),
                    "source_quality_weight": float(d.get("source_quality_weight", 1.0)),
                })
            bullish = sum(1 for p in labeled_posts if p["label"] == "bullish")
            bearish = sum(1 for p in labeled_posts if p["label"] == "bearish")
            neutral = len(details) - bullish - bearish
            total = bullish + bearish
            ratio = bullish / total if total > 0 else 0.0

            # funnel-fix: 표본 크기 수축 — 방향성 멘션 n이 적을수록 score를 50으로 당긴다.
            # 글 1개짜리 score 90이 글 50개짜리를 이기는 극소표본 노이즈 랭킹을 차단.
            k = config.WSB_SCORE_SHRINKAGE_K
            shrunk = round(50.0 + (score - 50.0) * total / (total + k), 2) if k > 0 else score

            result[symbol] = {
                "bullish": bullish,
                "bearish": bearish,
                "neutral": neutral,
                "ratio": round(ratio, 4),
                "mentions": len(posts),
                "score": shrunk,
                "score_raw": score,
                "labeled_posts": labeled_posts,
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
        방향성 의견(bull+bear)이 WSB_MIN_DIRECTIONAL_MENTIONS 미만이거나
        중립비율이 극단(WSB_NEUTRAL_RATIO_MAX=0.95) 초과인 종목을 NEUTRAL 강제.

        # funnel-fix 2026-06-13: 기존 "neutral/total > 0.75 → 킬" 방식은 FinBERT 중립
        # 편향 탓에 토론량 많은 종목일수록 탈락시키는 역차별이었다. 방향 판정은
        # 방향성 의견 수로 하고, 중립비율은 사이징 damper(position_sizer)로 강등.

        Returns:
            {symbol: "NEUTRAL"} — 필터 적용된 종목만 포함. 통과 종목은 제외.
        """
        neutral_overrides: dict[str, str] = {}
        for symbol, data in scored.items():
            directional = data["bullish"] + data["bearish"]
            total = directional + data["neutral"]
            if total == 0:
                continue
            neutral_ratio = data["neutral"] / total
            if directional < config.WSB_MIN_DIRECTIONAL_MENTIONS:
                neutral_overrides[symbol] = "NEUTRAL"
                logger.info(
                    f"[방향성 필터] {symbol}: 방향성 멘션={directional}"
                    f" < {config.WSB_MIN_DIRECTIONAL_MENTIONS} → NEUTRAL"
                )
            elif neutral_ratio > config.WSB_NEUTRAL_RATIO_MAX:
                neutral_overrides[symbol] = "NEUTRAL"
                logger.info(
                    f"[중립 필터] {symbol}: 중립비율={neutral_ratio:.0%}"
                    f" > {config.WSB_NEUTRAL_RATIO_MAX:.0%} (극단 노이즈) → NEUTRAL"
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

        # funnel-fix 2026-06-13: 기존 "BUY는 RSI 30~50, STRONG_BUY는 RSI<30" 창은
        # 군중 강세 종목(대개 RSI>50)과 모순되는 역추세 게이트였다.
        # 감성 모멘텀 전략답게 과매수(RSI≥70)만 회피하고 신호 등급은 score로 구분.
        if rsi >= config.WSB_RSI_BUY_MAX:
            return "NEUTRAL"
        if score > sb_threshold:
            return "STRONG_BUY"
        if score > buy_threshold:
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
