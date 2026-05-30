# Design Ref: §2.3 — PositionSizer ABC + EqualSizer + SentimentSizer + VolatilitySizer
# Plan SC FR-12: equal / sentiment / volatility 3가지 방법 구현
# Plan SC FR-13: get_sizer() 팩토리로 백테스팅에서 동일 인터페이스로 교체 가능
import math
import logging
from abc import ABC, abstractmethod

import config

logger = logging.getLogger(__name__)


class PositionSizer(ABC):
    """
    매수 주식 수 계산 추상 베이스 클래스.

    모든 Sizer는 calc_shares()를 구현한다.
    현금 부족 또는 가격 0인 경우 0을 반환한다.
    """

    @abstractmethod
    def calc_shares(self, total_cash: float, open_price: float, **kwargs) -> int:
        """
        매수할 주식 수를 계산한다.

        Args:
            total_cash: 현재 가용 현금 (USD)
            open_price: 매수 시가 (USD)
            **kwargs: Sizer별 추가 파라미터

        Returns:
            0 이상 정수. 현금 부족 또는 open_price <= 0이면 0.
        """


class EqualSizer(PositionSizer):
    """
    균등 배분 방식. total_cash / MAX_POSITIONS 슬롯 고정.
    # Design Ref: §2.3 — Equal Weighting (10% 고정)

    kwargs: (없음)
    """

    def calc_shares(self, total_cash: float, open_price: float, **kwargs) -> int:
        if open_price <= 0:
            return 0
        slot = total_cash / config.MAX_POSITIONS
        shares = math.floor(slot / open_price)
        logger.debug(f"EqualSizer: slot={slot:.2f}, open={open_price:.2f} → {shares}주")
        return max(0, shares)


class SentimentSizer(PositionSizer):
    """
    감성 비율(bullish_ratio)에 따라 5% / 10% / 15% 배분.
    # Design Ref: §2.3 — Sentiment-Weighted
    # Plan SC: bullish_ratio >= 0.80 → 15%, >= 0.65 → 10%, else 5%

    kwargs:
        bullish_ratio (float, 0-1): bullish / (bullish + bearish)
    """

    def calc_shares(self, total_cash: float, open_price: float, **kwargs) -> int:
        if open_price <= 0:
            return 0
        ratio = kwargs.get("bullish_ratio", 0.5)
        if ratio >= config.SENTIMENT_SIZE_HIGH_THRESHOLD:      # 0.80
            pct = config.SENTIMENT_SIZE_HIGH                    # 0.15
        elif ratio >= config.SENTIMENT_SIZE_MID_THRESHOLD:     # 0.65
            pct = config.SENTIMENT_SIZE_MID                     # 0.10
        else:
            pct = config.SENTIMENT_SIZE_LOW                     # 0.05
        shares = math.floor(total_cash * pct / open_price)
        logger.debug(
            f"SentimentSizer: ratio={ratio:.2f} → pct={pct:.0%},"
            f" open={open_price:.2f} → {shares}주"
        )
        return max(0, shares)


class VolatilitySizer(PositionSizer):
    """
    ATR 기반 변동성 역수 비례 배분. 고변동 → 작은 비중, 저변동 → 큰 비중.
    # Design Ref: §2.3 — Volatility-Weighted (ATR 기반)
    # Plan SC FR-13: indicators.get_atr() 연동

    kwargs:
        atr (float): 14일 ATR 값
        prev_close (float): 전일 종가

    ATR 없거나 prev_close=0이면 EqualSizer 폴백.
    size_pct = clamp(TARGET_RISK / (atr / prev_close), 5%, 15%)
    """

    def calc_shares(self, total_cash: float, open_price: float, **kwargs) -> int:
        if open_price <= 0:
            return 0

        atr = kwargs.get("atr")
        prev_close = kwargs.get("prev_close")

        if not atr or not prev_close or prev_close <= 0:
            logger.warning(
                "VolatilitySizer: ATR 또는 prev_close 없음 — EqualSizer 폴백"
            )
            shares = math.floor(total_cash / config.MAX_POSITIONS / open_price)
            return max(0, shares)

        atr_pct = atr / prev_close
        if atr_pct <= 0:
            logger.warning("VolatilitySizer: atr_pct <= 0 — EqualSizer 폴백")
            shares = math.floor(total_cash / config.MAX_POSITIONS / open_price)
            return max(0, shares)

        raw_size = config.VOLATILITY_TARGET_RISK / atr_pct
        size_pct = max(config.VOLATILITY_MIN_PCT,
                       min(config.VOLATILITY_MAX_PCT, raw_size))
        shares = math.floor(total_cash * size_pct / open_price)
        logger.debug(
            f"VolatilitySizer: atr={atr:.4f}, atr_pct={atr_pct:.4f},"
            f" raw={raw_size:.3f} → clamped={size_pct:.0%} → {shares}주"
        )
        return max(0, shares)


