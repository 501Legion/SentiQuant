"""live-scheduler-deploy 안전장치 단위 테스트 (M4 / Plan SC-03~06,08).

runtime_guard(키스위치·일일/노출 한도·heartbeat·selfcheck) + notifier(no-op·마스킹).
파일 I/O는 tmp/임시 상수로 격리. (pytest 미설치 — 단독 러너)

실행:
  pytest tests/test_runtime_guard.py
  python tests/test_runtime_guard.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
import runtime_guard as rg
import notifier

_I = lambda s, sh: types.SimpleNamespace(symbol=s, shares=sh)


# --- TC-01: 키스위치 파일/env ---
def test_tc01_killswitch_file_and_env():
    with tempfile.TemporaryDirectory() as d:
        orig = config.TRADING_HALT_FILE
        config.TRADING_HALT_FILE = os.path.join(d, "TRADING_HALT")
        try:
            assert rg.is_halted() is False
            open(config.TRADING_HALT_FILE, "w").close()
            assert rg.is_halted() is True
            os.remove(config.TRADING_HALT_FILE)
            assert rg.is_halted() is False
            os.environ["TRADING_HALT"] = "1"
            assert rg.is_halted() is True
        finally:
            os.environ.pop("TRADING_HALT", None)
            config.TRADING_HALT_FILE = orig


# --- TC-02: 일일 매수 건수 한도 ---
def test_tc02_daily_buy_limit():
    buys = [(_I("AAA", 1), 100.0), (_I("BBB", 1), 100.0)]
    # today_buy_count가 이미 상한 → 전량 차단
    allowed, blocked = rg.filter_by_limits(
        buys, equity=1_000_000, positions_value=0, position_value_by_symbol={},
        today_buy_count=config.MAX_DAILY_BUYS)
    assert allowed == [] and len(blocked) == 2
    assert "일일 매수 한도" in blocked[0]
    # 여유 있으면 통과
    allowed, _ = rg.filter_by_limits(
        buys, equity=1_000_000, positions_value=0, position_value_by_symbol={},
        today_buy_count=0)
    assert len(allowed) == 2

def test_tc02b_count_today_buy_activity_from_trades_and_summaries():
    import csv
    import json
    with tempfile.TemporaryDirectory() as d:
        trades = os.path.join(d, "trades.csv")
        summaries = os.path.join(d, "run_summaries.jsonl")
        with open(trades, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "date", "symbol", "action", "signal", "price", "shares",
                "amount", "net_profit_pct", "net_profit_usd", "order_no", "kis_status",
            ])
            writer.writeheader()
            writer.writerow({
                "date": "2026-06-01T15:00:00+00:00", "symbol": "AAA",
                "action": "BUY", "order_no": "00001", "kis_status": "FILLED",
            })
            writer.writerow({
                "date": "2026-06-01T16:00:00+00:00", "symbol": "BBB",
                "action": "SELL", "order_no": "00002", "kis_status": "FILLED",
            })
        with open(summaries, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "date": "2026-06-01", "dry_run": False,
                "buy_order_count": 1, "buy_order_nos": ["00003"],
            }) + "\n")
            f.write(json.dumps({
                "date": "2026-06-01", "dry_run": True,
                "buy_order_count": 5, "buy_order_nos": ["00004"],
            }) + "\n")
        assert rg.count_today_buy_activity(
            "2026-06-01", trades_file=trades,
            run_summaries_file=summaries, tz_name="UTC",
        ) == 2


# --- TC-03: 종목당 비중 한도 ---
def test_tc03_symbol_weight_cap():
    # equity 10000, 종목당 20%=2000. 3000짜리 매수 → 차단
    big = [(_I("AAA", 30), 100.0)]   # 3000
    allowed, blocked = rg.filter_by_limits(
        big, equity=10_000, positions_value=0, position_value_by_symbol={},
        today_buy_count=0)
    assert allowed == [] and "비중" in blocked[0]


# --- TC-04: 총 노출 한도 ---
def test_tc04_total_exposure_cap():
    # equity 10000, 총 60%=6000. 이미 5000 보유 + 2000 매수 → 7000 > 6000 차단
    buys = [(_I("NEW", 20), 100.0)]  # 2000
    allowed, blocked = rg.filter_by_limits(
        buys, equity=10_000, positions_value=5_000,
        position_value_by_symbol={"OLD": 5_000}, today_buy_count=0)
    assert allowed == [] and "총 노출" in blocked[0]


# --- TC-05: heartbeat 기록/신선도 ---
def test_tc05_heartbeat():
    import datetime as dt
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "hb.json")
        rg.write_heartbeat("order", path=p)
        hb = rg.read_heartbeat(p)
        assert "order" in hb
        assert rg.heartbeat_stale("order", hb=hb, minutes=90) is False
        old = {"order": "2020-01-01T00:00:00+00:00"}
        assert rg.heartbeat_stale("order", hb=old, minutes=90) is True
        assert rg.heartbeat_stale("signal", hb=hb) is True  # 기록 없음 → stale


# --- TC-06: selfcheck (자격 누락 감지) ---
def test_tc06_selfcheck_detects_missing():
    orig = config.KIS_APP_KEY
    try:
        config.KIS_APP_KEY = ""
        fails = rg.selfcheck()
        assert any("KIS_APP_KEY" in f for f in fails)
    finally:
        config.KIS_APP_KEY = orig
    # 복원 후엔 KIS_APP_KEY 누락 항목 없음
    assert not any("자격증명 누락: KIS_APP_KEY" == f for f in rg.selfcheck())


# --- TC-07: notifier no-op + 마스킹 ---
def test_tc07_notifier_noop_and_mask():
    orig = config.SLACK_WEBHOOK_URL
    try:
        config.SLACK_WEBHOOK_URL = ""
        assert notifier.notify("error", "KIS_APP_KEY=SECRET123", {"token": "abc"}) is False
    finally:
        config.SLACK_WEBHOOK_URL = orig
    masked = notifier._mask("KIS_APP_KEY=SECRET123 token: abc123")
    assert "SECRET123" not in masked and "abc123" not in masked


# --- TC-08: 워치독 stale 판정 (SC-09 핵심 로직) ---
def test_tc08_watchdog_stale_logic():
    import datetime as dt
    now = dt.datetime(2026, 6, 9, 12, 0, tzinfo=dt.timezone.utc)
    fresh = {"order": (now - dt.timedelta(minutes=10)).isoformat()}
    stale = {"order": (now - dt.timedelta(minutes=200)).isoformat()}
    assert rg.heartbeat_stale("order", now=now, minutes=90, hb=fresh) is False
    assert rg.heartbeat_stale("order", now=now, minutes=90, hb=stale) is True


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\nruntime_guard 단위 테스트 - {len(tests)}건\n" + "-" * 50)
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
