# Design Ref: §6 — 백테스팅 엔진 (모델별 분리 + 캐시)
# Plan SC-01~02: 모델별 수익률/승률/MDD 비교 출력
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

import collector
import config
import indicators
import sentiment_provider as sp
from signals import determine_signal

logger = logging.getLogger(__name__)

BACKTEST_CACHE_VERSION = "v2"


# ---------------------------------------------------------------------------
# 데이터 클래스
# ---------------------------------------------------------------------------

@dataclass
class TradeRecord:
    symbol: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    pnl_pct: float        # (exit - entry) / entry * 100
    is_win: bool


@dataclass
class BacktestResult:
    model: str
    total_return_pct: float
    trade_count: int
    win_rate_pct: float
    mdd_pct: float        # Max Drawdown (%)
    per_symbol: dict[str, dict]
    trades: list[TradeRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# BacktestEngine
# ---------------------------------------------------------------------------

class BacktestEngine:
    """
    고정 기간(BACKTEST_START ~ BACKTEST_END) 백테스팅 엔진.
    # Design Ref: §2.7 — BacktestEngine + commission 공제

    모델 선택:
      "textblob"  → TextBlobProvider만 사용
      "finbert"   → FinBERTProvider(neutral 필터 포함)만 사용
      "combined"  → FinBERT + TextBlob 평균
      "gpt5"     → GPTProvider (실제 호출 모델: config.GPT_MODEL)
    """

    _VALID_MODELS = ("textblob", "finbert", "combined", config.GPT_MODEL_ALIAS)

    def __init__(self, model: str):
        if model not in self._VALID_MODELS:
            raise ValueError(
                f"model은 {self._VALID_MODELS} 중 하나여야 합니다: {model}"
            )
        self.model = model
        self._cache: dict = _load_backtest_cache()
        self._sentiment_cache: dict = self._cache.setdefault("sentiment", {})
        self._providers = self._build_providers()

    def _build_providers(self) -> list[sp.SentimentProvider]:
        if self.model == "textblob":
            return [sp.TextBlobProvider()]
        if self.model == "finbert":
            return [sp.FinBERTProvider()]
        if self.model == config.GPT_MODEL_ALIAS:
            return [sp.GPTProvider()]
        return [sp.FinBERTProvider(), sp.TextBlobProvider()]  # combined

    def run(self, symbols: list[str]) -> BacktestResult:
        """
        BACKTEST_START ~ BACKTEST_END 기간 백테스팅 실행.

        알고리즘:
        1. 거래일 목록 생성 (NYSE 영업일)
        2. 종목별 전체 OHLCV 사전 수집
        3. 날짜별 신호 계산 (캐시 활용)
        4. 거래 시뮬레이션
        5. 성과 지표 계산
        """
        logger.info(
            f"[Backtest] 시작 — model={self.model}"
            f" ({config.BACKTEST_START} ~ {config.BACKTEST_END})"
        )
        trading_days = _get_trading_days(config.BACKTEST_START, config.BACKTEST_END)

        all_trades: list[TradeRecord] = []
        per_symbol: dict[str, dict] = {}

        for symbol in symbols:
            logger.info(f"[Backtest] {symbol} 처리 중...")
            try:
                trades = self._run_symbol(symbol, trading_days)
                all_trades.extend(trades)
                per_symbol[symbol] = _summarize_trades(trades)
            except Exception as e:
                logger.error(f"[Backtest] {symbol} 처리 실패: {e}", exc_info=True)

        # 캐시 저장
        _save_backtest_cache(self._cache)

        total_return = _calc_total_return(all_trades)
        win_rate = _calc_win_rate(all_trades)
        mdd = _calc_mdd(all_trades)

        logger.info(
            f"[Backtest] 완료 — model={self.model}"
            f" | 수익률={total_return:+.1f}%"
            f" | 거래={len(all_trades)}회"
            f" | 승률={win_rate:.1f}%"
            f" | MDD={mdd:.1f}%"
        )
        return BacktestResult(
            model=self.model,
            total_return_pct=total_return,
            trade_count=len(all_trades),
            win_rate_pct=win_rate,
            mdd_pct=mdd,
            per_symbol=per_symbol,
            trades=all_trades,
        )

    def _run_symbol(
        self,
        symbol: str,
        trading_days: list[str],
    ) -> list[TradeRecord]:
        """종목 단위 신호 계산 + 거래 시뮬레이션."""
        # 전체 기간 OHLCV 사전 수집 (Polygon)
        start_dt = datetime.strptime(config.BACKTEST_START, "%Y-%m-%d")
        # RSI 계산 버퍼 포함 (70거래일 전부터)
        ohlcv_start = (start_dt - timedelta(days=100)).strftime("%Y-%m-%d")

        ohlcv_full = _get_ohlcv_snapshot(symbol, ohlcv_start, config.BACKTEST_END)
        if ohlcv_full.empty:
            logger.warning(f"[Backtest] {symbol} OHLCV 없음 — 스킵")
            return []

        # 날짜 → {open, high, low, close, volume} 조회용 dict
        ohlcv_by_date: dict[str, dict] = {
            row["date"]: row for _, row in ohlcv_full.iterrows()
        }

        signals_by_date: dict[str, str] = {}

        for date_str in trading_days:
            # 해당 날짜까지의 OHLCV 슬라이스
            slice_df = ohlcv_full[ohlcv_full["date"] <= date_str].copy()
            if len(slice_df) < config.RSI_PERIOD + 1:
                continue

            rsi, _ = indicators.get_latest_rsi(symbol, slice_df)
            if rsi is None:
                continue

            sentiment = self._get_sentiment(symbol, date_str)
            signal = determine_signal(rsi, sentiment)
            signals_by_date[date_str] = signal

        return self._simulate_trades(symbol, signals_by_date, ohlcv_by_date, trading_days)

    def _get_sentiment(self, symbol: str, date_str: str) -> float:
        """
        캐시 조회 → 미스 시 Finnhub + Provider 계산 후 캐시 저장.
        캐시 키: "{symbol}_{date_str}_{model}"
        """
        cache_key = _sentiment_cache_key(symbol, date_str, self.model)
        if cache_key in self._sentiment_cache:
            return self._sentiment_cache[cache_key]

        news_from = (
            datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=7)
        ).strftime("%Y-%m-%d")
        articles = _get_news_snapshot(symbol, news_from, date_str)

        scores = []
        for provider in self._providers:
            score, _ = provider.score(articles)
            scores.append(score)

        sentiment = round(sum(scores) / len(scores), 2) if scores else 50.0
        self._sentiment_cache[cache_key] = sentiment
        logger.info(
            f"[Backtest] {symbol} {date_str}: 뉴스 {len(articles)}건"
            f" | sentiment={sentiment:.1f} ({self.model})"
        )
        return sentiment

    def _simulate_trades(
        self,
        symbol: str,
        signals_by_date: dict[str, str],
        ohlcv_by_date: dict[str, dict],
        trading_days: list[str],
    ) -> list[TradeRecord]:
        """
        거래 시뮬레이션.

        규칙:
        - BUY/STRONG_BUY 신호 → 다음 거래일 시가로 진입 (포지션 없을 때만)
        - Exit 조건:
          1. 진입가 대비 1% 이상 수익 → 다음 날 종가 청산
          2. 14 거래일 경과 → 해당일 종가 청산
          3. SELL/STRONG_SELL 신호 보유 중 → 다음 날 시가 청산
        """
        trades: list[TradeRecord] = []
        position: dict | None = None  # {entry_date, entry_price, days_held}

        for i, date_str in enumerate(trading_days):
            ohlcv = ohlcv_by_date.get(date_str)
            if ohlcv is None:
                continue

            current_close = float(ohlcv["close"])
            current_open = float(ohlcv["open"])
            signal = signals_by_date.get(date_str, "NEUTRAL")

            # 보유 중 → 청산 조건 체크
            if position is not None:
                position["days_held"] += 1
                pnl = (current_close - position["entry_price"]) / position["entry_price"] * 100

                should_exit = (
                    pnl >= 1.0                         # 1% 수익 목표
                    or position["days_held"] >= 14     # 14거래일 경과
                    or signal in ("SELL", "STRONG_SELL")
                )

                if should_exit:
                    exit_price = current_close
                    gross_pnl = (exit_price - position["entry_price"]) / position["entry_price"] * 100
                    # Plan SC NFR-07: 수수료 공제 (한국투자증권 0.25%, 최소 $2)
                    net_pnl = gross_pnl - _calc_commission_pct(position["entry_price"])
                    trades.append(TradeRecord(
                        symbol=symbol,
                        entry_date=position["entry_date"],
                        entry_price=position["entry_price"],
                        exit_date=date_str,
                        exit_price=exit_price,
                        pnl_pct=round(net_pnl, 2),
                        is_win=net_pnl > 0,
                    ))
                    position = None

            # 포지션 없고 BUY 신호 → 진입
            if position is None and signal in ("BUY", "STRONG_BUY"):
                position = {
                    "entry_date": date_str,
                    "entry_price": current_open,
                    "days_held": 0,
                }

        # 기간 종료 시 미청산 포지션 강제 청산
        if position is not None and trading_days:
            last_date = trading_days[-1]
            last_ohlcv = ohlcv_by_date.get(last_date)
            if last_ohlcv is not None:
                exit_price = float(last_ohlcv["close"])
                gross_pnl = (exit_price - position["entry_price"]) / position["entry_price"] * 100
                net_pnl = gross_pnl - _calc_commission_pct(position["entry_price"])
                trades.append(TradeRecord(
                    symbol=symbol,
                    entry_date=position["entry_date"],
                    entry_price=position["entry_price"],
                    exit_date=last_date,
                    exit_price=exit_price,
                    pnl_pct=round(net_pnl, 2),
                    is_win=net_pnl > 0,
                ))

        return trades


