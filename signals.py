# Design Ref: §2.4 — 5단계 신호 결정 로직, 우선순위 순서 조건 검사
# Design Ref: §5 — Provider 통합, Volume Spike, articles_detail (signal-v2)
# Plan SC-01: 매 거래일 신호 자동 생성
import json
import logging
from datetime import datetime
from typing import Literal

import config
import collector
import indicators
import market_filter
import sentiment_provider as sp

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


def _get_active_providers() -> list[sp.SentimentProvider]:
    """config.SENTIMENT_PROVIDERS 에서 Provider 인스턴스 목록을 반환한다."""
    providers = []
    for name in config.SENTIMENT_PROVIDERS:
        try:
            providers.append(sp.get_provider(name))
        except ValueError as e:
            logger.warning(f"Provider 로드 실패: {e}")
    return providers


def _check_volume_spike(
    current_volume: float | None,
    volume_ma20: float | None,
    rsi: float,
    sentiment: float,
) -> bool:
    """
    Volume Spike 조건 검사 (Plan FR-09~11).

    조건:
    - current_volume >= volume_ma20 × VOLUME_SPIKE_MULTIPLIER (2.0)
    - rsi < VOLUME_SPIKE_RSI_MAX (40)
    - SENTIMENT_NEUTRAL_LOW <= sentiment <= SENTIMENT_NEUTRAL_HIGH (40~60)
    """
    if current_volume is None or volume_ma20 is None or volume_ma20 == 0:
        return False
    return (
        current_volume >= volume_ma20 * config.VOLUME_SPIKE_MULTIPLIER
        and rsi < config.VOLUME_SPIKE_RSI_MAX
        and config.SENTIMENT_NEUTRAL_LOW <= sentiment <= config.SENTIMENT_NEUTRAL_HIGH
    )