class CommunityOpinionTrendSizer(PositionSizer):
    """
    커뮤니티 의견 트렌드 기반 7-factor 사이징.
    # Design Ref: community-opinion-trend-sizing §6
    급등추격 금지 — final factor max 1.3, NEW_SPIKE/DECLINING은 축소.

    kwargs:
        opinion: OpinionMetrics 호환 객체 (duck-typed). 속성:
                 opinion_score, sentiment_trend, persistence_days, consensus_ratio,
                 neutral_ratio, velocity_state, atr, prev_close

    진입 게이팅(0 반환):
        opinion_score < WSB_OPINION_SCORE_LOW(60)
        or neutral_ratio > WSB_OPINION_NEUTRAL_ENTRY_MAX(0.70)
        or consensus_ratio < WSB_OPINION_CONSENSUS_MIN_RATIO(1.5)

    base = total_cash × EQUAL_POSITION_PCT × clamp(Π factors, MIN, MAX) / open_price
    """

    last_size_factor: float = 0.0   # 직전 calc_shares의 최종 factor (RedditTradeRecord 기록용)

    def calc_shares(self, total_cash: float, open_price: float, **kwargs) -> int:
        self.last_size_factor = 0.0
        opinion = kwargs.get("opinion")
        if opinion is None or open_price <= 0:
            return 0

        score = opinion.opinion_score
        neutral_ratio = opinion.neutral_ratio
        consensus = opinion.consensus_ratio

        # --- 진입 게이팅 (Plan SC-03/04/05) ---
        if (score < config.WSB_OPINION_SCORE_LOW
                or neutral_ratio > config.WSB_OPINION_NEUTRAL_ENTRY_MAX
                or consensus < config.WSB_OPINION_CONSENSUS_MIN_RATIO):
            logger.debug(
                f"OpinionTrendSizer 진입 제외: score={score:.1f},"
                f" neutral={neutral_ratio:.2f}, consensus={consensus:.2f}"
            )
            return 0

        # --- 7 factor (Design Ref §6) ---
        f_sentiment = self._sentiment_factor(score)
        f_trend = self._trend_factor(opinion.sentiment_trend)
        f_persist = self._persistence_factor(opinion.persistence_days)
        f_consensus = self._consensus_factor(consensus)
        f_neutral = self._neutral_factor(neutral_ratio)
        f_attention = self._attention_factor(opinion.velocity_state)
        f_risk = self._risk_factor(
            getattr(opinion, "atr", None), getattr(opinion, "prev_close", None)
        )

        # --- community-opinion-agent §7: +3 factor (속성 없으면 1.0 → 회귀 0) ---
        f_source = self._source_quality_factor(
            getattr(opinion, "source_quality_score", None)
        )
        f_universe = self._mult_factor(getattr(opinion, "universe_size_multiplier", 1.0))
        f_cost = self._mult_factor(getattr(opinion, "cost_risk_factor", 1.0))

        raw = (f_sentiment * f_trend * f_persist * f_consensus
               * f_neutral * f_attention * f_risk
               * f_source * f_universe * f_cost)
        final = max(config.WSB_OPINION_SIZE_FACTOR_MIN,
                    min(config.WSB_OPINION_SIZE_FACTOR_MAX, raw))
        self.last_size_factor = final

        shares = math.floor(total_cash * config.EQUAL_POSITION_PCT * final / open_price)
        logger.debug(
            f"OpinionTrendSizer: score={score:.1f} trend={opinion.sentiment_trend}"
            f" persist={opinion.persistence_days} cons={consensus:.2f}"
            f" vel={opinion.velocity_state} → factors"
            f"({f_sentiment},{f_trend},{f_persist},{f_consensus},{f_neutral},"
            f"{f_attention},{f_risk},src={f_source},uni={f_universe},cost={f_cost})"
            f" → final={final:.3f} → {shares}주"
        )
        return max(0, shares)

    @staticmethod
    def _source_quality_factor(sqs: float | None) -> float:
        """source_quality_score(평균 글 품질 weight) → 사이징 factor.
        속성 없음(None) → 1.0 (기존 OpinionMetrics 회귀 0)."""
        if sqs is None:
            return 1.0
        if sqs >= 1.2:
            return 1.1
        if sqs >= 0.8:
            return 1.0
        if sqs >= 0.5:
            return 0.9
        return 0.7

    @staticmethod
    def _mult_factor(value) -> float:
        """universe_size_multiplier / cost_risk_factor를 안전 float로.
        None/음수 → 1.0, 그 외 그대로 (0.0이면 진입 차단으로 작동)."""
        if value is None:
            return 1.0
        try:
            v = float(value)
        except (TypeError, ValueError):
            return 1.0
        return v if v >= 0 else 1.0

    @staticmethod
    def _sentiment_factor(score: float) -> float:
        if score >= config.WSB_OPINION_SCORE_HIGH:
            return config.WSB_OPINION_FACTOR_HIGH
        if score >= config.WSB_OPINION_SCORE_MID:
            return config.WSB_OPINION_FACTOR_MID
        return config.WSB_OPINION_FACTOR_LOW   # [60, 70)

    @staticmethod
    def _trend_factor(trend: str) -> float:
        if trend == "UP":
            return config.WSB_OPINION_TREND_UP_FACTOR
        if trend == "DOWN":
            return config.WSB_OPINION_TREND_DOWN_FACTOR
        return config.WSB_OPINION_TREND_FLAT_FACTOR

    @staticmethod
    def _persistence_factor(days: int) -> float:
        if days >= config.WSB_OPINION_PERSISTENCE_STRONG_DAYS:
            return config.WSB_OPINION_PERSISTENCE_STRONG_FACTOR
        if days >= config.WSB_OPINION_PERSISTENCE_MIN_DAYS:
            return config.WSB_OPINION_PERSISTENCE_NORMAL_FACTOR
        return config.WSB_OPINION_PERSISTENCE_WEAK_FACTOR   # 0~1일

    @staticmethod
    def _consensus_factor(consensus: float) -> float:
        # 게이팅으로 consensus >= MIN_RATIO(1.5) 보장됨
        if consensus >= config.WSB_OPINION_CONSENSUS_STRONG_RATIO:
            return config.WSB_OPINION_FACTOR_HIGH   # 1.2
        return config.WSB_OPINION_FACTOR_MID        # 1.0

    @staticmethod
    def _neutral_factor(neutral_ratio: float) -> float:
        # 게이팅으로 neutral_ratio <= 0.70 보장됨
        if neutral_ratio > 0.50:
            return 0.7
        return 1.0

    @staticmethod
    def _attention_factor(velocity_state: str) -> float:
        if velocity_state == "NEW_SPIKE":
            return config.WSB_OPINION_NEW_SPIKE_FACTOR        # 0.5 (단발 폭증 보수적)
        if velocity_state == "DECLINING":
            return config.WSB_OPINION_DECLINING_FACTOR        # 0.6
        if velocity_state == "HIGH_MOMENTUM":
            return config.WSB_OPINION_HIGH_ATTENTION_FACTOR   # 1.1 (안정적 관심 증가)
        return 1.0                                            # NORMAL / NEW_IGNORE

    @staticmethod
    def _risk_factor(atr: float | None, prev_close: float | None) -> float:
        if not atr or not prev_close or prev_close <= 0:
            return 1.0
        atr_pct = atr / prev_close
        if atr_pct > 0.08:
            return 0.5
        if atr_pct > 0.05:
            return 0.8
        return 1.0


def get_sizer(method: str) -> PositionSizer:
    """
    이름으로 PositionSizer 인스턴스를 반환한다.

    Args:
        method: "equal" | "sentiment" | "volatility" | "opinion_trend"

    Returns:
        PositionSizer 인스턴스

    Raises:
        ValueError: 알 수 없는 method 이름
    """
    if method == "equal":
        return EqualSizer()
    if method == "sentiment":
        return SentimentSizer()
    if method == "volatility":
        return VolatilitySizer()
    if method == "opinion_trend":
        return CommunityOpinionTrendSizer()
    raise ValueError(
        f"알 수 없는 sizing method: '{method}'."
        f" 사용 가능: equal, sentiment, volatility, opinion_trend"
    )