# ---------------------------------------------------------------------------
# 유틸리티 함수
# ---------------------------------------------------------------------------

def run_all_models(
    symbols: list[str],
    models: tuple[str, ...] = ("textblob", "finbert", "combined"),
) -> dict[str, BacktestResult]:
    """
    지정 모델 목록 순차 실행.
    # Plan SC FR-14: --model gpt5 추가로 뉴스 3종 비교 가능

    Args:
        symbols: 백테스팅 대상 종목 리스트
        models: 실행할 모델 튜플 (기본: textblob/finbert/combined)

    Returns: {model_name: BacktestResult}
    """
    results = {}
    for model in models:
        logger.info(f"[Backtest] 모델 {model} 시작...")
        engine = BacktestEngine(model)
        results[model] = engine.run(symbols)
    return results


def _calc_buy_and_hold(symbols: list[str]) -> dict[str, float]:
    """종목별 Buy & Hold 수익률 계산 (백테스팅 기간 기준)."""
    bnh: dict[str, float] = {}
    for symbol in symbols:
        try:
            df = _get_ohlcv_snapshot(symbol, config.BACKTEST_START, config.BACKTEST_END)
            if df.empty or len(df) < 2:
                continue
            start_price = float(df.iloc[0]["open"])
            end_price = float(df.iloc[-1]["close"])
            bnh[symbol] = round((end_price - start_price) / start_price * 100, 1)
        except Exception as e:
            logger.warning(f"[B&H] {symbol} 수익률 계산 실패: {e}")
    return bnh


