# Design Ref: §3.5 — 페이퍼 트레이딩 엔진: KIS Broker로 주문 위임 + 비즈니스 룰 보존
# Plan SC-02 (dry-run), SC-03 (실주문), SC-04 (order_no/kis_status), SC-10 (sync 후 일치)
import logging
import math
from datetime import datetime, timezone

import config
from kis_broker import Broker, PositionSnapshot
from portfolio import Portfolio, Position, Trade

logger = logging.getLogger(__name__)


# Plan SC: KIS Source of Truth — 잔고는 broker.get_account() 1회 조회로 디바운싱.
# 비즈니스 룰 (NEUTRAL 14일, 추가 매수 조건)은 기존 portfolio (caching buy_date) 사용.
_EMPTY_POS = PositionSnapshot(shares=0, avg_price=0.0, current_price=0.0)


def process_orders(
    signals: dict[str, dict],
    portfolio: Portfolio,
    broker: Broker,
    dry_run: bool = False,
) -> list[Trade]:
    """전날 계산된 신호를 기반으로 KIS 모의계좌에 실제 주문을 위임한다.

    Plan FR-05~07: trader는 KIS Broker에 위임만 하고 apply_buy/sell은 호출하지 않는다
      (Source of Truth = KIS 계좌). 호출자가 process_orders 후 sync_from_kis()로 갱신.

    Args:
        signals: {symbol: {signal, rsi, sentiment, ...}}
        portfolio: 캐시된 포트폴리오 (buy_date 등 비즈니스 룰 추적용)
        broker: KIS Broker (place_order, get_account, get_quote 위임)
        dry_run: True면 place_order 호출하지 않고 의도만 로그
    """
    executed_trades: list[Trade] = []

    # Plan SC: 디바운싱 — 잔고는 시작 시 1회 조회. 같은 round의 BUY 후 가용현금은 in-memory 차감.
    try:
        account = broker.get_account()
    except Exception as e:
        logger.error(f"[KIS] 잔고 조회 실패 — 주문 처리 중단: {e}")
        return executed_trades

    available_cash = account.cash_usd
    logger.info(f"[KIS] 잔고 조회: cash=${available_cash:,.2f}, positions={len(account.positions)}개")

    for symbol, signal_data in signals.items():
        signal = signal_data.get("signal", "NEUTRAL")
        logger.info(f"[{symbol}] 주문 처리 시작 (신호={signal})")

        kis_pos = account.positions.get(symbol, _EMPTY_POS)
        cached_pos = portfolio.positions.get(symbol)

        trade = None
        if signal in ("BUY", "STRONG_BUY"):
            trade = _process_buy(
                symbol, signal, broker, available_cash, kis_pos, dry_run
            )
            if trade and not dry_run:
                available_cash -= trade.amount  # 같은 round 내 후속 BUY 가용현금 추적
        elif signal in ("NEUTRAL", "SELL", "STRONG_SELL"):
            if kis_pos.shares <= 0:
                logger.debug(f"[{symbol}] KIS 포지션 없음 — 매도 스킵")
                continue
            trade = _process_sell(
                symbol, signal, broker, kis_pos, cached_pos, dry_run
            )

        if trade:
            executed_trades.append(trade)
            logger.info(
                f"[{symbol}] {trade.action} {trade.kis_status or 'FILLED'} "
                f"| 주문번호={trade.order_no} | 가격=${trade.price:.2f} "
                f"| 수량={trade.shares} | 금액=${trade.amount:,.2f}"
            )

    return executed_trades


def _process_buy(
    symbol: str,
    signal: str,
    broker: Broker,
    available_cash: float,
    kis_pos: PositionSnapshot,
    dry_run: bool,
) -> Trade | None:
    """매수 신호 처리.
    - 포지션 없음: 신규 매수
    - 포지션 있음 + 현재가 < KIS avg_price: 추가 매수
    - 포지션 있음 + 현재가 >= KIS avg_price: 스킵 (Plan FR-04-3)
    """
    try:
        quote = broker.get_quote(symbol)
    except Exception as e:
        logger.warning(f"[{symbol}] 현재가 조회 실패 — 매수 스킵: {e}")
        return None

    if kis_pos.shares > 0:
        if quote >= kis_pos.avg_price:
            logger.info(
                f"[{symbol}] 추가 매수 스킵 (시가=${quote:.2f} >= KIS 평균매수가=${kis_pos.avg_price:.2f})"
            )
            return None
        logger.info(f"[{symbol}] 추가 매수 조건 충족 (시가 < KIS 평균매수가)")

    shares = _calculate_shares_to_buy(available_cash, quote)
    if shares <= 0:
        logger.warning(f"[{symbol}] 가용 현금 부족 (cash=${available_cash:,.2f}) — 매수 스킵")
        return None

    if dry_run:
        logger.info(f"[DRY-RUN] BUY {symbol} {shares}주 @${quote:.2f} (총 ${quote*shares:,.2f})")
        return None

    result = broker.place_order(symbol, "BUY", shares)
    if result.status == "PENDING":
        logger.info(f"[{symbol}] BUY 주문 접수: order_no={result.order_no} — 체결 확인 대기")
        return None
    if result.status != "FILLED":
        logger.warning(f"[{symbol}] BUY 거부: {result.error_msg}")
        return None

    fill_price = result.fill_price or quote
    fill_shares = result.fill_shares or shares
    return Trade(
        symbol=symbol,
        date=result.timestamp,
        action="BUY",
        signal=signal,
        price=fill_price,
        shares=fill_shares,
        amount=fill_price * fill_shares,
        net_profit_pct=0.0,
        net_profit_usd=0.0,
        order_no=result.order_no,
        kis_status=result.status,
    )


