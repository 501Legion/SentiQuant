# Design Ref: §2 — Market RSI Filter 모듈 (Option C: 독립 모듈 분리)
# Plan SC-01: QQQ 14일 RSI로 시장 과열/하락 추세 감지
# Plan SC-02: 매수 신호 다운그레이드 (STRONG_BUY→BUY, BUY→NEUTRAL)
import logging

import config
import collector
import indicators

logger = logging.getLogger(__name__)

# 세션 내 1회 캐시 — 전 종목에 동일 Market RSI 재사용 (Design Ref: §10)
_market_rsi_cache: float | None = None
_cache_initialized: bool = False


def get_market_rsi() -> float | None:
    """
    QQQ ETF의 14일 RSI를 계산해 반환한다 (세션 내 1회 캐시).

    기존 get_ohlcv() + get_latest_rsi()를 재사용하여
    추가 구현 없이 Market RSI를 계산한다.

    Returns:
        float: Market RSI (0~100). 수집/계산 실패 시 None.
    """
    global _market_rsi_cache, _cache_initialized

    if _cache_initialized:
        return _market_rsi_cache

    _cache_initialized = True

    ohlcv_df = collector.get_ohlcv(config.MARKET_SYMBOL)
    if ohlcv_df.empty:
        logger.error(f"[Market Filter] {config.MARKET_SYMBOL} OHLCV 수집 실패 — Market Filter 비활성화")
        return None

    rsi, _ = indicators.get_latest_rsi(config.MARKET_SYMBOL, ohlcv_df)
    _market_rsi_cache = rsi

    if rsi is not None:
        state = _describe_market_state(rsi)
        logger.info(f"[Market Filter] {config.MARKET_SYMBOL} RSI={rsi:.2f} → 시장 상태: {state}")
    else:
        logger.warning(f"[Market Filter] {config.MARKET_SYMBOL} RSI 계산 실패")

    return _market_rsi_cache


def apply_market_filter(signal: str, market_rsi: float | None) -> str:
    """
    Market RSI 상태에 따라 매수 신호를 다운그레이드한다.

    규칙 (Design Ref: §2.3):
    - market_rsi > MARKET_RSI_OVERBOUGHT(70): 초과열 → 매수 억제
    - market_rsi < MARKET_RSI_DOWNTREND(30):  하락 추세 → 매수 억제
    - 30 ≤ market_rsi ≤ 70: 정상 → 신호 변경 없음
    - market_rsi is None: 수집 실패 → 신호 변경 없음 (안전 폴백)

    다운그레이드 매트릭스:
        STRONG_BUY → BUY
        BUY        → NEUTRAL
        기타       → 변경 없음

    Args:
        signal: 원래 신호 ('STRONG_BUY', 'BUY', 'NEUTRAL', 'SELL', 'STRONG_SELL')
        market_rsi: Market RSI 값 또는 None

    Returns:
        str: 필터 적용 후 최종 신호
    """
    if market_rsi is None:
        return signal

    is_extreme = (
        market_rsi > config.MARKET_RSI_OVERBOUGHT
        or market_rsi < config.MARKET_RSI_DOWNTREND
    )

    if not is_extreme:
        return signal

    # Plan SC-02: 매수 신호 다운그레이드
    if signal == "STRONG_BUY":
        return "BUY"
    if signal == "BUY":
        return "NEUTRAL"

    # NEUTRAL, SELL, STRONG_SELL은 변경 없음
    return signal


def _describe_market_state(rsi: float) -> str:
    """Market RSI 값을 사람이 읽기 쉬운 상태 문자열로 변환한다."""
    if rsi > config.MARKET_RSI_OVERBOUGHT:
        return f"과열 (RSI>{config.MARKET_RSI_OVERBOUGHT:.0f}) — 매수 신호 다운그레이드 활성"
    if rsi < config.MARKET_RSI_DOWNTREND:
        return f"하락 추세 (RSI<{config.MARKET_RSI_DOWNTREND:.0f}) — 매수 신호 다운그레이드 활성"
    return "정상 (필터 비활성)"


def reset_cache() -> None:
    """세션 캐시를 초기화한다. 테스트 또는 강제 재조회 시 사용."""
    global _market_rsi_cache, _cache_initialized
    _market_rsi_cache = None
    _cache_initialized = False