def print_comparison(results: dict[str, BacktestResult]) -> None:
    """모델별 결과 비교 출력 (Plan §4 포맷)."""
    print(f"\n{'='*55}")
    print(f"  백테스팅 결과 ({config.BACKTEST_START} ~ {config.BACKTEST_END})")
    print(f"{'='*55}")

    for model_name, result in results.items():
        sign = "+" if result.total_return_pct >= 0 else ""
        print(f"\n  모델: {model_name.upper()}")
        print(
            f"    총 수익률: {sign}{result.total_return_pct:.1f}%"
            f" | 거래: {result.trade_count}회"
            f" | 승률: {result.win_rate_pct:.1f}%"
            f" | MDD: {result.mdd_pct:.1f}%"
        )

    # 종목별 상세 + Buy & Hold 비교
    if results:
        ref_model = next(iter(results))
        ref_result = results[ref_model]
        symbols = list(ref_result.per_symbol.keys())
        bnh = _calc_buy_and_hold(symbols)

        print(f"\n  종목별 ({ref_model.upper()} 기준 vs Buy&Hold):")
        for symbol, stats in ref_result.per_symbol.items():
            sign = "+" if stats["return_pct"] >= 0 else ""
            bnh_val = bnh.get(symbol)
            bnh_str = f"{bnh_val:+.1f}%" if bnh_val is not None else "N/A"
            diff = stats["return_pct"] - bnh_val if bnh_val is not None else None
            diff_str = f"({diff:+.1f}%p)" if diff is not None else ""
            print(
                f"    {symbol:<6} | 전략: {sign}{stats['return_pct']:.1f}%"
                f" | B&H: {bnh_str} {diff_str}"
                f" | {stats['trade_count']}거래"
            )

    print(f"\n  * Threshold 조정 후 재실행: python main.py --backtest --model finbert")
    print(f"  * config.py에서 SENTIMENT_BUY, RSI_OVERSOLD 값을 변경하세요")
    print(f"{'='*55}\n")


