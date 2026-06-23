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
from kis_broker import PositionSnapshot
from reddit_portfolio import Position, RedditPortfolio
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
    _save(community_live, "record_trade"); community_live.record_trade = lambda *a, **k: None
    # decision log → tmp
    _save(config, "COMMUNITY_LIVE_DECISIONS_FILE"); config.COMMUNITY_LIVE_DECISIONS_FILE = decisions_path
    _save(config, "COMMUNITY_LIVE_RUN_SUMMARIES_FILE")
    config.COMMUNITY_LIVE_RUN_SUMMARIES_FILE = os.path.join(os.path.dirname(decisions_path), "run_summaries.jsonl")
    # decision report → tmp (daily-decision-report: 실 data/community/live/reports 미오염)
    _save(config, "COMMUNITY_LIVE_REPORTS_DIR")
    config.COMMUNITY_LIVE_REPORTS_DIR = os.path.join(os.path.dirname(decisions_path), "reports")
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
        # SC-01 (daily-decision-report): run_live 종료 시 보고서 자동 생성
        assert res["report_path"] and os.path.exists(res["report_path"])
        assert res["report_path"].endswith(f"{_DATE}.md")


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


def test_t2b_live_initializes_from_broker_and_preserves_metadata(tmp_path=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        saved_dir = config.REDDIT_DATA_DIR
        try:
            config.REDDIT_DATA_DIR = d
            prior = RedditPortfolio(config.COMMUNITY_LIVE_STRATEGY_KEY)
            prior.cash = 1.0
            prior.positions["NVDA"] = Position(
                symbol="NVDA", entry_date="2026-05-01", entry_price=90.0,
                shares=3, highest_price=130.0, size_factor=0.7,
                entry_decision_id="decision-1", stop_loss_pct=-4.0,
                trailing_stop_pct=-3.0)
            prior.save_state("2026-05-31")
            broker = MockBroker(
                initial_cash=4321.0,
                positions={
                    "NVDA": PositionSnapshot(
                        shares=7, avg_price=110.0, current_price=125.0),
                    "AAPL": PositionSnapshot(
                        shares=2, avg_price=200.0, current_price=210.0),
                },
            )

            restored = community_live._portfolio_from_broker_account(
                broker, config.COMMUNITY_LIVE_STRATEGY_KEY, _DATE)

            assert restored.cash == 4321.0
            assert set(restored.positions) == {"NVDA", "AAPL"}
            nvda = restored.positions["NVDA"]
            assert (nvda.shares, nvda.entry_price) == (7, 110.0)
            assert nvda.entry_date == "2026-05-01"
            assert nvda.highest_price == 130.0
            assert nvda.size_factor == 0.7
            assert nvda.entry_decision_id == "decision-1"
            assert nvda.stop_loss_pct == -4.0
            assert restored.positions["AAPL"].entry_date == _DATE
        finally:
            config.REDDIT_DATA_DIR = saved_dir


def test_t2c_rejected_buy_does_not_mutate_mirror(tmp_path=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = _portfolio()
        before_cash = p.cash
        broker = MockBroker(tradable=[], quote=100.0)
        with _live_env(decisions_path=os.path.join(d, "dec.jsonl")):
            res = community_live.run_live(
                date=_DATE, dry_run=False, universe_mode="community_liquid",
                broker=broker,
                posts_by_symbol={"NVDA": [{"title": "NVDA moon", "body_excerpt": "calls"}]},
                ohlcv_full={"NVDA": _make_df("NVDA")}, portfolio=p,
                memory=CommunityMemoryStore(backend=InMemoryBackend()))
        buys = [o for o in res["orders"] if o.get("side") == "BUY"]
        assert buys and not buys[0]["executed"]
        assert p.cash == before_cash
        assert "NVDA" not in p.positions


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


def test_t3b_run_summary_persists_even_without_candidates(tmp_path=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "dec.jsonl")
        with _live_env(decisions_path=path):
            res = _run(dry_run=True, broker=MockBroker(tradable=["NVDA", "AAPL"]), posts={})
        summary_path = os.path.join(d, "run_summaries.jsonl")
        assert os.path.exists(summary_path)
        recs = [json.loads(line) for line in open(summary_path, encoding="utf-8") if line.strip()]
        assert recs[-1]["date"] == _DATE
        assert recs[-1]["candidates"] == 0
        assert recs[-1]["snapshot_status"] == "missing"
        assert recs[-1]["no_snapshot_reason"] == "no_posts"
        assert recs[-1]["input_symbols"] == 0
        assert res["summary"]["candidates"] == 0


def test_t3c_state_overrides_isolate_replay_outputs(tmp_path=None):
    import tempfile

    class _EngWritesMention(_FakeEngine):
        def run_pipeline(self, posts_by_symbol, ohlcv_cache, date_str=None):
            os.makedirs(os.path.dirname(config.MENTION_HISTORY_FILE), exist_ok=True)
            with open(config.MENTION_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump({"override_seen": [1]}, f)
            return super().run_pipeline(posts_by_symbol, ohlcv_cache, date_str)

    with tempfile.TemporaryDirectory() as d:
        live_dir = os.path.join(d, "live")
        replay_dir = os.path.join(d, "replay")
        path = os.path.join(live_dir, "dec.jsonl")
        original_mention_path = config.MENTION_HISTORY_FILE
        overrides = {
            "mention_history": os.path.join(replay_dir, "mention_history.json"),
            "score_history": os.path.join(replay_dir, "score_history.json"),
            "position_scores": os.path.join(replay_dir, "position_scores.json"),
            "daily_snapshots": os.path.join(replay_dir, "daily_snapshots.jsonl"),
            "decisions": os.path.join(replay_dir, "decisions.jsonl"),
            "run_summaries": os.path.join(replay_dir, "run_summaries.jsonl"),
            "reports_dir": os.path.join(replay_dir, "reports"),
            "memory_dir": os.path.join(replay_dir, "memory"),
            "reddit_data_dir": os.path.join(replay_dir, "reddit"),
        }
        with _live_env(decisions_path=path):
            community_live.WSBSignalEngine = _EngWritesMention
            res = community_live.run_live(
                date=_DATE, dry_run=True, universe_mode="community_liquid",
                broker=MockBroker(tradable=["NVDA", "AAPL"]),
                posts_by_symbol={"NVDA": [{"title": "NVDA moon", "body_excerpt": "calls"}]},
                ohlcv_full={"NVDA": _make_df("NVDA")}, portfolio=_portfolio(),
                memory=CommunityMemoryStore(backend=InMemoryBackend()),
                state_overrides=overrides,
            )

        assert os.path.exists(overrides["mention_history"])
        assert os.path.exists(overrides["decisions"])
        assert os.path.exists(overrides["run_summaries"])
        assert res["decision_log_path"] == overrides["decisions"]
        assert res["report_path"].startswith(overrides["reports_dir"])
        assert not os.path.exists(path), "replay override 중 live decision path가 오염됨"
        assert config.MENTION_HISTORY_FILE == original_mention_path


def test_t3d_run_summary_explains_filtered_out_snapshots(tmp_path=None):
    import tempfile

    class _EngNoRanked(_FakeEngine):
        def run_pipeline(self, posts_by_symbol, ohlcv_cache, date_str=None):
            return [], [_BUY_SCORED]

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "dec.jsonl")
        with _live_env(decisions_path=path):
            community_live.WSBSignalEngine = _EngNoRanked
            res = community_live.run_live(
                date=_DATE, dry_run=True, universe_mode="community_liquid",
                broker=MockBroker(tradable=["NVDA"]),
                posts_by_symbol={"NVDA": [{"title": "NVDA moon", "body_excerpt": "calls"}]},
                ohlcv_full={"NVDA": _make_df("NVDA")}, portfolio=_portfolio(),
                memory=CommunityMemoryStore(backend=InMemoryBackend()),
            )

        assert res["summary"]["snapshot_status"] == "missing"
        assert res["summary"]["no_snapshot_reason"] == "filtered_out_all"
        assert res["summary"]["input_symbols"] == 1
        assert res["summary"]["scored_symbols"] == 1
        assert res["summary"]["ranked_symbols"] == 0
        assert res["summary"]["snapshot_count"] == 0


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
    import runtime_guard
    saved = {"trading": scheduler.is_trading_day, "run_live": community_live.run_live,
             "load_signals": getattr(scheduler, "load_signals", None),
             "strategy": config.LIVE_STRATEGY,
             "heartbeat": runtime_guard.write_heartbeat,
             "halted": runtime_guard.is_halted}
    calls = {"n": 0}
    try:
        # 실 운영 data/heartbeat.json 오염 방지 — 워치독이 stale 감지를 못 하게 됨
        runtime_guard.write_heartbeat = lambda *a, **k: None
        runtime_guard.is_halted = lambda: False
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
        runtime_guard.write_heartbeat = saved["heartbeat"]
        runtime_guard.is_halted = saved["halted"]


# --- T7: 청산 시 high-level reflection 생성 (FR-09) -------------------------
def test_t7_high_level_reflection_on_close(tmp_path=None):
    import tempfile

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
        broker = MockBroker(
            tradable=["NVDA"],
            positions={"NVDA": PositionSnapshot(
                shares=20, avg_price=120.0, current_price=100.0)},
        )
        with _live_env(decisions_path=os.path.join(d, "dec.jsonl")):
            community_live.WSBSignalEngine = _EngSell      # 청산 신호 엔진으로 교체
            res = community_live.run_live(
                date=_DATE, dry_run=False, universe_mode="community_liquid",
                broker=broker,
                posts_by_symbol={"NVDA": [{"title": "NVDA dump", "body_excerpt": "puts"}]},
                ohlcv_full={"NVDA": _make_df("NVDA")}, portfolio=p, memory=mem)
        assert res["summary"]["sells"] >= 1, res["summary"]
        assert res["summary"]["reflections"]["high"] >= 1, res["summary"]
        assert "NVDA" not in p.positions


def test_t7b_rejected_sell_does_not_mutate_mirror(tmp_path=None):
    import tempfile

    sell_scored = {"symbol": "NVDA", "bullish": 1, "bearish": 5, "neutral": 1,
                   "score": 40, "mentions": 7, "neutral_ratio": 0.14,
                   "velocity_state": "DECLINING", "signal": "BUY"}

    class _EngSell(_FakeEngine):
        def run_pipeline(self, posts_by_symbol, ohlcv_cache, date_str=None):
            return ["NVDA"], [sell_scored]

    with tempfile.TemporaryDirectory() as d:
        p = _portfolio()
        p.cash = 50_000.0
        p.positions["NVDA"] = Position(
            symbol="NVDA", entry_date="2026-05-01",
            entry_price=120.0, shares=20, highest_price=130.0)
        before_cash = p.cash
        with _live_env(decisions_path=os.path.join(d, "dec.jsonl")):
            community_live.WSBSignalEngine = _EngSell
            res = community_live.run_live(
                date=_DATE, dry_run=False, universe_mode="community_liquid",
                broker=MockBroker(tradable=[]),
                posts_by_symbol={"NVDA": [{"title": "NVDA dump", "body_excerpt": "puts"}]},
                ohlcv_full={"NVDA": _make_df("NVDA")}, portfolio=p,
                memory=CommunityMemoryStore(backend=InMemoryBackend()))
        sells = [o for o in res["orders"] if o.get("side") == "SELL"]
        assert sells and not sells[0]["executed"]
        assert p.cash == before_cash
        assert p.positions["NVDA"].shares == 20


def test_t7c_partial_sell_keeps_remaining_position():
    p = RedditPortfolio("partial")
    p.cash = 1_000.0
    p.positions["NVDA"] = Position(
        symbol="NVDA", entry_date="2026-05-01",
        entry_price=100.0, shares=10, highest_price=120.0)
    trade = p._sell("NVDA", 110.0, _DATE, reason="reduce", shares=4)
    assert trade and trade["shares"] == 4
    assert p.positions["NVDA"].shares == 6


def test_t7d_buy_hold_sell_across_consecutive_runs():
    import tempfile

    hold_scored = dict(_BUY_SCORED)
    sell_scored = {
        "symbol": "NVDA", "bullish": 1, "bearish": 5, "neutral": 1,
        "score": 40, "mentions": 7, "neutral_ratio": 0.14,
        "velocity_state": "DECLINING", "signal": "BUY",
    }

    class _EngHold(_FakeEngine):
        def run_pipeline(self, posts_by_symbol, ohlcv_cache, date_str=None):
            return ["NVDA"], [hold_scored]

    class _EngSell(_FakeEngine):
        def run_pipeline(self, posts_by_symbol, ohlcv_cache, date_str=None):
            return ["NVDA"], [sell_scored]

    with tempfile.TemporaryDirectory() as d:
        broker = MockBroker(initial_cash=100_000.0, tradable=["NVDA"], quote=100.0)
        portfolio = _portfolio()
        memory = CommunityMemoryStore(backend=InMemoryBackend())
        posts = {"NVDA": [{"title": "NVDA", "body_excerpt": "calls"}]}
        ohlcv = {"NVDA": _make_df("NVDA")}

        with _live_env(decisions_path=os.path.join(d, "day1.jsonl")):
            day1 = community_live.run_live(
                date="2026-06-01", dry_run=False, broker=broker,
                universe_mode="community_liquid", posts_by_symbol=posts,
                ohlcv_full=ohlcv, portfolio=portfolio, memory=memory,
            )
        assert day1["summary"]["buys"] == 1
        bought_shares = portfolio.positions["NVDA"].shares
        assert bought_shares > 0

        with _live_env(decisions_path=os.path.join(d, "day2.jsonl")):
            community_live.WSBSignalEngine = _EngHold
            day2 = community_live.run_live(
                date="2026-06-02", dry_run=False, broker=broker,
                universe_mode="community_liquid", posts_by_symbol=posts,
                ohlcv_full=ohlcv, portfolio=portfolio, memory=memory,
            )
        assert day2["summary"]["buys"] == 0
        assert day2["summary"]["sells"] == 0
        assert portfolio.positions["NVDA"].shares == bought_shares

        with _live_env(decisions_path=os.path.join(d, "day3.jsonl")):
            community_live.WSBSignalEngine = _EngSell
            day3 = community_live.run_live(
                date="2026-06-03", dry_run=False, broker=broker,
                universe_mode="community_liquid", posts_by_symbol=posts,
                ohlcv_full=ohlcv, portfolio=portfolio, memory=memory,
            )
        assert day3["summary"]["sells"] == 1
        assert "NVDA" not in portfolio.positions
        assert [fill.action for fill in broker.get_order_history("", "")] == ["BUY", "SELL"]


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
