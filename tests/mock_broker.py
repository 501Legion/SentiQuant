"""MockBroker — Broker Protocol을 만족하는 테스트용 인메모리 구현.

Design Ref: §8.1 — 단위 테스트 인프라. 실제 KIS API 호출 없이 trader/portfolio의
주문 흐름을 검증한다. tradable 목록에 없는 종목은 place_order에서 REJECTED를 반환한다.
"""
from __future__ import annotations

from datetime import datetime, timezone

from kis_broker import AccountSnapshot, FillRecord, OrderResult, PositionSnapshot


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MockBroker:
    """인메모리 Broker — Broker Protocol 암묵 만족 (connect/place_order/get_account/
    get_quote/get_tradable_symbols)."""

    def __init__(
        self,
        initial_cash: float = 10000.0,
        tradable: list[str] | None = None,
        quote: float = 100.0,
        positions: dict[str, PositionSnapshot] | None = None,
    ):
        self._cash = initial_cash
        self._tradable = tradable if tradable is not None else ["AAPL", "MSFT"]
        self._quote = quote
        self._positions: dict[str, PositionSnapshot] = dict(positions or {})
        self._order_seq = 0
        self._fills: list[FillRecord] = []

    def connect(self) -> None:
        pass

    def get_quote(self, symbol: str) -> float:
        return self._quote

    def place_order(self, symbol, action, shares, price=None) -> OrderResult:
        if symbol not in self._tradable:
            return OrderResult(
                order_no="", status="REJECTED",
                fill_price=None, fill_shares=None,
                timestamp=_iso(), error_msg=f"{symbol} not tradable",
            )
        px = price if price else self._quote
        self._order_seq += 1
        if action == "BUY":
            self._cash -= px * shares
            prev = self._positions.get(symbol)
            new_shares = (prev.shares if prev else 0) + shares
            self._positions[symbol] = PositionSnapshot(
                shares=new_shares, avg_price=px, current_price=px,
            )
        else:  # SELL
            self._cash += px * shares
            self._positions.pop(symbol, None)
        self._fills.append(FillRecord(
            order_no=str(self._order_seq), symbol=symbol, action=action,
            fill_price=px, fill_shares=shares, timestamp=_iso(),
        ))
        return OrderResult(
            order_no=str(self._order_seq), status="FILLED",
            fill_price=px, fill_shares=shares,
            timestamp=_iso(), error_msg=None,
        )

    def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(
            cash_usd=self._cash,
            positions=dict(self._positions),
            updated_at=_iso(),
        )

    def get_order_history(self, start_date: str, end_date: str) -> list[FillRecord]:
        return list(self._fills)

    def get_tradable_symbols(self) -> list[str]:
        return list(self._tradable)