def _calc_commission_pct(entry_price: float) -> float:
    """
    뉴스 백테스팅용 수수료 퍼센트 계산 (매수+매도 합산).
    # Plan SC NFR-07: 한국투자증권 0.25%, 최소 $2 per leg
    포지션 크기: INITIAL_CASH × POSITION_SIZE_PCT
    """
    position_value = config.INITIAL_CASH * config.POSITION_SIZE_PCT
    buy_comm = max(position_value * config.COMMISSION_RATE, config.COMMISSION_MIN_USD)
    sell_comm = max(position_value * config.COMMISSION_RATE, config.COMMISSION_MIN_USD)
    return (buy_comm + sell_comm) / position_value * 100


def _get_trading_days(start: str, end: str) -> list[str]:
    """NYSE 영업일 목록 반환 (pandas_market_calendars 사용)."""
    try:
        import pandas_market_calendars as mcal
        nyse = mcal.get_calendar("NYSE")
        schedule = nyse.schedule(start_date=start, end_date=end)
        return [d.strftime("%Y-%m-%d") for d in schedule.index]
    except Exception as e:
        logger.warning(f"거래일 캘린더 로드 실패: {e} — 주말 제외 방식으로 폴백")
        # 폴백: 주말 제외 (공휴일 미반영)
        days = []
        current = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        while current <= end_dt:
            if current.weekday() < 5:  # 월~금
                days.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
        return days


def _get_ohlcv_snapshot(symbol: str, from_date: str, to_date: str) -> pd.DataFrame:
    """백테스트 OHLCV를 파일 스냅샷으로 고정해 재실행 결과를 안정화한다."""
    path = _snapshot_path("ohlcv", f"{symbol}_{from_date}_{to_date}.csv")
    if os.path.exists(path):
        df = pd.read_csv(path)
        return _normalize_ohlcv_df(df)

    df = collector.get_ohlcv_range(symbol, from_date, to_date)
    if df.empty:
        return df

    df = _normalize_ohlcv_df(df)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)
    logger.info(f"[Backtest] OHLCV 스냅샷 저장: {path}")
    return df


def _get_news_snapshot(symbol: str, from_date: str, to_date: str) -> list[dict]:
    """백테스트 뉴스 원문을 파일 스냅샷으로 고정한다."""
    path = _snapshot_path("news", f"{symbol}_{from_date}_{to_date}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                return _sort_articles(raw)[:config.NEWS_MAX_ARTICLES]
            logger.warning(f"[Backtest] 뉴스 스냅샷 형식 오류: {path}")
        except json.JSONDecodeError:
            logger.warning(f"[Backtest] 뉴스 스냅샷 손상, 재수집: {path}")

    # Finnhub rate limit 대응: API 호출이 필요할 때만 delay
    time.sleep(config.FINNHUB_REQUEST_DELAY)
    articles = collector.get_news(
        symbol,
        from_date=from_date,
        to_date=to_date,
        limit=config.NEWS_MAX_ARTICLES,
    )
    articles = _sort_articles(articles)[:config.NEWS_MAX_ARTICLES]

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)
    logger.info(f"[Backtest] 뉴스 스냅샷 저장: {path}")
    return articles


