# Design Ref: §2.4 — 5단계 신호 결정 로직, 우선순위 순서 조건 검사
# Design Ref: §4 — Market Filter + FinBERT 통합 (market-filter-finbert)
# Plan SC-01: 매 거래일 신호 자동 생성
import logging
from datetime import datetime
from typing import Literal

import config
import collector
import indicators
import market_filter

logger = logging.getLogger(__name__)

SignalType = Literal["STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL"]


def determine_signal(rsi: float, sentiment: float) -> SignalType:
    """
    RSI와 감성 점수를 입력받아 5단계 매매 신호를 반환한다.

    우선순위 순서로 검사 (강한 신호 우선):
    1. STRONG_BUY:  sentiment > 70 AND rsi < 30
    2. STRONG_SELL: sentiment < 30 AND rsi > 70
    3. BUY:         sentiment > 50 AND 30 <= rsi < 50
    4. SELL:        sentiment < 50 AND rsi > 70
    5. NEUTRAL:     40 <= sentiment <= 60 AND 40 <= rsi <= 60
    6. 기본값:      NEUTRAL (조건 미해당 — 신호 없음 방지)

    Args:
        rsi: RSI 값 (0~100)
        sentiment: Scaled Sentiment 값 (0~100)

    Returns:
        SignalType
    """
    if sentiment > config.SENTIMENT_STRONG_BUY and rsi < config.RSI_OVERSOLD:
        return "STRONG_BUY"
    if sentiment < config.SENTIMENT_STRONG_SELL and rsi > config.RSI_OVERBOUGHT:
        return "STRONG_SELL"
    if sentiment > config.SENTIMENT_BUY and config.RSI_OVERSOLD <= rsi < config.RSI_OVERBOUGHT - 20:
        # 30 <= rsi < 50  (Plan FR-03: BUY 조건 RSI 30 이상 50 미만)
        return "BUY"
    if sentiment < config.SENTIMENT_BUY and rsi > config.RSI_OVERBOUGHT:
        return "SELL"
    if (config.SENTIMENT_NEUTRAL_LOW <= sentiment <= config.SENTIMENT_NEUTRAL_HIGH
            and config.RSI_NEUTRAL_LOW <= rsi <= config.RSI_NEUTRAL_HIGH):
        return "NEUTRAL"

    # 엣지케이스: 조건 미해당 → NEUTRAL 기본값 (Plan: 신호 없음 방지)
    logger.debug(f"조건 미해당 (RSI={rsi:.1f}, Sentiment={sentiment:.1f}) → NEUTRAL 기본값")
    return "NEUTRAL"


def generate_signals_for_all(symbols: list[str]) -> dict[str, dict]:
    """
    모든 종목에 대해 신호를 계산하고 결과를 반환한다.

    프로세스:
    1. get_ohlcv() → get_latest_rsi()
    2. get_news() → calculate_sentiment_score()
    3. determine_signal()

    Args:
        symbols: 종목 티커 목록

    Returns:
        {symbol: {rsi, rsi_ma, sentiment, signal, timestamp}}
        계산 실패한 종목은 결과에서 제외됨
    """
    results = {}
    timestamp = datetime.now().isoformat()

    # Design Ref: §10 — Market RSI 1회 조회, 전 종목 재사용 (Plan SC-01)
    mkt_rsi = market_filter.get_market_rsi()

    for symbol in symbols:
        logger.info(f"[{symbol}] 신호 계산 시작")
        try:
            # 1. OHLCV 수집 및 RSI 계산
            ohlcv_df = collector.get_ohlcv(symbol)
            rsi, rsi_ma = indicators.get_latest_rsi(symbol, ohlcv_df)

            if rsi is None:
                logger.warning(f"[{symbol}] RSI 계산 실패 — 스킵")
                continue

            # 2. 뉴스 수집 및 감성 점수 계산 (TextBlob + FinBERT 병렬)
            articles = collector.get_news(symbol)
            sentiment_textblob = indicators.calculate_sentiment_score(articles)
            sentiment_finbert = indicators.calculate_finbert_sentiment_score(articles)
            # Plan SC-04: 두 모델 평균이 최종 sentiment
            sentiment = round((sentiment_textblob + sentiment_finbert) / 2, 2)

            # 3. 신호 결정
            signal_original = determine_signal(rsi, sentiment)

            # 4. Market Filter 적용 (Plan SC-02)
            signal = market_filter.apply_market_filter(signal_original, mkt_rsi)
            market_filter_applied = signal != signal_original

            if market_filter_applied:
                logger.warning(
                    f"[Market Filter] {symbol}: {signal_original} → {signal} "
                    f"(Market RSI={mkt_rsi:.1f})"
                )

            results[symbol] = {
                "rsi": round(rsi, 2),
                "rsi_ma": round(rsi_ma, 2) if rsi_ma is not None else None,
                # Plan SC-04: TextBlob + FinBERT 두 점수 모두 저장
                "sentiment": sentiment,
                "sentiment_textblob": sentiment_textblob,
                "sentiment_finbert": sentiment_finbert,
                # Plan SC-01: Market RSI 메타
                "market_rsi": round(mkt_rsi, 2) if mkt_rsi is not None else None,
                "market_filter_applied": market_filter_applied,
                "signal": signal,
                "signal_original": signal_original,
                "timestamp": timestamp,
            }

            rsi_ma_str = f"{rsi_ma:.1f}" if rsi_ma is not None else "N/A"
            logger.info(
                f"[{symbol}] 신호={signal} | RSI={rsi:.1f} | "
                f"RSI_MA={rsi_ma_str} | TB={sentiment_textblob:.1f} | FB={sentiment_finbert:.1f} | Avg={sentiment:.1f}"
            )

        except Exception as e:
            logger.error(f"[{symbol}] 신호 계산 중 예외 발생: {e}", exc_info=True)

    return results


def format_signals_summary(signals: dict[str, dict]) -> str:
    """콘솔 출력용 신호 요약 문자열 생성"""
    if not signals:
        return "신호 없음"
    lines = [f"\n{'='*55}", f"{'신호 요약':^55}", f"{'='*55}"]
    for symbol, data in signals.items():
        signal = data["signal"]
        rsi = data["rsi"]
        sentiment = data["sentiment"]
        rsi_ma = data.get("rsi_ma")
        rsi_ma_str = f"{rsi_ma:.1f}" if rsi_ma is not None else "N/A"
        lines.append(
            f"  {symbol:<6} | {signal:<12} | RSI={rsi:5.1f} | MA={rsi_ma_str:>5} | Sent={sentiment:5.1f}"
        )
    lines.append(f"{'='*55}\n")
    return "\n".join(lines)
