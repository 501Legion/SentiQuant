"""community_live.run_live + scheduler LIVE_STRATEGY 분기 단위 테스트
(community-opinion-agent-live §6 Test Plan).

신호 엔진/프로바이더(FinBERT)는 스텁 — 라이브 *배선*만 검증한다:
  - dry-run 기본 → place_order 호출 0 (SC-02)
  - --no-dry-run → place_order(paper) 호출 (SC-03)
  - decision log(live) 영속 — BUY/SKIP 모두 (SC-04)
  - LLM 일일 상한 초과 → rule fallback (SC-05)
  - LIVE_STRATEGY="news" → community_live 미호출 (SC-06, 회귀 0)
  - 게시글 없음 → 안전 빈 결과

실행:
  pytest tests/test_community_live.py
  python tests/test_community_live.py
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pandas as pd

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config
import community_live
import wsb_state
from community_memory import CommunityMemoryStore, InMemoryBackend
from reddit_portfolio import RedditPortfolio
from mock_broker import MockBroker

_DATE = "2026-06-01"


# --- 픽스처 -----------------------------------------------------------------
def _make_df(symbol: str) -> pd.DataFrame:
    """30 거래일 OHLCV, 마지막 행 = _DATE, 고유동성(통과)."""
    dates = list(pd.bdate_range(end=_DATE, periods=30).strftime("%Y-%m-%d"))
    n = len(dates)
    return pd.DataFrame({
        "date": dates,
        "open": [100.0] * n, "high": [103.0] * n,
        "low": [98.0] * n, "close": [100.0] * n,
        "volume": [10_000_000] * n,    # 100 × 1e7 = $1B/day >> MIN
    })


_BUY_SCORED = {"symbol": "NVDA", "bullish": 6, "bearish": 1, "neutral": 1,
               "score": 85, "mentions": 8, "neutral_ratio": 0.12,
               "velocity_state": "NORMAL", "signal": "BUY"}
_SKIP_SCORED = {"symbol": "AAPL", "bullish": 5, "bearish": 1, "neutral": 10,
                "score": 72, "mentions": 16, "neutral_ratio": 0.80,
                "velocity_state": "NORMAL", "signal": "BUY"}
_HIST = {"NVDA": [{"score": 85, "bullish": 6, "bearish": 1},
                  {"score": 78, "bullish": 5, "bearish": 1},
                  {"score": 72, "bullish": 4, "bearish": 1}]}


class _FakeEngine:
    """WSBSignalEngine 스텁 — 모델 없이 (top_n, signal_details) 고정 반환."""

    def __init__(self, provider, ranking="mentions"):
        pass

    def run_pipeline(self, posts_by_symbol, ohlcv_cache, date_str=None):
        # 신호 엔진이 후보를 surface → 에이전트(agent_gate)가 BUY/SKIP 게이팅
        details = [d for d in (_BUY_SCORED, _SKIP_SCORED) if d["symbol"] in posts_by_symbol]
        top_n = [d["symbol"] for d in details]
        return top_n, details

    def check_exit(self, **kwargs):
        return False, ""


@contextmanager
def _live_env(*, decisions_path, llm_router_cls=None, strategy="agent"):
    """파일/모델 부수효과 차단 — 영속 함수 stub + 신호 엔진 stub + tmp 로그 경로."""
    saved = {}

    def _save(obj, name):
        saved[(obj, name)] = getattr(obj, name)

    # 영속 상태 함수 stub (실제 data/ 미오염)
    _save(wsb_state, "load_score_history"); wsb_state.load_score_history = lambda: dict(_HIST)
    _save(wsb_state, "save_score_history"); wsb_state.save_score_history = lambda h: None
    _save(wsb_state, "load_position_scores"); wsb_state.load_position_scores = lambda: {}
    _save(wsb_state, "save_position_scores"); wsb_state.save_position_scores = lambda s: None
    _save(wsb_state, "append_daily_snapshot"); wsb_state.append_daily_snapshot = lambda *a, **k: None
    _save(wsb_state, "load_daily_snapshots"); wsb_state.load_daily_snapshots = lambda *a, **k: []
    # 신호 엔진/프로바이더 stub (FinBERT 미로드)
    _save(community_live, "get_provider"); community_live.get_provider = lambda name: object()
    _save(community_live, "WSBSignalEngine"); community_live.WSBSignalEngine = _FakeEngine
    # decision log → tmp
    _save(config, "COMMUNITY_LIVE_DECISIONS_FILE"); config.COMMUNITY_LIVE_DECISIONS_FILE = decisions_path
    _save(config, "LIVE_STRATEGY"); config.LIVE_STRATEGY = strategy
    if llm_router_cls is not None:
        _save(community_live, "DecisionRouter"); community_live.DecisionRouter = llm_router_cls
    try:
        yield
    finally:
        for (obj, name), val in saved.items():
            setattr(obj, name, val)


def _portfolio():
    p = RedditPortfolio("test_agent_live")
    p.save_state = lambda *a, **k: None          # 파일 미생성
    return p


def _run(dry_run, broker=None, posts=None, llm_router=None, mem=None):
    posts = posts if posts is not None else {"NVDA": [{"title": "NVDA to moon", "body_excerpt": "calls"}],
                                             "AAPL": [{"title": "AAPL", "body_excerpt": ""}]}
    return community_live.run_live(
        date=_DATE, dry_run=dry_run, llm_router=llm_router,
        universe_mode="community_liquid", broker=broker,
        posts_by_symbol=posts, ohlcv_full={"NVDA": _make_df("NVDA"), "AAPL": _make_df("AAPL")},
        portfolio=_portfolio(), memory=(mem or CommunityMemoryStore(backend=InMemoryBackend())),
    )


# --- T1: dry-run → place_order 호출 0 (SC-02) -------------------------------
def test_t1_dry_run_no_order(tmp_path=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        broker = MockBroker(tradable=["NVDA", "AAPL"], quote=100.0)
        with _live_env(decisions_path=os.path.join(d, "dec.jsonl")):
            res = _run(dry_run=True, broker=broker)
        assert broker._order_seq == 0, "dry-run인데 place_order 호출됨"
        assert all(not o.get("executed") for o in res["orders"])
        assert res["summary"]["placed"] == 0


# --- T2: --no-dry-run → place_order(paper) 호출 (SC-03) ---------------------
def test_t2_live_places_order(tmp_path=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        broker = MockBroker(tradable=["NVDA", "AAPL"], quote=100.0)
        with _live_env(decisions_path=os.path.join(d, "dec.jsonl")):
            res = _run(dry_run=False, broker=broker)
        assert broker._order_seq >= 1, "실모의주문 모드인데 place_order 미호출"
        buys = [o for o in res["orders"] if o.get("side") == "BUY"]
        assert buys and buys[0]["executed"] and buys[0]["status"] == "FILLED"


# --- T3: decision log(live) 영속 — BUY/SKIP 모두 (SC-04) --------------------
def test_t3_decision_log_persist(tmp_path=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "dec.jsonl")
        broker = MockBroker(tradable=["NVDA", "AAPL"])
        with _live_env(decisions_path=path):
            res = _run(dry_run=True, broker=broker)
        assert os.path.exists(path)
        recs = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
        actions = {r["symbol"]: r["final_action"] for r in recs}
        assert "NVDA" in actions and "AAPL" in actions     # BUY + SKIP 모두 기록
        assert res["decision_log_path"] == path


# --- T4: LLM 일일 상한 초과 → rule fallback (SC-05) -------------------------
class _FakeLLMRouter:
    """llm_router ON이면 llm_assisted, 강등 후엔 rule_based 반환."""

    def __init__(self, llm_router=None):
        self.llm_router = True if llm_router else bool(config.COMMUNITY_LLM_ROUTER_ENABLED)

    def decide(self, **kwargs):
        mode = "llm_assisted" if self.llm_router else "rule_based"
        return SimpleNamespace(action="SKIP", size_factor=1.0, router_mode=mode,
                               reasoning="stub", reason_codes=[], warnings=[],
                               confidence=0.5, rule_action="SKIP", llm_action="")


def test_t4_llm_daily_cap(tmp_path=None):
    import tempfile
    saved_cap = config.COMMUNITY_LLM_LIVE_MAX_CALLS
    with tempfile.TemporaryDirectory() as d:
        try:
            config.COMMUNITY_LLM_LIVE_MAX_CALLS = 1     # 1회 초과 시 강등
            with _live_env(decisions_path=os.path.join(d, "dec.jsonl"),
                           llm_router_cls=_FakeLLMRouter):
                res = _run(dry_run=True, broker=MockBroker(tradable=["NVDA", "AAPL"]),
                           llm_router=True)
            # 2 후보(NVDA, AAPL) — 1번째 llm, 상한 도달→강등, 2번째 rule → llm_calls==1
            assert res["summary"]["llm_calls"] == 1, res["summary"]
        finally:
            config.COMMUNITY_LLM_LIVE_MAX_CALLS = saved_cap


# --- T5: LIVE_STRATEGY 스위치 — agent만 run_live 호출 (SC-06 회귀) ----------
def test_t5_scheduler_switch():
    try:
        import scheduler
    except ModuleNotFoundError as e:
        # 스케줄러 선택적 의존성(pandas_market_calendars 등) 미설치 환경 → 스킵
        print(f"  SKIP  test_t5_scheduler_switch (의존성 없음: {e.name})")
        return
    saved = {"trading": scheduler.is_trading_day, "run_live": community_live.run_live,
             "load_signals": getattr(scheduler, "load_signals", None),
             "strategy": config.LIVE_STRATEGY}
    calls = {"n": 0}
    try:
        scheduler.is_trading_day = lambda: True
        community_live.run_live = lambda *a, **k: calls.__setitem__("n", calls["n"] + 1)
        scheduler.load_signals = lambda: {}        # 뉴스 경로 조기 종료

        config.LIVE_STRATEGY = "news"
        scheduler.order_processing_job(dry_run=True)
        assert calls["n"] == 0, "news 전략인데 community_live 호출됨 (회귀)"

        config.LIVE_STRATEGY = "agent"
        scheduler.order_processing_job(dry_run=True)
        assert calls["n"] == 1, "agent 전략인데 community_live 미호출"
    finally:
        scheduler.is_trading_day = saved["trading"]
        community_live.run_live = saved["run_live"]
        if saved["load_signals"] is not None:
            scheduler.load_signals = saved["load_signals"]
        config.LIVE_STRATEGY = saved["strategy"]


# --- T7: 청산 시 high-level reflection 생성 (FR-09) -------------------------
def test_t7_high_level_reflection_on_close(tmp_path=None):
    import tempfile
    from reddit_portfolio import Position

    sell_scored = {"symbol": "NVDA", "bullish": 1, "bearish": 5, "neutral": 1,
                   "score": 40, "mentions": 7, "neutral_ratio": 0.14,
                   "velocity_state": "DECLINING", "signal": "BUY"}

    class _EngSell(_FakeEngine):
        def run_pipeline(self, posts_by_symbol, ohlcv_cache, date_str=None):
            return ["NVDA"], [sell_scored]

    with tempfile.TemporaryDirectory() as d:
        p = _portfolio()
        p.cash = 50_000.0
        p.positions["NVDA"] = Position(symbol="NVDA", entry_date="2026-05-01",
                                       entry_price=120.0, shares=20, highest_price=130.0)
        mem = CommunityMemoryStore(backend=InMemoryBackend())
        with _live_env(decisions_path=os.path.join(d, "dec.jsonl")):
            community_live.WSBSignalEngine = _EngSell      # 청산 신호 엔진으로 교체
            res = community_live.run_live(
                date=_DATE, dry_run=True, universe_mode="community_liquid",
                broker=MockBroker(tradable=["NVDA"]),
                posts_by_symbol={"NVDA": [{"title": "NVDA dump", "body_excerpt": "puts"}]},
                ohlcv_full={"NVDA": _make_df("NVDA")}, portfolio=p, memory=mem)
        assert res["summary"]["sells"] >= 1, res["summary"]
        assert res["summary"]["reflections"]["high"] >= 1, res["summary"]


# --- T8: forward 확정분 → low-level reflection 생성 (FR-09) -----------------
def test_t8_low_level_forward_reflection():
    mem = CommunityMemoryStore(backend=InMemoryBackend())
    df = _make_df("NVDA")
    cohort = sorted(df["date"].tolist())[-15]      # today(_DATE) 기준 14거래일 전
    snap = {"date": cohort, "symbol": "NVDA", "opinion_score": 80,
            "consensus_ratio": 0.8, "neutral_ratio": 0.1, "velocity_state": "NORMAL",
            "opinion_trend": "RISING", "persistence_days": 3, "universe_tier": "CORE",
            "decision_id": "d1"}
    saved = wsb_state.load_daily_snapshots
    try:
        wsb_state.load_daily_snapshots = lambda *a, **k: [snap]
        counts = community_live._build_reflections(mem, {"NVDA": df}, _DATE, [], {})
        assert counts["low"] >= 1, counts
    finally:
        wsb_state.load_daily_snapshots = saved


# --- T9: 라이브 실시간 가격(get_quote)로 체결가 보정 (보강①) ---------------
def test_t9_live_quote_pricing(tmp_path=None):
    import tempfile
    # 당일 일봉 미마감 상황 재현: OHLCV 마지막 봉을 _DATE 이전으로 (stale)
    df = _make_df("NVDA").iloc[:-1].reset_index(drop=True)
    broker = MockBroker(tradable=["NVDA"], quote=150.0)
    with tempfile.TemporaryDirectory() as d:
        with _live_env(decisions_path=os.path.join(d, "dec.jsonl")):
            res = community_live.run_live(
                date=_DATE, dry_run=False, universe_mode="community_liquid", broker=broker,
                posts_by_symbol={"NVDA": [{"title": "NVDA moon", "body_excerpt": "calls"}]},
                ohlcv_full={"NVDA": df}, portfolio=_portfolio(),
                memory=CommunityMemoryStore(backend=InMemoryBackend()))
        buys = [o for o in res["orders"] if o.get("side") == "BUY"]
        assert buys and buys[0]["executed"], res["orders"]   # quote(150) 사이징 → 체결
        assert broker._order_seq >= 1


# --- T10: 오늘 수집분 없으면 최근 수집일 여론 사용 (보강②) ------------------
def test_t10_posts_date_fallback(tmp_path=None):
    import tempfile
    saved_load = community_live.RedditCollector.load_posts
    saved_disc = community_live.RedditCollector.discover_dates
    try:
        def fake_load(date_str):
            return {} if date_str == _DATE else {"NVDA": [{"title": "x", "body_excerpt": ""}]}
        community_live.RedditCollector.load_posts = staticmethod(fake_load)
        community_live.RedditCollector.discover_dates = staticmethod(lambda f, t: ["2026-05-30"])
        with tempfile.TemporaryDirectory() as d:
            with _live_env(decisions_path=os.path.join(d, "dec.jsonl")):
                res = community_live.run_live(
                    date=_DATE, dry_run=True, universe_mode="community_liquid",
                    broker=MockBroker(tradable=["NVDA"]),
                    posts_by_symbol=None,                      # fallback 경로 트리거
                    ohlcv_full={"NVDA": _make_df("NVDA")}, portfolio=_portfolio(),
                    memory=CommunityMemoryStore(backend=InMemoryBackend()))
        assert res["summary"]["candidates"] >= 1, res["summary"]   # 05-30 여론으로 평가
    finally:
        community_live.RedditCollector.load_posts = saved_load
        community_live.RedditCollector.discover_dates = saved_disc


# --- T6: 게시글 없음 → 안전 빈 결과 ----------------------------------------
def test_t6_no_posts(tmp_path=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with _live_env(decisions_path=os.path.join(d, "dec.jsonl")):
            res = _run(dry_run=True, posts={})
        assert res["summary"]["candidates"] == 0
        assert res["orders"] == []


def _run_standalone() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    print(f"\ncommunity_live 단위 테스트 - {len(tests)}건\n" + "-" * 50)
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"  FAIL  {name}: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print("-" * 50)
    print(f"{passed} passed, {failed} failed (of {len(tests)})")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run_standalone())
