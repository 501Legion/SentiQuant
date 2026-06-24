# Design Ref: §2.6 — 포지션/거래이력/리포트 관리, portfolio.json + trades.csv 영속화
# Plan SC-04: 거래 이력 저장 + 포트폴리오 손익 계산
import csv
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import config

logger = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    shares: int
    avg_price: float       # 평균 매수가
    buy_date: str          # 최초 매수일 (ISO 8601)
    total_cost: float      # 총 투자 비용 (avg_price * shares)
    # KIS 동기화 시 실계좌 현재가를 함께 캐시 (Source of Truth).
    # 대시보드가 낡은 커밋 OHLCV 스냅샷 대신 이 값으로 평가손익을 표시한다.
    # 비-KIS 경로(백테스트/수기 입력)에서는 None → 대시보드가 스냅샷 종가로 폴백.
    current_price: float | None = None
    price_asof: str | None = None   # current_price 기준 시각 (ISO 8601)


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
    # Plan FR-19: KIS 모의투자 주문 추적용. 백테스팅/non-KIS 경로에서는 None.
    order_no: str | None = None
    kis_status: str | None = None  # "FILLED" | "REJECTED" | None


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


_TRADES_FIELDNAMES = [
    "date", "symbol", "action", "signal", "price",
    "shares", "amount", "net_profit_pct", "net_profit_usd",
    # Plan FR-19: KIS 모의투자 주문 추적
    "order_no", "kis_status",
]


def record_trade(trade: Trade) -> None:
    """
    trades.csv에 거래 이력을 한 행 추가한다 (append 모드).
    파일이 없으면 헤더를 먼저 생성한다.
    헤더가 기존 형식(9컬럼)이면 자동 백업 후 신규 헤더(11컬럼)로 재시작 (FR-19 마이그레이션).
    """
    os.makedirs(config.DATA_DIR, exist_ok=True)
    write_header = _ensure_trades_header_current()
    try:
        with open(config.TRADES_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=_TRADES_FIELDNAMES, extrasaction="ignore"
            )
            if write_header:
                writer.writeheader()
            writer.writerow(asdict(trade))
    except Exception as e:
        logger.error(f"거래 이력 저장 실패: {e}")


