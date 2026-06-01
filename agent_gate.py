# Design Ref: community-opinion-agent-live §3.1 — 후보 1건 평가 순수 helper
# snapshot → universe → cost → memory → router → (DecisionResult, OrderIntent).
# 부수효과 없음(파일 I/O·주문 X) — 라이브 드라이버(community_live)와 백테스터가
# 동일 의사결정을 공유할 수 있도록 분리. 5개 에이전트 모듈은 주입받아 재사용.
import logging
import math
from dataclasses import dataclass, field

import config
from wsb_signal_engine import build_daily_snapshot
from decision_log import make_decision_id

logger = logging.getLogger(__name__)


@dataclass
class OrderIntent:
    symbol: str
    action: str                 # BUY|HOLD|SELL|REDUCE|SKIP|EXIT (DecisionResult.action)
    side: str                   # "BUY" | "SELL" | "" (주문 없음)
    shares: int
    size_factor: float
    decision_id: str
    reason: str = ""
    snapshot_summary: str = ""


def _recent_volatility_pct(ohlcv, lookback: int = 14) -> float | None:
    """OHLCV DataFrame 최근 lookback일 평균 (고-저)/종가 × 100 — cost edge proxy."""
    if ohlcv is None:
        return None
    try:
        if getattr(ohlcv, "empty", True) or "high" not in ohlcv.columns:
            return None
        t = ohlcv.tail(lookback)
        v = ((t["high"] - t["low"]) / t["close"]).mean() * 100
        return float(v) if v == v else None
    except Exception:
        return None


def _prev_close(ohlcv) -> float | None:
    try:
        if ohlcv is None or getattr(ohlcv, "empty", True) or len(ohlcv) < 2:
            return None
        return float(ohlcv.iloc[-2]["close"])
    except Exception:
        return None


def evaluate_candidate(
    *,
    symbol: str,
    scored_entry: dict,
    history: list,
    run_meta: dict,                # {date, source, model, ranking, sizing, universe_mode, run_id}
    universe_filter,
    cost_filter,
    memory,
    router,
    open_price: float,
    account_equity: float,
    ohlcv=None,
    price: float = None,
    avg_dollar_volume: float = None,
    recent_volatility_pct: float = None,
    atr_pct: float = None,
    rsi: float = None,
    current_position: dict = None,
    texts: list = None,
    market_filter_status: str = "NORMAL",
    cash: float = None,
) -> tuple["DecisionResult", OrderIntent]:
    """후보 1건 → (DecisionResult, OrderIntent). 순수(부수효과 없음)."""
    date_str = run_meta.get("date", "")

    # 1. universe
    univ_dec = universe_filter.decide(
        symbol, ohlcv=ohlcv, price=(price if price is not None else open_price),
        avg_dollar_volume=avg_dollar_volume,
    )

    # 2. snapshot
    if recent_volatility_pct is None:
        recent_volatility_pct = _recent_volatility_pct(ohlcv)
    snap = build_daily_snapshot(
        symbol, scored_entry, history, universe_decision=univ_dec,
        texts=texts, atr=None, prev_close=_prev_close(ohlcv), date_str=date_str,
    )

    # 3. cost
    conviction = max(0.0, min(1.0, (scored_entry.get("score", 50.0) - 50.0) / 40.0))
    cost_dec = cost_filter.evaluate(
        atr_pct=atr_pct, recent_volatility_pct=recent_volatility_pct,
        opinion_conviction=conviction,
    )

    # 4. memory (라이브: 영속 누적 조회)
    query = {
        "symbol": symbol,
        "opinion_score": getattr(snap, "opinion_score", None),
        "consensus_ratio": getattr(snap, "consensus_ratio", None),
        "neutral_ratio": getattr(snap, "neutral_ratio", None),
        "velocity_state": getattr(snap, "velocity_state", None),
        "opinion_trend": getattr(snap, "opinion_trend", None),
        "universe_tier": getattr(snap, "universe_tier", None),
        "top_keywords": getattr(snap, "top_keywords", []),
    }
    sim = memory.retrieve_similar_opinions(symbol, query)
    low_refs = memory.retrieve_low_level_reflections(symbol, query)
    high_refs = memory.retrieve_high_level_reflections(symbol, query)

    # 5. router
    decision = router.decide(
        symbol=symbol, current_signal=scored_entry.get("signal", "NEUTRAL"),
        daily_opinion_snapshot=snap,
        retrieved_similar_opinions=sim,
        retrieved_low_level_reflections=low_refs,
        retrieved_high_level_reflections=high_refs,
        rsi=rsi, atr=None, market_filter_status=market_filter_status,
        universe_decision=univ_dec, cost_filter_decision=cost_dec,
        current_position=current_position,
        cash=(cash if cash is not None else account_equity),
        equity=account_equity, risk_settings={},
    )

    # 6. OrderIntent
    decision_id = make_decision_id(
        date_str, symbol, run_meta.get("source", "reddit"), run_meta.get("model", ""),
        run_meta.get("ranking", ""), run_meta.get("sizing", ""),
        run_meta.get("universe_mode", ""),
    )
    action = decision.action
    pos_shares = int((current_position or {}).get("shares", 0))
    side, shares = "", 0
    if action == "BUY":
        side = "BUY"
        if open_price > 0 and account_equity > 0:
            shares = math.floor(account_equity * config.EQUAL_POSITION_PCT
                                * decision.size_factor / open_price)
            shares = max(0, shares)
    elif action in ("SELL", "EXIT"):
        side, shares = "SELL", pos_shares
    elif action == "REDUCE":
        side, shares = "SELL", pos_shares // 2

    intent = OrderIntent(
        symbol=symbol, action=action, side=side, shares=shares,
        size_factor=round(decision.size_factor, 4), decision_id=decision_id,
        reason=(decision.reasoning or "")[:200],
        snapshot_summary=getattr(snap, "summary", ""),
    )
    return decision, intent
