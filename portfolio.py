# Design Ref: §2.6 — 포지션/거래이력/리포트 관리, portfolio.json + trades.csv 영속화
# Plan SC-04: 거래 이력 저장 + 포트폴리오 손익 계산
import csv
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime

import config

logger = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    shares: int
    avg_price: float       # 평균 매수가
    buy_date: str          # 최초 매수일 (ISO 8601)
    total_cost: float      # 총 투자 비용 (avg_price * shares)


@dataclass
class Trade:
    symbol: str
    date: str              # 체결일 (ISO 8601)
    action: str            # "BUY" | "SELL"
    signal: str            # 신호명
    price: float           # 체결가
    shares: int
    amount: float          # price * shares
    net_profit_pct: float  # 매도 시 순수익률 (매수 시 0.0)
    net_profit_usd: float  # 매도 시 순수익 USD (매수 시 0.0)


@dataclass
class Portfolio:
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)


# ---------- 로드/저장 ----------

def load_portfolio() -> Portfolio:
    """
    portfolio.json에서 포트폴리오를 로드한다.
    파일이 없으면 초기 상태($INITIAL_CASH)를 반환한다.
    """
    os.makedirs(config.DATA_DIR, exist_ok=True)
    if not os.path.exists(config.PORTFOLIO_FILE):
        logger.info(f"portfolio.json 없음 — 초기 포트폴리오 생성 (${config.INITIAL_CASH:,.0f})")
        return Portfolio(cash=config.INITIAL_CASH)

    try:
        with open(config.PORTFOLIO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        positions = {
            symbol: Position(**pos_data)
            for symbol, pos_data in data.get("positions", {}).items()
        }
        portfolio = Portfolio(cash=data["cash"], positions=positions)
        logger.info(f"포트폴리오 로드 완료 (현금=${portfolio.cash:,.2f}, 보유종목={len(portfolio.positions)}개)")
        return portfolio
    except Exception as e:
        logger.error(f"portfolio.json 로드 실패: {e} — 초기 상태로 복구")
        return Portfolio(cash=config.INITIAL_CASH)


def save_portfolio(portfolio: Portfolio) -> None:
    """
    portfolio.json에 포트폴리오를 저장한다 (atomic write).
    임시 파일에 먼저 쓴 후 원본을 교체해 데이터 손상을 방지한다.
    """
    os.makedirs(config.DATA_DIR, exist_ok=True)
    data = {
        "cash": portfolio.cash,
        "positions": {
            symbol: asdict(pos)
            for symbol, pos in portfolio.positions.items()
        },
        "updated_at": datetime.now().isoformat(),
    }
    try:
        dir_path = os.path.dirname(os.path.abspath(config.PORTFOLIO_FILE))
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=dir_path, delete=False, suffix=".tmp"
        ) as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name
        os.replace(tmp_path, config.PORTFOLIO_FILE)
        logger.info("포트폴리오 저장 완료")
    except Exception as e:
        logger.error(f"포트폴리오 저장 실패: {e}")


def record_trade(trade: Trade) -> None:
    """
    trades.csv에 거래 이력을 한 행 추가한다 (append 모드).
    파일이 없으면 헤더를 먼저 생성한다.
    """
    os.makedirs(config.DATA_DIR, exist_ok=True)
    fieldnames = [
        "date", "symbol", "action", "signal", "price",
        "shares", "amount", "net_profit_pct", "net_profit_usd",
    ]
    file_exists = os.path.exists(config.TRADES_FILE)
    try:
        with open(config.TRADES_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            writer.writerow(asdict(trade))
    except Exception as e:
        logger.error(f"거래 이력 저장 실패: {e}")


def save_signals(signals: dict) -> None:
    """signals.json을 최신 신호로 갱신한다."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    data = {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "signals": signals,
    }
    try:
        with open(config.SIGNALS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"signals.json 저장 실패: {e}")


def load_signals() -> dict:
    """signals.json에서 가장 최근 신호를 로드한다."""
    if not os.path.exists(config.SIGNALS_FILE):
        logger.warning("signals.json 없음 — 신호 없음 반환")
        return {}
    try:
        with open(config.SIGNALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("signals", {})
    except Exception as e:
        logger.error(f"signals.json 로드 실패: {e}")
        return {}


# ---------- 포지션 업데이트 ----------

def apply_buy(portfolio: Portfolio, trade: Trade) -> None:
    """
    매수 체결을 포트폴리오에 반영한다.
    기존 포지션이 있으면 평균 매수가를 재계산한다.
    """
    cost = trade.price * trade.shares
    if trade.symbol in portfolio.positions:
        pos = portfolio.positions[trade.symbol]
        total_shares = pos.shares + trade.shares
        total_cost = pos.total_cost + cost
        pos.shares = total_shares
        pos.avg_price = total_cost / total_shares
        pos.total_cost = total_cost
    else:
        portfolio.positions[trade.symbol] = Position(
            symbol=trade.symbol,
            shares=trade.shares,
            avg_price=trade.price,
            buy_date=trade.date,
            total_cost=cost,
        )
    portfolio.cash -= cost


def apply_sell(portfolio: Portfolio, trade: Trade) -> None:
    """매도 체결을 포트폴리오에 반영한다. 포지션을 제거하고 현금을 증가시킨다."""
    portfolio.cash += trade.price * trade.shares
    if trade.symbol in portfolio.positions:
        del portfolio.positions[trade.symbol]


# ---------- 리포트 출력 ----------

def print_portfolio_report(portfolio: Portfolio, current_prices: dict[str, float] = None) -> None:
    """
    현재 포트폴리오 현황을 콘솔에 출력한다.

    Args:
        portfolio: Portfolio 객체
        current_prices: {symbol: 현재가} (선택, 없으면 평가손익 미표시)
    """
    print(f"\n{'='*60}")
    print(f"{'포트폴리오 현황':^58}")
    print(f"{'='*60}")
    print(f"  가용 현금:   ${portfolio.cash:>12,.2f}")

    if not portfolio.positions:
        print("  보유 종목:   없음")
    else:
        print(f"\n  {'종목':<6} {'수량':>6} {'평균매수가':>12} {'평가가':>12} {'손익(%)':>10}")
        print(f"  {'-'*52}")
        total_market_value = 0.0
        total_cost = 0.0
        for symbol, pos in portfolio.positions.items():
            current_price = (current_prices or {}).get(symbol)
            if current_price:
                market_value = current_price * pos.shares
                profit_pct = (current_price - pos.avg_price) / pos.avg_price * 100
                profit_str = f"{profit_pct:+.2f}%"
                price_str = f"${current_price:,.2f}"
                total_market_value += market_value
                total_cost += pos.total_cost
            else:
                price_str = "N/A"
                profit_str = "N/A"
                total_market_value += pos.total_cost
                total_cost += pos.total_cost
            print(
                f"  {symbol:<6} {pos.shares:>6,} ${pos.avg_price:>11,.2f} "
                f"{price_str:>12} {profit_str:>10}"
            )

    total_value = portfolio.cash + sum(
        (current_prices or {}).get(sym, pos.avg_price) * pos.shares
        for sym, pos in portfolio.positions.items()
    )
    total_profit_pct = (total_value - config.INITIAL_CASH) / config.INITIAL_CASH * 100
    print(f"\n  총 포트폴리오 가치: ${total_value:>12,.2f}")
    print(f"  초기 자금 대비:     {total_profit_pct:>+.2f}%")
    print(f"{'='*60}\n")