def reconcile_trades_from_kis(fills, trades_file: str | None = None) -> dict:
    """KIS 체결내역으로 기존 거래를 보정하고 누락 체결을 추가한다."""
    path = trades_file or config.TRADES_FILE
    rows: list[dict] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))

    def _order_key(value) -> str:
        text = str(value or "").strip()
        return text.lstrip("0") or ("0" if text else "")

    def _local_date(timestamp: str) -> str:
        try:
            return (
                datetime.fromisoformat(timestamp)
                .astimezone(ZoneInfo("Asia/Seoul"))
                .strftime("%Y-%m-%d")
            )
        except (TypeError, ValueError):
            return str(timestamp or "")[:10]

    def _trade_key(date, order_no, symbol, action):
        return (_local_date(date), _order_key(order_no), symbol, action)

    by_order = {
        _trade_key(
            row.get("date"), row.get("order_no"),
            row.get("symbol"), row.get("action"),
        ): row
        for row in rows if _order_key(row.get("order_no"))
    }
    added = updated = 0
    for fill in fills:
        key = _trade_key(
            fill.timestamp, fill.order_no, fill.symbol, fill.action)
        row = by_order.get(key)
        values = {
            "date": fill.timestamp,
            "symbol": fill.symbol,
            "action": fill.action,
            "price": str(fill.fill_price),
            "shares": str(fill.fill_shares),
            "amount": str(round(fill.fill_price * fill.fill_shares, 8)),
            "order_no": str(fill.order_no),
            "kis_status": fill.status,
        }
        if row is None:
            row = {
                **values,
                "signal": "kis_reconcile",
                "net_profit_pct": "0.0",
                "net_profit_usd": "0.0",
            }
            rows.append(row)
            by_order[key] = row
            added += 1
        else:
            row.update(values)
            updated += 1

    rows.sort(key=lambda row: row.get("date", ""))
    inventory: dict[str, dict[str, float]] = {}
    for row in rows:
        symbol = row.get("symbol", "")
        action = row.get("action", "")
        try:
            shares = int(float(row.get("shares", 0) or 0))
            price = float(row.get("price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if not symbol or shares <= 0 or price <= 0:
            continue

        state = inventory.setdefault(symbol, {"shares": 0.0, "cost": 0.0})
        if action == "BUY":
            state["shares"] += shares
            state["cost"] += shares * price
        elif action == "SELL":
            held = int(state["shares"])
            sold = min(shares, held)
            if sold > 0:
                avg_cost = state["cost"] / state["shares"]
                if row.get("signal") == "kis_reconcile":
                    pnl = (price - avg_cost) * sold
                    row["net_profit_usd"] = str(round(pnl, 8))
                    row["net_profit_pct"] = str(round(
                        pnl / (avg_cost * sold) * 100, 8))
                state["shares"] -= sold
                state["cost"] -= avg_cost * sold

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=os.path.dirname(os.path.abspath(path)),
        delete=False, suffix=".tmp",
    ) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=_TRADES_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        tmp_path = tmp.name
    os.replace(tmp_path, path)
    logger.info("[KIS] 거래 이력 정합화 완료: added=%d updated=%d", added, updated)
    return {"added": added, "updated": updated, "total": len(rows)}


def _ensure_trades_header_current() -> bool:
    """trades.csv 헤더가 신규 스키마와 일치하는지 확인.
    불일치 시 .bak 백업 후 신규 파일 생성 트리거. 반환값=헤더 작성 필요 여부.
    """
    if not os.path.exists(config.TRADES_FILE):
        return True
    try:
        with open(config.TRADES_FILE, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
        if not first_line:
            return True
        existing = [c.strip() for c in first_line.split(",")]
        if existing == _TRADES_FIELDNAMES:
            return False
        # 헤더 불일치 → 백업 후 신규 시작
        backup_path = config.TRADES_FILE + ".bak"
        if os.path.exists(backup_path):
            # 기존 백업이 있으면 타임스탬프 붙여 중복 회피
            backup_path = f"{config.TRADES_FILE}.{datetime.now().strftime('%Y%m%d%H%M%S')}.bak"
        os.rename(config.TRADES_FILE, backup_path)
        logger.warning(
            f"[trades.csv] 헤더 마이그레이션 — 기존 파일을 {backup_path}로 백업 후 신규 헤더(FR-19) 시작"
        )
        return True
    except Exception as e:
        logger.error(f"trades.csv 헤더 확인 실패: {e} — 헤더 작성 진행")
        return True


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
    """매도 체결을 포트폴리오에 반영한다.

    부분 매도는 남은 수량과 원가를 줄이고, 전량 매도일 때만 포지션을 제거한다.
    """
    portfolio.cash += trade.price * trade.shares
    pos = portfolio.positions.get(trade.symbol)
    if pos is None:
        return

    sell_shares = max(0, min(int(trade.shares), int(pos.shares)))
    remaining_shares = pos.shares - sell_shares
    if remaining_shares <= 0:
        del portfolio.positions[trade.symbol]
        return

    pos.shares = remaining_shares
    pos.total_cost = pos.avg_price * remaining_shares


# ---------- KIS 동기화 (Plan FR-11~13, Design §3.4) ----------

def sync_from_kis(portfolio: Portfolio, broker) -> Portfolio:
    """KIS 계좌 잔고를 Source of Truth로 Portfolio 객체 재구성.

    Plan FR-11: KIS 계좌를 권위로 사용해 portfolio.json은 캐시로 격하.
    buy_date 보존 정책 (Q1=a): KIS는 매수일을 알려주지 않으므로 기존 portfolio의
      buy_date를 보존한다. 신규 종목은 now()를 사용 (14일 보유 룰 영향 최소).
    """
    snap = broker.get_account()
    new_positions: dict[str, Position] = {}
    now_iso = datetime.now().isoformat()
    for symbol, p in snap.positions.items():
        prior = portfolio.positions.get(symbol)
        # KIS는 정확한 shares/avg_price를 알지만 buy_date는 모름 → 기존 보존
        buy_date = prior.buy_date if prior else now_iso
        new_positions[symbol] = Position(
            symbol=symbol,
            shares=p.shares,
            avg_price=p.avg_price,
            buy_date=buy_date,
            total_cost=p.avg_price * p.shares,
            # KIS 잔고가 돌려준 현재가를 캐시 → 대시보드 평가손익 정확도 확보.
            # current_price가 0/없으면 None으로 저장해 대시보드가 스냅샷으로 폴백.
            current_price=(p.current_price or None),
            price_asof=(now_iso if p.current_price else None),
        )
    return Portfolio(cash=snap.cash_usd, positions=new_positions)


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
