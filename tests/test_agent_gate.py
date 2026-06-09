"""agent_gate.evaluate_candidate 단위 테스트
(community-opinion-agent-live §3.1).

순수 helper — snapshot/universe/cost/memory/router → (DecisionResult, OrderIntent).
부수효과 없음, 사이징·side 매핑·decision_id 검증.

실행:
  pytest tests/test_agent_gate.py
  python tests/test_agent_gate.py
"""
from __future__ import annotations

import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
from agent_gate import evaluate_candidate, OrderIntent
from universe_filter import UniverseFilter
from cost_aware_trade_filter import CostAwareTradeFilter
from community_memory import CommunityMemoryStore, InMemoryBackend
from decision_router import DecisionRouter

_SP = {"AAPL", "NVDA"}
_NQ = {"NVDA"}
_EQUITY = 100_000.0
_PRICE = 100.0
_RUN_META = dict(date="2026-06-01", source="reddit", model="finbert-wsb",
                 ranking="sentiment", sizing="opinion_trend", universe_mode="community_liquid")


def _filters():
    return (UniverseFilter("community_liquid", sp500=_SP, nasdaq100=_NQ),
            CostAwareTradeFilter(),
            CommunityMemoryStore(backend=InMemoryBackend()),
            DecisionRouter(llm_router=False))


def _eval(symbol="NVDA", scored=None, position=None, **over):
    uf, cf, mem, router = _filters()
    scored = scored or {"bullish": 6, "bearish": 1, "neutral": 1, "score": 85,
                        "mentions": 8, "neutral_ratio": 0.12, "velocity_state": "NORMAL",
                        "signal": "BUY"}
    hist = [{"score": 85, "bullish": 6, "bearish": 1},
            {"score": 78, "bullish": 5, "bearish": 1},
            {"score": 72, "bullish": 4, "bearish": 1}]   # 상승추세 + persistence
    kw = dict(symbol=symbol, scored_entry=scored, history=hist, run_meta=_RUN_META,
              universe_filter=uf, cost_filter=cf, memory=mem, router=router,
              open_price=_PRICE, account_equity=_EQUITY,
              avg_dollar_volume=config.COMMUNITY_MIN_AVG_DOLLAR_VOLUME * 3,
              recent_volatility_pct=5.0, rsi=45.0, current_position=position)
    kw.update(over)
    dec, intent, _snap = evaluate_candidate(**kw)   # live-iterate: 3-tuple (snapshot 추가)
    return dec, intent


# --- T1: BUY → OrderIntent side BUY, 사이징 = equity×pct×size_factor/price ---
def test_t1_buy_intent():
    dec, intent = _eval()
    assert dec.action == "BUY"
    assert intent.side == "BUY" and intent.action == "BUY"
    expected = math.floor(_EQUITY * config.EQUAL_POSITION_PCT * dec.size_factor / _PRICE)
    assert intent.shares == expected and intent.shares > 0
    assert intent.decision_id == \
        "2026-06-01|NVDA|reddit|finbert-wsb|sentiment|opinion_trend|community_liquid"


# --- T2: SKIP (neutral 높음) → side "", shares 0 ---
def test_t2_skip_no_order():
    dec, intent = _eval(scored={"bullish": 5, "bearish": 1, "neutral": 10, "score": 72,
                                "mentions": 16, "neutral_ratio": 0.80,
                                "velocity_state": "NORMAL", "signal": "BUY"})
    assert dec.action == "SKIP"
    assert intent.side == "" and intent.shares == 0


# --- T3: universe blocked → SKIP, 주문 없음 ---
def test_t3_universe_blocked():
    # XYZ는 인덱스 외 + 저유동 → BLOCKED
    dec, intent = _eval(symbol="XYZ", avg_dollar_volume=1000.0)
    assert dec.action == "SKIP"
    assert intent.side == "" and intent.shares == 0
    assert "universe_blocked" in dec.reason_codes


# --- T4: 보유 중 합의 붕괴 → SELL/EXIT/REDUCE, side SELL + 보유수량 기반 ---
def test_t4_sell_uses_position():
    pos = {"symbol": "NVDA", "shares": 20}
    dec, intent = _eval(
        scored={"bullish": 1, "bearish": 5, "neutral": 1, "score": 40,
                "mentions": 7, "neutral_ratio": 0.14, "velocity_state": "DECLINING",
                "signal": "BUY"},
        position=pos)
    assert dec.action in ("SELL", "EXIT", "REDUCE")
    assert intent.side == "SELL"
    assert intent.shares == (20 if dec.action in ("SELL", "EXIT") else 10)


# --- T5: 순수성 — 파일/주문 부수효과 없음 (decision log 미생성) ---
def test_t5_no_side_effects(tmp_path=None):
    import tempfile
    # evaluate_candidate는 decision log를 쓰지 않는다(드라이버가 담당)
    with tempfile.TemporaryDirectory() as d:
        orig = config.COMMUNITY_DECISIONS_FILE
        try:
            config.COMMUNITY_DECISIONS_FILE = os.path.join(d, "x.jsonl")
            _eval()
            assert not os.path.exists(config.COMMUNITY_DECISIONS_FILE)
        finally:
            config.COMMUNITY_DECISIONS_FILE = orig


# --- T6: open_price<=0 → shares 0 (안전) ---
def test_t6_zero_price():
    dec, intent = _eval(open_price=0.0)
    assert intent.shares == 0


# --- T7: OrderIntent 스키마 ---
def test_t7_schema():
    _, intent = _eval()
    for fld in ("symbol", "action", "side", "shares", "size_factor",
                "decision_id", "reason", "snapshot_summary"):
        assert hasattr(intent, fld), fld


# --- T8: snapshot 반환 (live-iterate Gap-1/2 — 영속·로그 보강용) ---
def test_t8_returns_snapshot():
    uf, cf, mem, router = _filters()
    scored = {"bullish": 6, "bearish": 1, "neutral": 1, "score": 85, "mentions": 8,
              "neutral_ratio": 0.12, "velocity_state": "NORMAL", "signal": "BUY"}
    dec, intent, snap = evaluate_candidate(
        symbol="NVDA", scored_entry=scored, history=[], run_meta=_RUN_META,
        universe_filter=uf, cost_filter=cf, memory=mem, router=router,
        open_price=_PRICE, account_equity=_EQUITY,
        avg_dollar_volume=config.COMMUNITY_MIN_AVG_DOLLAR_VOLUME * 3, recent_volatility_pct=5.0)
    assert snap is not None
    assert getattr(snap, "symbol", None) == "NVDA"
    assert hasattr(snap, "opinion_score")


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\nagent_gate.evaluate_candidate 단위 테스트 - {len(tests)}건\n" + "-" * 50)
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
