# Design Ref: §2.5 — 페이퍼 트레이딩 엔진: 매수/매도 판단 + 보유 기간 조정 로직
# Plan SC-02: 정규장 타이밍에 포트폴리오 업데이트
# Plan SC-03: 14일 보유 기간 조정 로직
import logging
import math
from datetime import datetime, timezone

import config
import collector
from portfolio import Portfolio, Position, Trade, apply_buy, apply_sell

logger = logging.getLogger(__name__)


def process_orders(signals: dict[str, dict], portfolio: Portfolio) -> list[Trade]:
    """
    전날 계산된 신호를 기반으로 오늘 시가에 가상 주문을 처리한다.

    Args:
        signals: {symbol: {signal, rsi, sentiment, ...}} — signals.json에서 로드
        portfolio: 현재 포트폴리오 상태

    Returns:
        체결된 Trade 목록
    """
    executed_trades: list[Trade] = []

    for symbol, signal_data in signals.items():
        signal = signal_data.get("signal", "NEUTRAL")
        logger.info(f"[{symbol}] 주문 처리 시작 (신호={signal})")

        # 오늘 시가 수집
        open_price = collector.get_latest_open_price(symbol)
        if open_price is None:
            logger.warning(f"[{symbol}] 시가 수집 실패 — 주문 스킵")
            continue

        trade = None
        if signal in ("BUY", "STRONG_BUY"):
            trade = _process_buy_signal(symbol, signal, open_price, portfolio)
        elif signal in ("NEUTRAL", "SELL", "STRONG_SELL"):
            trade = _process_sell_signal(symbol, signal, open_price, portfolio)

        if trade:
            executed_trades.append(trade)
            if trade.action == "BUY":
                apply_buy(portfolio, trade)
            else:
                apply_sell(portfolio, trade)
            logger.info(
                f"[{symbol}] {trade.action} 체결 | 가격=${trade.price:.2f} | "
                f"수량={trade.shares} | 금액=${trade.amount:,.2f}"
            )

    return executed_trades


def _process_buy_signal(
    symbol: str, signal: str, open_price: float, portfolio: Portfolio
) -> Trade | None:
    """
    매수 신호 처리 로직.

    - 포지션 없음: 신규 매수
    - 포지션 있음 + open_price < avg_price: 추가 매수
    - 포지션 있음 + open_price >= avg_price: 스킵 (추가 매수 조건 미충족)
    """
    existing = portfolio.positions.get(symbol)

    if existing:
        # Plan FR-04-3: 추가 매수 조건 — 시가 < 이전 평균 매수가
        if open_price >= existing.avg_price:
            logger.info(
                f"[{symbol}] 추가 매수 스킵 (시가=${open_price:.2f} >= 평균매수가=${existing.avg_price:.2f})"
            )
            return None
        logger.info(f"[{symbol}] 추가 매수 조건 충족 (시가 < 평균매수가)")

    shares = _calculate_shares_to_buy(portfolio.cash, open_price)
    if shares <= 0:
        logger.warning(f"[{symbol}] 가용 현금 부족 (현금=${portfolio.cash:,.2f}) — 매수 스킵")
        return None

    amount = open_price * shares
    return Trade(
        symbol=symbol,
        date=datetime.now(timezone.utc).isoformat(),
        action="BUY",
        signal=signal,
        price=open_price,
        shares=shares,
        amount=amount,
        net_profit_pct=0.0,
        net_profit_usd=0.0,
    )


def _process_sell_signal(
    symbol: str, signal: str, open_price: float, portfolio: Portfolio
) -> Trade | None:
    """
    매도 신호 처리 로직.

    - 포지션 없음: 스킵
    - SELL / STRONG_SELL: 무조건 매도
    - NEUTRAL: 순수익률 조건 충족 시에만 매도
      - 기본: net_profit > 1%
      - 보유 14일 초과: net_profit > 0.25%
    """
    pos = portfolio.positions.get(symbol)
    if not pos:
        logger.debug(f"[{symbol}] 포지션 없음 — 매도 스킵")
        return None

    net_profit_pct = (open_price - pos.avg_price) / pos.avg_price * 100
    net_profit_usd = (open_price - pos.avg_price) * pos.shares

    if signal in ("SELL", "STRONG_SELL"):
        logger.info(f"[{symbol}] {signal} — 무조건 매도 (수익률={net_profit_pct:+.2f}%)")
        return _make_sell_trade(symbol, signal, open_price, pos, net_profit_pct, net_profit_usd)

    # NEUTRAL: 수익률 조건 검사
    # Plan SC-03: 14일 경과 후 목표 수익률 하향
    holding_days = _get_holding_days(pos.buy_date)
    if holding_days >= config.HOLDING_PERIOD_DAYS:
        target_pct = config.PROFIT_TARGET_ADJUSTED_PCT
        logger.info(f"[{symbol}] 보유 {holding_days}일 경과 — 목표 수익률 {target_pct}%로 하향")
    else:
        target_pct = config.PROFIT_TARGET_PCT

    if net_profit_pct > target_pct:
        logger.info(
            f"[{symbol}] NEUTRAL 매도 조건 충족 "
            f"(수익률={net_profit_pct:+.2f}% > 목표={target_pct}%)"
        )
        return _make_sell_trade(symbol, signal, open_price, pos, net_profit_pct, net_profit_usd)

    logger.info(
        f"[{symbol}] NEUTRAL 매도 조건 미충족 "
        f"(수익률={net_profit_pct:+.2f}% <= 목표={target_pct}%) — 스킵"
    )
    return None


def _make_sell_trade(
    symbol: str, signal: str, price: float, pos: Position,
    net_profit_pct: float, net_profit_usd: float,
) -> Trade:
    return Trade(
        symbol=symbol,
        date=datetime.now(timezone.utc).isoformat(),
        action="SELL",
        signal=signal,
        price=price,
        shares=pos.shares,
        amount=price * pos.shares,
        net_profit_pct=round(net_profit_pct, 4),
        net_profit_usd=round(net_profit_usd, 2),
    )


def _calculate_shares_to_buy(available_cash: float, open_price: float) -> int:
    """
    매수 가능 주수를 계산한다.
    가용 현금의 POSITION_SIZE_PCT 범위 내에서 매수.
    """
    budget = available_cash * config.POSITION_SIZE_PCT
    shares = math.floor(budget / open_price)
    return max(0, shares)


def _get_holding_days(buy_date_str: str) -> int:
    """최초 매수일로부터 경과 일수를 계산한다."""
    try:
        buy_date = datetime.fromisoformat(buy_date_str)
        if buy_date.tzinfo is None:
            buy_date = buy_date.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - buy_date
        return delta.days
    except Exception:
        return 0
