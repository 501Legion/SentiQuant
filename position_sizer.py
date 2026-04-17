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


def get_sizer(method: str) -> PositionSizer:
    """
    이름으로 PositionSizer 인스턴스를 반환한다.

    Args:
        method: "equal" | "sentiment" | "volatility"

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
    raise ValueError(
        f"알 수 없는 sizing method: '{method}'."
        f" 사용 가능: equal, sentiment, volatility"
    )
