# llm-p1 — LLM 라우터 활용 1단계 회귀 테스트.
# ② 프롬프트 원문 발췌 주입  ③ LLM stop/trailing 보정의 클램프·포지션 배선
import json

import pytest

import config
from decision_router import (
    DecisionRouter, LLMRouter, _clamp_stop, parse_llm_decision,
)
from wsb_signal_engine import WSBSignalEngine


# --- ③ 클램프 ---

def test_clamp_stop_negates_positive():
    assert _clamp_stop(7, lo=-10.0, hi=-3.0) == -7.0


def test_clamp_stop_bounds():
    assert _clamp_stop(-15, lo=-10.0, hi=-3.0) == -10.0
    assert _clamp_stop(-1, lo=-10.0, hi=-3.0) == -3.0


def test_clamp_stop_none_and_garbage():
    assert _clamp_stop(None, lo=-10.0, hi=-3.0) is None
    assert _clamp_stop("abc", lo=-10.0, hi=-3.0) is None


# --- ③ LLM 병합 시 stop 클램프 적용 ---

def _snap(**kw):
    base = dict(opinion_score=72.0, consensus_ratio=2.0, neutral_ratio=0.3,
                persistence_days=3, velocity_state="NORMAL", opinion_trend="UP",
                universe_tier="CORE", is_consensus_sell=False)
    base.update(kw)
    return base


def _decide(router, **kw):
    defaults = dict(symbol="NVDA", current_signal="BUY",
                    daily_opinion_snapshot=_snap(), rsi=55.0,
                    universe_decision={"allowed": True, "size_multiplier": 1.0},
                    cost_filter_decision={"allowed": True, "cost_risk_factor": 1.0},
                    cash=10_000.0, equity=100_000.0)
    defaults.update(kw)
    return router.decide(**defaults)


def test_llm_stop_clamped_in_merge(monkeypatch):
    monkeypatch.setattr(config, "COMMUNITY_LLM_ROUTER_ENABLED", True)
    fake = lambda prompt: json.dumps({
        "action": "BUY", "confidence": 0.9, "size_factor_modifier": 1.0,
        "stop_loss_pct": -20, "trailing_stop_pct": 4,
    })
    router = DecisionRouter(llm_router=True, llm=LLMRouter(complete_fn=fake))
    d = _decide(router)
    assert d.action == "BUY" and d.router_mode == "llm_assisted"
    assert d.stop_loss_pct == -10.0       # -20 → 하한 클램프
    assert d.trailing_stop_pct == -4.0    # +4 → 음수 정규화


def test_llm_no_stop_keeps_rule_default(monkeypatch):
    monkeypatch.setattr(config, "COMMUNITY_LLM_ROUTER_ENABLED", True)
    fake = lambda prompt: json.dumps({
        "action": "BUY", "confidence": 0.9, "size_factor_modifier": 1.0})
    router = DecisionRouter(llm_router=True, llm=LLMRouter(complete_fn=fake))
    d = _decide(router)
    assert d.stop_loss_pct == config.STOP_LOSS_PCT  # rule 기본값 유지


# --- ② 프롬프트 발췌 주입 ---

def test_prompt_includes_excerpts():
    ctx = {"symbol": "GME", "current_signal": "BUY", "snap": _snap(),
           "universe": None, "cost": None, "rsi": 50, "cash": 1000,
           "excerpts": ["YOLO GME to the moon 🚀" * 30, "  ", "earnings beat incoming"]}
    prompt = LLMRouter.build_prompt(ctx)
    assert "to the moon" in prompt
    assert "earnings beat incoming" in prompt
    assert "sarcasm" in prompt                      # 해석 지시 포함
    assert "stop_loss_pct" in prompt                # 스키마에 stop 필드 노출
    # 발췌당 200자 제한
    excerpt_lines = [l for l in prompt.split("\n") if l.startswith("- ")]
    assert all(len(l) <= 2 + LLMRouter._EXCERPT_MAX_CHARS for l in excerpt_lines)


def test_prompt_excerpt_count_capped():
    ctx = {"symbol": "GME", "current_signal": "BUY", "snap": _snap(),
           "universe": None, "cost": None, "rsi": 50, "cash": 1000,
           "excerpts": [f"post {i}" for i in range(20)]}
    prompt = LLMRouter.build_prompt(ctx)
    excerpt_lines = [l for l in prompt.split("\n") if l.startswith("- ")]
    assert len(excerpt_lines) == LLMRouter._EXCERPT_MAX_COUNT


def test_prompt_without_excerpts_unchanged():
    ctx = {"symbol": "GME", "current_signal": "BUY", "snap": _snap(),
           "universe": None, "cost": None, "rsi": 50, "cash": 1000}
    prompt = LLMRouter.build_prompt(ctx)
    assert "post excerpts" not in prompt


