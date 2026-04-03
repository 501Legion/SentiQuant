# Design Ref: §2.3 — RSI(14), RSI MA(7), Scaled Sentiment 계산
# Design Ref: §3 — FinBERT 감성 분석 (market-filter-finbert)
# Plan SC-01: 매 거래일 신호 자동 생성의 핵심 계산 모듈
import logging

import numpy as np
import pandas as pd
from textblob import TextBlob

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


def calculate_sentiment_score(articles: list[dict]) -> float:
    """
    TextBlob으로 뉴스 기사의 감성 점수를 계산한다.

    알고리즘:
    1. 각 기사의 title + description 결합
    2. TextBlob polarity 계산 (-1 ~ 1)
    3. 평균 polarity → Scaled Sentiment = (avg + 1) * 50 → [0, 100]

    Args:
        articles: list of {title, description, publishedAt}

    Returns:
        float [0, 100]. 기사 없으면 50.0 (중립).
    """
    # Plan SC: 뉴스 없을 경우 중립(50.0) 반환 — 신호 생성 중단 방지
    if not articles:
        logger.warning("뉴스 기사 없음 — 감성 점수 기본값 50.0 (중립) 반환")
        return 50.0

    polarities = []
    for article in articles:
        text = f"{article.get('title', '')} {article.get('description', '')}".strip()
        if not text:
            continue
        try:
            polarity = TextBlob(text).sentiment.polarity
            polarities.append(polarity)
        except Exception as e:
            logger.warning(f"감성 분석 실패: {e}")

    if not polarities:
        logger.warning("유효한 기사 없음 — 감성 점수 기본값 50.0 반환")
        return 50.0

    avg_polarity = sum(polarities) / len(polarities)
    scaled = (avg_polarity + 1) * 50  # [-1,1] → [0,100]
    scaled = max(0.0, min(100.0, scaled))  # 클리핑

    logger.info(f"감성 점수: avg_polarity={avg_polarity:.4f} → scaled={scaled:.2f} (기사 {len(polarities)}건)")
    return round(scaled, 2)


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


def calculate_finbert_sentiment_score(articles: list[dict]) -> float:
    """
    FinBERT(ProsusAI/finbert)로 뉴스 기사의 감성 점수를 계산한다.

    알고리즘:
    1. title + description 결합, 512자 truncation
    2. FinBERT → {positive, negative, neutral} 확률
    3. raw = positive - negative → [-1, 1]
    4. scaled = (raw + 1) * 50 → [0, 100]
    5. 전체 기사 평균 반환

    Args:
        articles: list of {title, description, publishedAt}

    Returns:
        float [0, 100]. 기사 없거나 실패 시 50.0 (중립).
    """
    # Plan SC-03: FinBERT 감성 점수 계산
    if not articles:
        logger.warning("FinBERT: 뉴스 기사 없음 — 기본값 50.0 반환")
        return 50.0

    try:
        pipe = _get_finbert_pipeline()
    except Exception as e:
        logger.error(f"FinBERT 초기화 실패: {e} — 기본값 50.0 반환")
        return 50.0

    scores = []
    for article in articles:
        text = f"{article.get('title', '')} {article.get('description', '')}".strip()
        if not text:
            continue
        try:
            # FinBERT 최대 512 토큰, 초과 시 자동 truncation
            result = pipe(text[:512], truncation=True)
            # result 형태: [[{label: "positive", score: 0.8}, {label: "negative", ...}, ...]]
            label_map = {r["label"]: r["score"] for r in result[0]}
            positive = label_map.get("positive", 0.0)
            negative = label_map.get("negative", 0.0)
            raw = positive - negative          # [-1.0, 1.0]
            scores.append(raw)
        except Exception as e:
            logger.warning(f"FinBERT 개별 기사 분석 실패: {e}")

    if not scores:
        logger.warning("FinBERT: 유효한 기사 없음 — 기본값 50.0 반환")
        return 50.0

    avg_raw = sum(scores) / len(scores)
    scaled = (avg_raw + 1) * 50              # [-1,1] → [0,100]
    scaled = max(0.0, min(100.0, scaled))    # 클리핑

    logger.info(f"FinBERT 감성 점수: avg_raw={avg_raw:.4f} → scaled={scaled:.2f} (기사 {len(scores)}건)")
    return round(scaled, 2)


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
