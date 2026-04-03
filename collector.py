# Plan SC-01: Massive.com(구 Polygon.io) SDK 기반 OHLCV + NewsAPI 뉴스 수집
# Migration: polygon-api-client → massive (2025-10-30 리브랜딩)
import logging
import time
from datetime import datetime, timedelta

import pandas as pd
import requests
from massive import RESTClient

import config

logger = logging.getLogger(__name__)

# Massive RESTClient 싱글톤 (모듈 로드 시 1회 초기화)
_rest_client: RESTClient | None = None


def _get_client() -> RESTClient:
    """Massive RESTClient 인스턴스를 반환한다 (lazy singleton)."""
    global _rest_client
    if _rest_client is None:
        _rest_client = RESTClient(api_key=config.POLYGON_API_KEY)
    return _rest_client


def get_ohlcv(symbol: str, days: int = None) -> pd.DataFrame:
    """
    Massive RESTClient로 일별 OHLCV 데이터를 수집한다.

    Args:
        symbol: 종목 티커 (예: "AAPL")
        days: 과거 몇 거래일치 수집할지 (기본값: config.OHLCV_LOOKBACK_DAYS)

    Returns:
        DataFrame with columns: [date, open, high, low, close, volume]
        date는 문자열 "YYYY-MM-DD" 형식. 수집 실패 시 빈 DataFrame.
    """
    if days is None:
        days = config.OHLCV_LOOKBACK_DAYS

    end_date = datetime.now()
    # 거래일 기준으로 여유 있게 캘린더 일수를 늘림 (주말/휴일 포함)
    start_date = end_date - timedelta(days=int(days * 1.5))

    client = _get_client()
    for attempt in range(1, config.REQUEST_MAX_RETRIES + 1):
        try:
            aggs = client.list_aggs(
                ticker=symbol,
                multiplier=1,
                timespan="day",
                from_=start_date.strftime("%Y-%m-%d"),
                to=end_date.strftime("%Y-%m-%d"),
                limit=200,
            )

            rows = []
            for agg in aggs:
                # Agg 객체 속성: timestamp(ms), open, high, low, close, volume
                date_str = datetime.fromtimestamp(agg.timestamp / 1000).strftime("%Y-%m-%d")
                rows.append({
                    "date": date_str,
                    "open": float(agg.open),
                    "high": float(agg.high),
                    "low": float(agg.low),
                    "close": float(agg.close),
                    "volume": float(agg.volume),
                })

            if not rows:
                logger.warning(f"[{symbol}] OHLCV 데이터 없음")
                return pd.DataFrame()

            df = pd.DataFrame(rows).tail(days).reset_index(drop=True)
            logger.info(f"[{symbol}] OHLCV {len(df)}일치 수집 완료")
            return df

        except Exception as e:
            logger.warning(f"[{symbol}] OHLCV 수집 실패 ({attempt}/{config.REQUEST_MAX_RETRIES}): {e}")
            if attempt < config.REQUEST_MAX_RETRIES:
                time.sleep(config.REQUEST_RETRY_BASE_DELAY * (2 ** (attempt - 1)))

    logger.error(f"[{symbol}] OHLCV 수집 최종 실패")
    return pd.DataFrame()


def get_latest_open_price(symbol: str) -> float | None:
    """
    당일 또는 가장 최근 거래일의 시가(Open Price)를 가져온다.
    가상 주문 처리(09:35 ET)에 사용된다.

    Returns:
        float: 시가. 수집 실패 시 None.
    """
    df = get_ohlcv(symbol, days=5)
    if df.empty:
        logger.error(f"[{symbol}] 시가 수집 실패")
        return None
    open_price = float(df.iloc[-1]["open"])
    logger.info(f"[{symbol}] 최근 시가: ${open_price:.2f}")
    return open_price


def get_news(symbol: str, days: int = None) -> list[dict]:
    """
    NewsAPI에서 종목 관련 최근 뉴스를 수집한다.
    (Massive 리브랜딩과 무관하게 NewsAPI는 변경 없음)

    Args:
        symbol: 종목 티커 (예: "AAPL")
        days: 몇 일 전부터 수집할지 (기본값: config.NEWS_LOOKBACK_DAYS)

    Returns:
        list of {title, description, publishedAt}
    """
    if days is None:
        days = config.NEWS_LOOKBACK_DAYS

    company_name = config.COMPANY_NAMES.get(symbol, symbol)
    from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    url = f"{config.NEWSAPI_BASE_URL}/everything"
    params = {
        "q": f"{symbol} OR {company_name}",
        "language": "en",
        "from": from_date,
        "sortBy": "publishedAt",
        "pageSize": 50,
        "apiKey": config.NEWS_API_KEY,
    }

    data = _newsapi_request(url, params)
    if not data:
        logger.warning(f"[{symbol}] 뉴스 수집 실패, 빈 결과 반환")
        return []

    articles = data.get("articles", [])
    filtered = [
        {
            "title": a.get("title", ""),
            "description": a.get("description", "") or "",
            "publishedAt": a.get("publishedAt", ""),
        }
        for a in articles
        if a.get("title") and a.get("title") != "[Removed]"
    ]
    logger.info(f"[{symbol}] 뉴스 {len(filtered)}건 수집 완료")
    return filtered


def _newsapi_request(url: str, params: dict) -> dict | None:
    """
    NewsAPI HTTP GET 요청 (지수 백오프 재시도).
    Massive SDK가 아닌 NewsAPI는 여전히 raw requests 사용.
    """
    delay = config.REQUEST_RETRY_BASE_DELAY
    for attempt in range(1, config.REQUEST_MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.json()
            logger.warning(
                f"NewsAPI 오류 {response.status_code} ({attempt}/{config.REQUEST_MAX_RETRIES})"
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"NewsAPI 요청 실패: {e} ({attempt}/{config.REQUEST_MAX_RETRIES})")

        if attempt < config.REQUEST_MAX_RETRIES:
            time.sleep(delay)
            delay *= 2

    logger.error(f"NewsAPI 모든 재시도 실패: {url}")
    return None
