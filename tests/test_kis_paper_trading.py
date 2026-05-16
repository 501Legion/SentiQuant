"""KIS Paper Trading 단위 테스트.

Design Ref: §8.2 — 시나리오 T1~T9. MockBroker로 실제 KIS API 호출 없이
trader / portfolio / signal_provider / signals 필터링을 검증한다.

실행 방법:
  pytest tests/test_kis_paper_trading.py          # pytest 설치 시
  python tests/test_kis_paper_trading.py          # 단독 실행 (pytest 불필요)
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile

# 프로젝트 루트 + tests 디렉토리를 sys.path에 추가 (pytest / 단독 실행 양쪽 지원)
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
import kis_broker
import portfolio as portfolio_mod
import signal_provider
import trader
from kis_broker import PositionSnapshot
from mock_broker import MockBroker


# --- T1: 신규 매수 체결 ---------------------------------------------------
def test_t1_buy_filled():
    broker = MockBroker(initial_cash=10000.0, tradable=["AAPL"], quote=100.0)
    port = portfolio_mod.Portfolio(cash=0.0)
    trades = trader.process_orders({"AAPL": {"signal": "BUY"}}, port, broker)

    assert len(trades) == 1, f"expected 1 trade, got {len(trades)}"
    t = trades[0]
    expected_shares = math.floor(10000.0 * config.POSITION_SIZE_PCT / 100.0)
    assert t.action == "BUY"
    assert t.shares == expected_shares, f"shares {t.shares} != {expected_shares}"
    assert t.kis_status == "FILLED"
    assert t.order_no, "order_no should be set"


# --- T2: 매매 불가 종목 매수 거부 ----------------------------------------
def test_t2_buy_rejected_not_tradable():
    broker = MockBroker(initial_cash=10000.0, tradable=["AAPL"], quote=100.0)
    port = portfolio_mod.Portfolio(cash=0.0)
    trades = trader.process_orders({"NVDA": {"signal": "BUY"}}, port, broker)

    assert trades == [], "non-tradable BUY should yield no trade"


# --- T3: dry-run 시 실주문 없음 ------------------------------------------
def test_t3_dry_run_no_order():
    broker = MockBroker(initial_cash=10000.0, tradable=["AAPL"], quote=100.0)
    port = portfolio_mod.Portfolio(cash=0.0)
    trades = trader.process_orders(
        {"AAPL": {"signal": "BUY"}}, port, broker, dry_run=True
    )

    assert trades == [], "dry_run should produce no trades"


# --- T4: 보유 0주 매도 신호는 스킵 ---------------------------------------
def test_t4_sell_no_position_skipped():
    broker = MockBroker(initial_cash=10000.0, tradable=["TSLA"], quote=100.0)
    port = portfolio_mod.Portfolio(cash=0.0)
    trades = trader.process_orders({"TSLA": {"signal": "SELL"}}, port, broker)

    assert trades == [], "SELL with 0 shares should skip"


# --- T5: gpt5 엔진은 NotImplementedError (SC-09) -------------------------
def test_t5_gpt5_not_implemented():
    raised = False
    try:
        signal_provider.get_provider("gpt5")
    except NotImplementedError:
        raised = True
    assert raised, "get_provider('gpt5') should raise NotImplementedError"


# --- T6: finbert 엔진은 FinbertProvider ----------------------------------
def test_t6_finbert_provider():
    p = signal_provider.get_provider("finbert")
    assert p.name == "finbert"
    assert isinstance(p, signal_provider.SignalProvider)


# --- T7: sync_from_kis가 KIS 잔고로 Portfolio 재구성 ---------------------
def test_t7_sync_from_kis():
    broker = MockBroker(
        initial_cash=5000.0,
        positions={"AAPL": PositionSnapshot(shares=50, avg_price=100.0, current_price=110.0)},
    )
    port = portfolio_mod.Portfolio(cash=0.0)
    synced = portfolio_mod.sync_from_kis(port, broker)

    assert synced.cash == 5000.0
    assert "AAPL" in synced.positions
    assert synced.positions["AAPL"].shares == 50
    assert synced.positions["AAPL"].avg_price == 100.0


# --- T8: paper=False는 RuntimeError (FR-20) ------------------------------
def test_t8_paper_false_raises():
    raised = False
    try:
        kis_broker.KISBroker(
            app_key="k", app_secret="s", account_no="12345678-01", paper=False
        )
    except RuntimeError:
        raised = True
    assert raised, "paper=False should raise RuntimeError"


# --- T9: FR-14/SC-05 — 매매 불가 종목 신호 생성 제외 ---------------------
def test_t9_tradable_filter_excludes():
    import signals

    original = config.KIS_SYMBOLS_FILE
    fd, tmp_path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    try:
        # 매매 가능 = AAPL 만. NVDA/TSLA는 제외되어야 함
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(
                {"updated_at": "2099-01-01T00:00:00+00:00",
                 "refresh_days": 7, "tradable": ["AAPL"]},
                f,
            )
        config.KIS_SYMBOLS_FILE = tmp_path
        filtered = signals._filter_tradable_symbols(["AAPL", "NVDA", "TSLA"])
        assert filtered == ["AAPL"], f"expected ['AAPL'], got {filtered}"

        # 캐시 미존재 시 전체 통과 (폴백)
        config.KIS_SYMBOLS_FILE = tmp_path + ".missing"
        passthrough = signals._filter_tradable_symbols(["AAPL", "NVDA"])
        assert passthrough == ["AAPL", "NVDA"], "missing cache should pass through"
    finally:
        config.KIS_SYMBOLS_FILE = original
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# --- 단독 실행 러너 (pytest 미설치 환경) ----------------------------------
def _run_standalone() -> int:
    # Windows 콘솔(cp949)에서 유니코드 출력 깨짐 방지
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    passed = failed = 0
    print(f"\nKIS Paper Trading 단위 테스트 — {len(tests)}건\n" + "-" * 50)
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            failed += 1
    print("-" * 50)
    print(f"{passed} passed, {failed} failed (of {len(tests)})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_standalone())