def _process_sell(
    symbol: str,
    signal: str,
    broker: Broker,
    kis_pos: PositionSnapshot,
    cached_pos: Position | None,
    dry_run: bool,
) -> Trade | None:
    """매도 신호 처리.
    - SELL / STRONG_SELL: 무조건 매도
    - NEUTRAL: 순수익률 조건 충족 시에만 (기본 1%, 14일 경과 후 0.25%)
    KIS Source of Truth: shares/avg_price는 KIS, buy_date는 캐시(cached_pos).
    """
    try:
        quote = broker.get_quote(symbol)
    except Exception as e:
        logger.warning(f"[{symbol}] 현재가 조회 실패 — 매도 스킵: {e}")
        return None

    avg_price = kis_pos.avg_price
    shares = kis_pos.shares
    net_profit_pct = (quote - avg_price) / avg_price * 100 if avg_price > 0 else 0.0
    net_profit_usd = (quote - avg_price) * shares

    if signal in ("SELL", "STRONG_SELL"):
        logger.info(f"[{symbol}] {signal} — 무조건 매도 (수익률={net_profit_pct:+.2f}%)")
        return _execute_sell(symbol, signal, broker, quote, shares, net_profit_pct, net_profit_usd, dry_run)

    # NEUTRAL: 수익률 조건 검사. 14일 경과 시 목표 하향 (Plan SC-03)
    holding_days = _get_holding_days(cached_pos.buy_date if cached_pos else None)
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
        return _execute_sell(symbol, signal, broker, quote, shares, net_profit_pct, net_profit_usd, dry_run)

    logger.info(
        f"[{symbol}] NEUTRAL 매도 조건 미충족 "
        f"(수익률={net_profit_pct:+.2f}% <= 목표={target_pct}%) — 스킵"
    )
    return None


def _execute_sell(
    symbol: str,
    signal: str,
    broker: Broker,
    quote: float,
    shares: int,
    net_profit_pct: float,
    net_profit_usd: float,
    dry_run: bool,
) -> Trade | None:
    if dry_run:
        logger.info(f"[DRY-RUN] SELL {symbol} {shares}주 @${quote:.2f} (총 ${quote*shares:,.2f})")
        return None

    result = broker.place_order(symbol, "SELL", shares)
    if result.status == "PENDING":
        logger.info(f"[{symbol}] SELL 주문 접수: order_no={result.order_no} — 체결 확인 대기")
        return None
    if result.status != "FILLED":
        logger.warning(f"[{symbol}] SELL 거부: {result.error_msg}")
        return None

    fill_price = result.fill_price or quote
    fill_shares = result.fill_shares or shares
    # 거부 가능성 있는 SELL 응답이 다른 수량을 반환할 수 있어 수익률 재계산
    return Trade(
        symbol=symbol,
        date=result.timestamp,
        action="SELL",
        signal=signal,
        price=fill_price,
        shares=fill_shares,
        amount=fill_price * fill_shares,
        net_profit_pct=round(net_profit_pct, 4),
        net_profit_usd=round(net_profit_usd, 2),
        order_no=result.order_no,
        kis_status=result.status,
    )


def _calculate_shares_to_buy(available_cash: float, price: float) -> int:
    """매수 가능 주수 — 가용 현금의 POSITION_SIZE_PCT 범위 내 floor."""
    if price <= 0:
        return 0
    budget = available_cash * config.POSITION_SIZE_PCT
    shares = math.floor(budget / price)
    return max(0, shares)


def _get_holding_days(buy_date_str: str | None) -> int:
    """최초 매수일로부터 경과 일수."""
    if not buy_date_str:
        return 0
    try:
        buy_date = datetime.fromisoformat(buy_date_str)
        if buy_date.tzinfo is None:
            buy_date = buy_date.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - buy_date
        return delta.days
    except Exception:
        return 0
