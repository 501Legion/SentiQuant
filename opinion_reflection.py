# Design Ref: community-opinion-agent §3.4 — Low/HighLevelReflection
# Plan FR-2.4: 의견 신호→가격 변화(Low), 실제 매매 entry/exit 분석(High).
# 백테스트는 미래가격 확정 → next_Nd_return 계산. 실시간은 확정 snapshot에만 생성.
import logging
from dataclasses import dataclass, field

import config

logger = logging.getLogger(__name__)

# result_label 임계 (퍼센트)
_SUCCESS_1D_PCT = 2.0
_SUCCESS_3D_PCT = 3.0
_SUCCESS_7D_PCT = 5.0
_NOISY_SWING_PCT = 2.0


@dataclass
class LowLevelReflection:
    date: str
    symbol: str
    opinion_score: float
    consensus_ratio: float
    neutral_ratio: float
    velocity_state: str
    opinion_trend: str
    persistence_days: int
    universe_tier: str
    next_1d_return: float
    next_3d_return: float
    next_7d_return: float
    next_14d_return: float
    result_label: str
    reasoning: str = ""
    lesson: str = ""
    query: dict = field(default_factory=dict)


@dataclass
class HighLevelReflection:
    symbol: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    dollar_pnl: float
    net_pnl_after_cost: float
    total_commission_paid: float
    entry_opinion_score: float
    exit_opinion_score: float
    entry_consensus_ratio: float
    exit_consensus_ratio: float
    entry_neutral_ratio: float
    exit_neutral_ratio: float
    entry_velocity_state: str
    exit_velocity_state: str
    entry_universe_tier: str
    exit_reason: str
    decision_quality: str
    mistake_type: str = ""
    improvement: str = ""
    lesson: str = ""
    cost_drag_pct: float = 0.0
    opinion_score_change: float = 0.0
    consensus_change: float = 0.0
    neutral_ratio_change: float = 0.0
    query: dict = field(default_factory=dict)