def test_decide_accepts_post_excerpts():
    router = DecisionRouter(llm_router=False)
    d = _decide(router, post_excerpts=["bullish dd"])
    assert d.action == "BUY"  # rule 경로는 발췌 미사용 — 동작 불변


# --- ③ check_exit 포지션별 한도 우선 ---

class _NullProvider:
    def score(self, posts):
        return 50.0, []


def _check_exit(position):
    eng = WSBSignalEngine(_NullProvider())
    return eng.check_exit(
        position=position,
        today_ohlcv={"close": position["_close"], "open": None, "prev_close": None,
                     "rsi": 50.0},
        scored={}, ohlcv_cache={}, position_scores={},
    )


def test_check_exit_per_position_stop_overrides_config():
    # pnl -4%: 전역 -7%로는 보유, 포지션 한도 -3%면 청산
    pos = {"symbol": "X", "entry_price": 100.0, "highest_price": 100.0,
           "shares": 1, "_close": 96.0, "stop_loss_pct": -3.0}
    assert _check_exit(pos) == (True, "stop_loss")


def test_check_exit_falls_back_to_config():
    pos = {"symbol": "X", "entry_price": 100.0, "highest_price": 100.0,
           "shares": 1, "_close": 96.0}        # 한도 미지정 → 전역 -7% → 보유
    assert _check_exit(pos) == (False, "")


def test_check_exit_per_position_trailing():
    # 수익 +2%인데 최고점 대비 -3.8%: 전역 -5%로는 보유, 포지션 -3%면 청산
    pos = {"symbol": "X", "entry_price": 100.0, "highest_price": 106.0,
           "shares": 1, "_close": 102.0, "trailing_stop_pct": -3.0}
    assert _check_exit(pos) == (True, "trailing_stop")


# --- ③ 포지션 상태 왕복 보존 ---

def test_position_stop_roundtrip(tmp_path, monkeypatch):
    from reddit_portfolio import RedditPortfolio

    monkeypatch.setattr(config, "REDDIT_DATA_DIR", str(tmp_path))
    p = RedditPortfolio("t_key")
    p._buy("NVDA", 100.0, 5, "2026-06-13", stop_loss_pct=-4.0, trailing_stop_pct=-3.5)
    p.save_state("2026-06-13")

    q = RedditPortfolio("t_key")
    assert q.load_state("2026-06-13")
    assert q.positions["NVDA"].stop_loss_pct == -4.0
    assert q.positions["NVDA"].trailing_stop_pct == -3.5


def test_position_old_state_file_compat(tmp_path, monkeypatch):
    from reddit_portfolio import RedditPortfolio

    monkeypatch.setattr(config, "REDDIT_DATA_DIR", str(tmp_path))
    d = tmp_path / "2026-06-12"
    d.mkdir()
    (d / "portfolio_state_t_key.json").write_text(json.dumps({
        "cash": 90_000.0,
        "positions": {"IBM": {"entry_date": "2026-06-10", "entry_price": 250.0,
                              "shares": 10, "highest_price": 260.0}},
    }), encoding="utf-8")
    q = RedditPortfolio("t_key")
    assert q.load_state("2026-06-12")
    assert q.positions["IBM"].stop_loss_pct is None   # 구 파일 호환


# --- parse 견고화: 숫자 필드에 문자열이 와도 폴백하지 않음 ---

def test_parse_llm_decision_tolerates_string_numbers():
    raw = json.dumps({"action": "BUY", "confidence": 0.8,
                      "size_factor_modifier": 1.0,
                      "stop_loss_pct": "tighten", "risk_modifier": "low"})
    res = parse_llm_decision(raw)
    assert res is not None and res.action == "BUY"
    assert res.stop_loss_pct is None      # 변환 불가 → 기본값 (전체 폴백 아님)
    assert res.risk_modifier == 1.0


# --- determinism-fix: 백테스터가 전역 상태 파일을 건드리지 않음 ---

def test_backtester_run_isolates_state_files(tmp_path, monkeypatch):
    from reddit_backtester import RedditReplayBacktester

    mh = tmp_path / "mention_history.json"
    ps = tmp_path / "position_scores.json"
    mh.write_text('{"NVDA": [5, 5]}', encoding="utf-8")
    ps.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(config, "MENTION_HISTORY_FILE", str(mh))
    monkeypatch.setattr(config, "POSITION_SCORES_FILE", str(ps))
    monkeypatch.setattr(config, "REDDIT_DATA_DIR", str(tmp_path / "no_data"))

    r = RedditReplayBacktester(
        model="finbert", ranking="sentiment", sizing="equal",
        from_date="2026-01-01", to_date="2026-01-02",
    ).run()   # 데이터 없음 → 빈 결과, 단 redirect/restore는 수행됨
    assert r.total_trades == 0
    assert config.MENTION_HISTORY_FILE == str(mh)          # 원복 확인
    assert mh.read_text(encoding="utf-8") == '{"NVDA": [5, 5]}'  # 전역 파일 불침