def _snapshot_path(kind: str, filename: str) -> str:
    safe_filename = re.sub(r"[^A-Za-z0-9_.-]", "_", filename)
    return os.path.join(
        config.BACKTEST_SNAPSHOT_DIR,
        BACKTEST_CACHE_VERSION,
        kind,
        safe_filename,
    )


def _normalize_ohlcv_df(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df[["date", "open", "high", "low", "close", "volume"]]
        .copy()
        .sort_values("date")
        .drop_duplicates(subset=["date"], keep="last")
        .reset_index(drop=True)
    )


def _sort_articles(articles: list[dict]) -> list[dict]:
    return sorted(
        articles,
        key=lambda a: (
            a.get("publishedAt", ""),
            a.get("title", ""),
            a.get("description", ""),
        ),
    )


def _sentiment_cache_key(symbol: str, date_str: str, model: str) -> str:
    settings = [
        BACKTEST_CACHE_VERSION,
        f"model={model}",
        f"symbol={symbol}",
        f"date={date_str}",
        f"limit={config.NEWS_MAX_ARTICLES}",
        f"neutral={config.NEUTRAL_FILTER_THRESHOLD}",
        f"min={config.NEUTRAL_FILTER_MIN_ARTICLES}",
    ]
    return "|".join(settings)


def _summarize_trades(trades: list[TradeRecord]) -> dict:
    """종목별 성과 요약."""
    if not trades:
        return {"return_pct": 0.0, "trade_count": 0, "win_rate_pct": 0.0}
    total = sum(t.pnl_pct for t in trades)
    wins = sum(1 for t in trades if t.is_win)
    return {
        "return_pct": round(total, 2),
        "trade_count": len(trades),
        "win_rate_pct": round(wins / len(trades) * 100, 1),
    }


def _calc_total_return(trades: list[TradeRecord]) -> float:
    """전체 수익률 = 거래별 P&L 합산."""
    if not trades:
        return 0.0
    return round(sum(t.pnl_pct for t in trades), 2)


def _calc_win_rate(trades: list[TradeRecord]) -> float:
    """승률 = 수익 거래 / 전체 거래."""
    if not trades:
        return 0.0
    return round(sum(1 for t in trades if t.is_win) / len(trades) * 100, 1)


def _calc_mdd(trades: list[TradeRecord]) -> float:
    """
    Max Drawdown 계산.
    거래별 누적 P&L 기준으로 최대 낙폭을 계산한다.
    """
    if not trades:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in trades:
        cumulative += t.pnl_pct
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    return round(-max_dd, 2)


def _load_backtest_cache() -> dict:
    """data/backtest_cache.json 로드. 없거나 손상 시 {} 반환."""
    try:
        with open(config.BACKTEST_CACHE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            if "sentiment" in raw and isinstance(raw["sentiment"], dict):
                return raw
            if isinstance(raw, dict):
                return {
                    "version": BACKTEST_CACHE_VERSION,
                    "sentiment": raw,
                }
            return {
                "version": BACKTEST_CACHE_VERSION,
                "sentiment": {},
            }
    except FileNotFoundError:
        return {
            "version": BACKTEST_CACHE_VERSION,
            "sentiment": {},
        }
    except json.JSONDecodeError:
        logger.warning("backtest_cache.json 손상 — 빈 캐시로 재시작")
        return {
            "version": BACKTEST_CACHE_VERSION,
            "sentiment": {},
        }


def _save_backtest_cache(cache: dict) -> None:
    """data/backtest_cache.json 저장."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    cache["version"] = BACKTEST_CACHE_VERSION
    with open(config.BACKTEST_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