def _save_articles_detail(symbol: str, article_details: list[dict]) -> None:
    """
    data/articles_detail.json에 당일 기사별 FinBERT 분석 결과를 저장한다.
    기존 파일을 덮어씀 (당일 데이터만 유지 — NFR-03).
    """
    import os
    os.makedirs(config.DATA_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    # 기존 파일 로드 (같은 날짜면 종목 추가, 다른 날짜면 초기화)
    existing = {}
    try:
        with open(config.ARTICLES_DETAIL_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
        if existing.get("date") != today:
            existing = {}
    except (FileNotFoundError, json.JSONDecodeError):
        existing = {}

    existing["date"] = today
    existing[symbol] = article_details

    with open(config.ARTICLES_DETAIL_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def generate_signals_for_all(symbols: list[str]) -> dict[str, dict]:
    """SIGNAL_ENGINE 디스패처 — config.SIGNAL_ENGINE에 따라 Provider를 선택한다.

    Design Ref: §2.1, §3.3 — SignalProvider Protocol. 기존 호출부(scheduler/main/app)는
    변경 없이 이 함수를 그대로 호출하며, 내부에서 신호 엔진을 분기한다 (Plan FR-10).

    - SIGNAL_ENGINE="finbert" (기본값): FinbertProvider → _generate_signals_finbert
      (기존 동작 100% 보존, Plan SC-08)
    - SIGNAL_ENGINE="gpt5": NotImplementedError (Plan SC-09)

    Args:
        symbols: 종목 티커 목록

    Returns:
        {symbol: {...신호 데이터...}} — _generate_signals_finbert와 동일 스키마
    """
    # 지연 import — signal_provider ↔ signals 순환 import 방지
    import signal_provider

    # FR-14: KIS 모의투자 매매 가능 종목으로 필터링 (Design §2.1 step 2)
    tradable_symbols = _filter_tradable_symbols(symbols)

    provider = signal_provider.get_provider(config.SIGNAL_ENGINE)
    logger.info(f"[Signal Engine] '{provider.name}' Provider로 신호 생성 시작")
    return provider.generate_signals(tradable_symbols)


def _filter_tradable_symbols(symbols: list[str]) -> list[str]:
    """KIS 모의투자 매매 가능 종목으로 필터링한다 (Plan FR-14, SC-05).

    data/kis_symbols.json 캐시와 교집합을 취한다. 캐시가 없으면 전체 종목을
    그대로 통과시킨다 (폴백 — KIS 미설정 환경/첫 실행 호환).
    제외된 종목은 WARNING 로그로 남긴다 (SC-05).

    Args:
        symbols: 신호 생성 후보 종목 목록 (보통 config.SYMBOLS)

    Returns:
        매매 가능 종목만 남긴 목록
    """
    # 지연 import — signals ↔ kis_broker 모듈 로드 순서 영향 최소화
    import kis_broker

    tradable = kis_broker.load_cached_tradable_symbols()
    if tradable is None:
        logger.info("[KIS] 매매 가능 종목 캐시 없음 — 전체 종목으로 신호 생성")
        return symbols

    tradable_set = set(tradable)
    filtered: list[str] = []
    for s in symbols:
        if s in tradable_set:
            filtered.append(s)
        else:
            logger.warning(f"[KIS] {s} 모의투자 매매 불가 — 신호 생성 제외")
    if not filtered:
        logger.warning(
            "[KIS] 매매 가능 종목이 없습니다 — 전체 종목으로 폴백 (캐시 점검 필요)"
        )
        return symbols
    return filtered


def _generate_signals_finbert(symbols: list[str]) -> dict[str, dict]:
    """
    모든 종목에 대해 RSI + FinBERT 신호를 계산하고 결과를 반환한다.

    FinbertProvider가 래핑하는 실제 구현 — generate_signals_for_all 디스패처가
    SIGNAL_ENGINE="finbert"일 때 이 함수로 위임된다 (Design §3.3).

    프로세스:
    1. get_ohlcv() → get_latest_rsi() + calculate_volume_ma20()
    2. get_news() → Provider별 감성 점수 계산 → 평균
    3. determine_signal() → Volume Spike 예외 체크 → Market Filter

    Args:
        symbols: 종목 티커 목록

    Returns:
        {symbol: {rsi, rsi_ma, sentiment, sentiment_*,
                  volume_ma20, volume_spike,
                  market_rsi, market_filter_applied,
                  signal, signal_original, timestamp}}
        계산 실패한 종목은 결과에서 제외됨
    """
    results = {}
    timestamp = datetime.now().isoformat()

    # Market RSI 1회 조회, 전 종목 재사용 (Design Ref: §10)
    mkt_rsi = market_filter.get_market_rsi()

    # 활성 Provider 로드 (Design Ref: §5)
    providers = _get_active_providers()

    for symbol in symbols:
        logger.info(f"[{symbol}] 신호 계산 시작")
        try:
            # 1. OHLCV 수집 및 기술 지표 계산
            ohlcv_df = collector.get_ohlcv(symbol)
            rsi, rsi_ma = indicators.get_latest_rsi(symbol, ohlcv_df)

            if rsi is None:
                logger.warning(f"[{symbol}] RSI 계산 실패 — 스킵")
                continue

            # Volume MA20 (Plan FR-09)
            volume_ma20 = indicators.calculate_volume_ma20(ohlcv_df)
            current_volume = (
                float(ohlcv_df.iloc[-1]["volume"]) if not ohlcv_df.empty else None
            )

            # 2. 뉴스 수집 및 Provider별 감성 점수 계산
            articles = collector.get_news(symbol)
            scores_by_provider = {}
            finbert_article_details = []

            for provider in providers:
                score, details = provider.score(articles)
                provider_name = type(provider).__name__.replace("Provider", "").lower()
                scores_by_provider[provider_name] = score
                if isinstance(provider, sp.FinBERTProvider):
                    finbert_article_details = details

            sentiment_textblob = scores_by_provider.get("textblob", 50.0)
            sentiment_finbert = scores_by_provider.get("finbert", 50.0)
            # 활성 Provider 평균이 최종 sentiment
            all_scores = list(scores_by_provider.values())
            sentiment = round(sum(all_scores) / len(all_scores), 2) if all_scores else 50.0

            # 3. 신호 결정
            signal_original = determine_signal(rsi, sentiment)

            # 4. Volume Spike 예외 처리 (Plan FR-11, Market Filter 전)
            volume_spike = _check_volume_spike(current_volume, volume_ma20, rsi, sentiment)
            if volume_spike:
                signal_original = "BUY"
                logger.info(
                    f"[Volume Spike] {symbol}: BUY"
                    f" (vol={current_volume:.0f}/ma20={volume_ma20:.0f}"
                    f", ×{current_volume/volume_ma20:.1f})"
                )

            # 5. Market Filter 적용
            signal = market_filter.apply_market_filter(signal_original, mkt_rsi)
            market_filter_applied = signal != signal_original

            if market_filter_applied:
                logger.warning(
                    f"[Market Filter] {symbol}: {signal_original} → {signal}"
                    f" (Market RSI={mkt_rsi:.1f})"
                )

            # 6. articles_detail 저장 (FinBERT 결과가 있을 때만)
            if finbert_article_details:
                _save_articles_detail(symbol, finbert_article_details)

            results[symbol] = {
                "rsi": round(rsi, 2),
                "rsi_ma": round(rsi_ma, 2) if rsi_ma is not None else None,
                "sentiment": sentiment,
                "sentiment_textblob": sentiment_textblob,
                "sentiment_finbert": sentiment_finbert,
                "volume_ma20": round(volume_ma20, 0) if volume_ma20 is not None else None,
                "volume_spike": volume_spike,
                "market_rsi": round(mkt_rsi, 2) if mkt_rsi is not None else None,
                "market_filter_applied": market_filter_applied,
                "signal": signal,
                "signal_original": signal_original,
                "timestamp": timestamp,
            }

            rsi_ma_str = f"{rsi_ma:.1f}" if rsi_ma is not None else "N/A"
            vol_ma_str = f"{volume_ma20:.0f}" if volume_ma20 is not None else "N/A"
            logger.info(
                f"[{symbol}] 신호={signal} | RSI={rsi:.1f} | RSI_MA={rsi_ma_str}"
                f" | TB={sentiment_textblob:.1f} | FB={sentiment_finbert:.1f}"
                f" | Avg={sentiment:.1f} | VolMA={vol_ma_str}"
                f" | Spike={'Y' if volume_spike else 'N'}"
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
