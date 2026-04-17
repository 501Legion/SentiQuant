# Design Ref: §2.3 — RSI(14), RSI MA(7), Scaled Sentiment 계산
# Design Ref: §3 — FinBERT 감성 분석 (market-filter-finbert)
# Plan SC-01: 매 거래일 신호 자동 생성의 핵심 계산 모듈
import logging

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


def calculate_rsi(closes: pd.Series, period: int = None) -> pd.Series:
    """
    Wilder's Smoothing(EWM) 방식으로 RSI를 계산한다.

    Args:
        closes: 종가 Series (index=날짜 또는 정수)
        period: RSI 기간 (기본값: config.RSI_PERIOD = 14)

    Returns:
        RSI Series (0~100). 계산 불가 구간은 NaN.
    """
    if period is None:
        period = config.RSI_PERIOD

    if len(closes) < period + 1:
        logger.warning(f"RSI 계산 데이터 부족: {len(closes)}개 (최소 {period + 1}개 필요)")
        return pd.Series(dtype=float)

    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder's EWM: alpha = 1 / period
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    # 0으로 나누기 방지
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_rsi_ma(rsi: pd.Series, period: int = None) -> pd.Series:
    """
    RSI의 단순이동평균(SMA)을 계산한다.

    Args:
        rsi: RSI Series
        period: MA 기간 (기본값: config.RSI_MA_PERIOD = 7)

    Returns:
        RSI MA Series
    """
    if period is None:
        period = config.RSI_MA_PERIOD
    return rsi.rolling(window=period).mean()


def calculate_volume_ma20(ohlcv_df: pd.DataFrame) -> float | None:
    """
    OHLCV DataFrame에서 20일 평균 거래량(SMA)을 계산한다.
    Volume Spike 판단에 사용된다 (Design Ref: §3.2).

    Args:
        ohlcv_df: OHLCV DataFrame (volume 컬럼 필요)

    Returns:
        float: 최근 VOLUME_MA_PERIOD일 평균 거래량.
               데이터 부족 또는 빈 DataFrame이면 None.
    """
    if ohlcv_df.empty or len(ohlcv_df) < config.VOLUME_MA_PERIOD:
        return None
    return float(ohlcv_df["volume"].tail(config.VOLUME_MA_PERIOD).mean())


# Design Ref: §3.1 — FinBERT lazy singleton (CPU, 최초 1회 초기화)
_finbert_pipeline = None
_finbert_initialized = False  # 초기화 시도 여부 추적 (재시도 방지)


def _get_finbert_pipeline():
    """FinBERT text-classification pipeline lazy singleton.
    초기화 실패 시 재시도하지 않고 RuntimeError를 발생시킨다.
    """
    global _finbert_pipeline, _finbert_initialized
    if _finbert_initialized:
        if _finbert_pipeline is None:
            raise RuntimeError("FinBERT 이전 초기화 실패 — 재시도 불가")
        return _finbert_pipeline

    _finbert_initialized = True
    from optimum.onnxruntime import ORTModelForSequenceClassification
    from transformers import AutoTokenizer, pipeline as hf_pipeline
    import pathlib
    _ONNX_CACHE = pathlib.Path(__file__).parent / "models" / "finbert-onnx"
    if _ONNX_CACHE.exists():
        logger.info("FinBERT 모델 로드 중... (캐시)")
        model = ORTModelForSequenceClassification.from_pretrained(str(_ONNX_CACHE))
    else:
        logger.info("FinBERT 모델 초기화 중... (최초 ONNX 변환, ~1분 소요)")
        model = ORTModelForSequenceClassification.from_pretrained(
            "ProsusAI/finbert",
            export=True,
        )
        model.save_pretrained(str(_ONNX_CACHE))
        logger.info(f"FinBERT ONNX 모델 저장 완료: {_ONNX_CACHE}")
    tokenizer = AutoTokenizer.from_pretrained("ProsusAI/finbert")
    _finbert_pipeline = hf_pipeline(
        "text-classification",
        model=model,
        tokenizer=tokenizer,
        top_k=None,
    )
    logger.info("FinBERT 초기화 완료 (ONNX Runtime)")
    return _finbert_pipeline



def get_ma(ohlcv_df: pd.DataFrame, period: int) -> float | None:
    """
    단순이동평균(SMA) 계산. 30MA 진입 필터 및 청산 조건에 사용.
    # Design Ref: §2.2 — 30MA 필터 (WSBSignalEngine._filter_ma30)

    Args:
        ohlcv_df: Polygon OHLCV DataFrame (close 컬럼 필요)
        period: MA 기간 (예: 30)

    Returns:
        최신 MA 값. 데이터 부족(rows < period) 시 None.
    """
    if ohlcv_df.empty or len(ohlcv_df) < period:
        return None
    return float(ohlcv_df["close"].tail(period).mean())


def get_atr(ohlcv_df: pd.DataFrame, period: int = 14) -> float | None:
    """
    Average True Range (ATR) 계산. Wilder's smoothing 방식.
    # Design Ref: §2.3 — VolatilitySizer (position_sizer.py)
    # Plan SC: Volatility-Weighted sizing ATR 기반 포지션 크기 결정

    True Range = max(H-L, |H-prevC|, |L-prevC|)
    ATR = EWM(TR, alpha=1/period)

    Args:
        ohlcv_df: Polygon OHLCV DataFrame (high, low, close 컬럼 필요)
        period: ATR 기간 (기본값: config.ATR_PERIOD = 14)

    Returns:
        최신 ATR 값. 데이터 부족(rows < period+1) 시 None.
    """
    if ohlcv_df.empty or len(ohlcv_df) < period + 1:
        return None

    high = ohlcv_df["high"].astype(float)
    low = ohlcv_df["low"].astype(float)
    close = ohlcv_df["close"].astype(float)
    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Wilder's smoothing: alpha = 1/period
    atr_series = tr.ewm(alpha=1 / period, adjust=False).mean()
    result = atr_series.dropna()
    return float(result.iloc[-1]) if not result.empty else None


def get_latest_rsi(symbol: str, ohlcv_df: pd.DataFrame) -> tuple[float | None, float | None]:
    """
    OHLCV DataFrame에서 최신 RSI와 RSI MA를 계산해 반환한다.

    Returns:
        (rsi_latest, rsi_ma_latest). 계산 실패 시 (None, None).
    """
    if ohlcv_df.empty:
        logger.warning(f"[{symbol}] OHLCV 데이터 없음 — RSI 계산 불가")
        return None, None

    closes = ohlcv_df["close"].astype(float)
    rsi_series = calculate_rsi(closes)

    if rsi_series.empty or rsi_series.dropna().empty:
        logger.warning(f"[{symbol}] RSI 계산 결과 없음")
        return None, None

    rsi_ma_series = calculate_rsi_ma(rsi_series)

    rsi_latest = float(rsi_series.dropna().iloc[-1])
    rsi_ma_latest_raw = rsi_ma_series.dropna()
    rsi_ma_latest = float(rsi_ma_latest_raw.iloc[-1]) if not rsi_ma_latest_raw.empty else None

    rsi_ma_str = f"{rsi_ma_latest:.2f}" if rsi_ma_latest is not None else "N/A"
    logger.info(f"[{symbol}] RSI={rsi_latest:.2f}, RSI_MA={rsi_ma_str}")
    return rsi_latest, rsi_ma_latest