def _get(obj, key, default=None):
    """dict 또는 객체에서 안전하게 값 추출."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _classify_low_level(r1, r3, r7, r14) -> str:
    if r1 >= _SUCCESS_1D_PCT:
        return "success_1d"
    if r3 >= _SUCCESS_3D_PCT:
        return "success_3d"
    if r7 >= _SUCCESS_7D_PCT:
        return "success_7d"
    if r14 >= _SUCCESS_7D_PCT:
        return "delayed"
    rets = [r1, r3, r7, r14]
    if max(rets) >= _NOISY_SWING_PCT and min(rets) <= -_NOISY_SWING_PCT:
        return "noisy"
    return "failed"


def build_low_level(snapshot, forward_prices: dict, entry_price: float,
                    universe_tier: str = None) -> LowLevelReflection:
    """snapshot + forward_prices({1,3,7,14: price}) → LowLevelReflection.
    forward returns는 백테스트(미래가격 확정)에서만 의미. entry_price 기준 %수익."""
    def _ret(days):
        p = forward_prices.get(days)
        if p is None or entry_price <= 0:
            return 0.0
        return round((p - entry_price) / entry_price * 100, 4)

    r1, r3, r7, r14 = _ret(1), _ret(3), _ret(7), _ret(14)
    label = _classify_low_level(r1, r3, r7, r14)

    tier = universe_tier or _get(snapshot, "universe_tier", "CORE")
    score = _get(snapshot, "opinion_score", 0.0)
    consensus = _get(snapshot, "consensus_ratio", 0.0)
    neutral = _get(snapshot, "neutral_ratio", 0.0)
    velocity = _get(snapshot, "velocity_state", "NORMAL")
    trend = _get(snapshot, "opinion_trend", _get(snapshot, "sentiment_trend", "FLAT"))
    persistence = _get(snapshot, "persistence_days", 0)

    reasoning = (
        f"opinion {score:.0f}/consensus {consensus:.2f}/neutral {neutral:.0%}"
        f" → 1d {r1:+.1f}% 3d {r3:+.1f}% 7d {r7:+.1f}% ({label})"
    )
    lesson = {
        "success_1d": "강한 합의+낮은 노이즈는 단기 성과로 이어짐",
        "success_3d": "지속성 있는 의견은 3일 내 성과",
        "success_7d": "느리지만 의견 방향대로 수렴",
        "delayed": "신호는 맞았으나 시차 큼 — 진입 타이밍 재검토",
        "noisy": "변동 과대 — 노이즈 필터 강화 필요",
        "failed": "의견 신호가 가격으로 이어지지 않음 — 합의도/지속성 재평가",
    }.get(label, "")

    query = {
        "opinion_score": score, "consensus_ratio": consensus,
        "neutral_ratio": neutral, "velocity_state": velocity,
        "opinion_trend": trend, "persistence_days": persistence,
        "universe_tier": tier,
        "query": _get(snapshot, "query_opinion_trend", ""),
    }

    return LowLevelReflection(
        date=_get(snapshot, "date", ""), symbol=_get(snapshot, "symbol", ""),
        opinion_score=score, consensus_ratio=consensus, neutral_ratio=neutral,
        velocity_state=velocity, opinion_trend=trend, persistence_days=persistence,
        universe_tier=tier, next_1d_return=r1, next_3d_return=r3,
        next_7d_return=r7, next_14d_return=r14, result_label=label,
        reasoning=reasoning, lesson=lesson, query=query,
    )


def _classify_decision(entry_score, net_pnl, exit_reason) -> str:
    if exit_reason == "stop_loss":
        return "risk_management_failure" if net_pnl < 0 else "risk_management_success"
    if exit_reason == "trailing_stop":
        return "risk_management_success"
    if entry_score < config.COMMUNITY_OPINION_SCORE_LOW:
        return "bad_entry"
    if net_pnl > 0:
        return "good_entry_good_exit"
    return "good_entry_bad_exit"


def build_high_level(entry_snap, exit_snap, trade: dict) -> HighLevelReflection:
    """entry/exit opinion 상태 + 청산 trade 기록 → HighLevelReflection.
    trade: reddit_portfolio._sell 레코드(symbol,entry_date,date,entry_price,price,
           shares,gross_pnl,net_pnl,commission,reason)."""
    symbol = trade.get("symbol", _get(entry_snap, "symbol", ""))
    entry_price = float(trade.get("entry_price", 0.0))
    exit_price = float(trade.get("price", 0.0))
    shares = int(trade.get("shares", 0))
    gross_pnl = float(trade.get("gross_pnl", 0.0))
    net_pnl = float(trade.get("net_pnl", gross_pnl))
    commission = float(trade.get("commission", 0.0))
    pnl_pct = float(trade.get("pnl_pct", 0.0))
    exit_reason = trade.get("reason", "")

    notional = entry_price * shares
    cost_drag_pct = round((gross_pnl - net_pnl) / notional * 100, 4) if notional > 0 else 0.0
    # 매수+매도 양다리 수수료 추정 (sell 레코드 commission은 매도분만)
    total_commission = round(commission + (gross_pnl - net_pnl - commission), 4)
    if total_commission < commission:
        total_commission = commission

    e_score = _get(entry_snap, "opinion_score", _get(entry_snap, "entry_score", 0.0)) or 0.0
    x_score = _get(exit_snap, "opinion_score", e_score) or e_score
    e_cons = _get(entry_snap, "consensus_ratio", _get(entry_snap, "entry_consensus_ratio", 0.0)) or 0.0
    x_cons = _get(exit_snap, "consensus_ratio", e_cons) or e_cons
    e_neut = _get(entry_snap, "neutral_ratio", _get(entry_snap, "entry_neutral_ratio", 0.0)) or 0.0
    x_neut = _get(exit_snap, "neutral_ratio", e_neut) or e_neut
    e_vel = _get(entry_snap, "velocity_state", _get(entry_snap, "entry_velocity_state", "")) or ""
    x_vel = _get(exit_snap, "velocity_state", e_vel) or e_vel
    e_tier = _get(entry_snap, "universe_tier", _get(entry_snap, "entry_universe_tier", "CORE")) or "CORE"

    decision_quality = _classify_decision(e_score, net_pnl, exit_reason)
    mistake_type = {
        "bad_entry": "entry_signal_too_weak",
        "good_entry_bad_exit": "exit_timing",
        "risk_management_failure": "stop_hit",
    }.get(decision_quality, "none")
    improvement = {
        "entry_signal_too_weak": "진입 게이팅(opinion_score/consensus) 강화",
        "exit_timing": "opinion_reversal 청산 조건 점검",
        "stop_hit": "포지션 사이즈/변동성 필터 재검토",
    }.get(mistake_type, "현 규칙 유지")
    lesson = (
        f"{symbol} {decision_quality}: net {net_pnl:+.1f}"
        f" (cost_drag {cost_drag_pct:.2f}%), opinion {e_score:.0f}→{x_score:.0f}"
    )

    return HighLevelReflection(
        symbol=symbol, entry_date=trade.get("entry_date", ""),
        exit_date=trade.get("date", ""), entry_price=entry_price, exit_price=exit_price,
        pnl_pct=pnl_pct, dollar_pnl=gross_pnl, net_pnl_after_cost=net_pnl,
        total_commission_paid=total_commission,
        entry_opinion_score=e_score, exit_opinion_score=x_score,
        entry_consensus_ratio=e_cons, exit_consensus_ratio=x_cons,
        entry_neutral_ratio=e_neut, exit_neutral_ratio=x_neut,
        entry_velocity_state=e_vel, exit_velocity_state=x_vel,
        entry_universe_tier=e_tier, exit_reason=exit_reason,
        decision_quality=decision_quality, mistake_type=mistake_type,
        improvement=improvement, lesson=lesson,
        cost_drag_pct=cost_drag_pct,
        opinion_score_change=round(x_score - e_score, 2),
        consensus_change=round(x_cons - e_cons, 3),
        neutral_ratio_change=round(x_neut - e_neut, 3),
        query={"opinion_score": e_score, "consensus_ratio": e_cons,
               "neutral_ratio": e_neut, "velocity_state": e_vel,
               "universe_tier": e_tier, "decision_quality": decision_quality},
    )
