# Plan SC-01: Massive.com(구 Polygon.io) SDK 기반 OHLCV + NewsAPI 뉴스 수집
# Migration: polygon-api-client → massive (2025-10-30 리브랜딩)
import logging
import time
from datetime import datetime, timedelta

import pandas as pd
import requests
from polygon import RESTClient

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


def get_ohlcv_range(symbol: str, from_date: str, to_date: str) -> pd.DataFrame:
    """
    Massive RESTClient로 특정 날짜 범위의 OHLCV 데이터를 수집한다.
    백테스팅에서 전체 기간 데이터를 한 번에 가져올 때 사용한다.

    Args:
        symbol: 종목 티커 (예: "AAPL")
        from_date: 시작일 "YYYY-MM-DD"
        to_date: 종료일 "YYYY-MM-DD"

    Returns:
        DataFrame with columns: [date, open, high, low, close, volume]
        수집 실패 시 빈 DataFrame.
    """
    client = _get_client()
    for attempt in range(1, config.REQUEST_MAX_RETRIES + 1):
        try:
            aggs = client.list_aggs(
                ticker=symbol,
                multiplier=1,
                timespan="day",
                from_=from_date,
                to=to_date,
                limit=500,
            )

            rows = []
            for agg in aggs:
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
                logger.warning(f"[{symbol}] OHLCV range 데이터 없음 ({from_date}~{to_date})")
                return pd.DataFrame()

            df = pd.DataFrame(rows).reset_index(drop=True)
            logger.info(f"[{symbol}] OHLCV range {len(df)}일치 수집 완료 ({from_date}~{to_date})")
            return df

        except Exception as e:
            logger.warning(f"[{symbol}] OHLCV range 수집 실패 ({attempt}/{config.REQUEST_MAX_RETRIES}): {e}")
            if attempt < config.REQUEST_MAX_RETRIES:
                time.sleep(config.REQUEST_RETRY_BASE_DELAY * (2 ** (attempt - 1)))

    logger.error(f"[{symbol}] OHLCV range 수집 최종 실패")
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


def get_news(
    symbol: str,
    days: int = None,
    from_date: str = None,
    to_date: str = None,
    limit: int = None,
) -> list[dict]:
    """
    Finnhub company-news API로 종목 관련 뉴스를 수집한다.

    Args:
        symbol: 종목 티커 (예: "AAPL")
        days: 몇 일 전부터 수집할지 (기본값: config.NEWS_LOOKBACK_DAYS).
              from_date가 지정되면 무시됨.
        from_date: 명시적 시작 날짜 "YYYY-MM-DD" (백테스팅용)
        to_date: 명시적 종료 날짜 "YYYY-MM-DD" (백테스팅용, 기본값: 오늘)
                 백테스팅에서 미래 뉴스 포함 방지(Lookahead Bias) 목적으로 반드시 지정할 것.
        limit: 최대 기사 수 (기본값: config.NEWS_MAX_ARTICLES = 100)

    Returns:
        list of {title, description, publishedAt}
        - Finnhub headline → title
        - Finnhub summary  → description
        - Finnhub datetime (unix) → publishedAt (ISO 8601)
    """
    if limit is None:
        limit = config.NEWS_MAX_ARTICLES

    if to_date is None:
        to_date = datetime.now().strftime("%Y-%m-%d")
    if from_date is None:
        if days is None:
            days = config.NEWS_LOOKBACK_DAYS
        from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    url = f"{config.FINNHUB_BASE_URL}/company-news"
    params = {
        "symbol": symbol,
        "from": from_date,
        "to": to_date,
        "token": config.FINNHUB_API_KEY,
    }

    raw = _finnhub_request(url, params)
    if raw is None:
        logger.warning(f"[{symbol}] 뉴스 수집 실패, 빈 결과 반환")
        return []

    articles = []
    for a in raw[:limit]:
        headline = a.get("headline", "").strip()
        if not headline:
            continue
        # unix timestamp → ISO 8601
        ts = a.get("datetime", 0)
        try:
            published_at = datetime.fromtimestamp(ts).isoformat()
        except (OSError, ValueError):
            published_at = ""
        articles.append({
            "title": headline,
            "description": a.get("summary", "") or "",
            "publishedAt": published_at,
        })

    logger.info(f"[{symbol}] 뉴스 {len(articles)}건 수집 완료 (Finnhub, {from_date}~{to_date})")
    return articles


def _finnhub_request(url: str, params: dict) -> list | None:
    """
    Finnhub HTTP GET 요청 (지수 백오프 재시도).

    Returns:
        list: 응답 JSON 배열. 실패 시 None.
    """
    delay = config.REQUEST_RETRY_BASE_DELAY
    for attempt in range(1, config.REQUEST_MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
            if response.status_code == 200:
                data = response.json()
                if isinstance(data, list):
                    return data
                logger.warning(f"Finnhub 응답 형식 오류 (list 아님): {type(data)}")
                return None
            logger.warning(
                f"Finnhub 오류 {response.status_code} ({attempt}/{config.REQUEST_MAX_RETRIES})"
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"Finnhub 요청 실패: {e} ({attempt}/{config.REQUEST_MAX_RETRIES})")

        if attempt < config.REQUEST_MAX_RETRIES:
            time.sleep(delay)
            delay *= 2

    logger.error(f"Finnhub 모든 재시도 실패: {url}")
    return None
