# Design Ref: community-opinion-agent — Persistent DecisionLog (판단 원본 jsonl)
# DecisionRouter가 반환한 모든 DecisionResult를 후보별/날짜별로 영속 저장한다.
# BUY뿐 아니라 SKIP/HOLD/REDUCE/SELL/EXIT/DOWNSIZE 판단도 모두 저장.
# DecisionLog = 판단 원본 / Reflection = 결과 검증 → decision_id로 join.
import json
import logging
import os
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def make_decision_id(date, symbol, source, model, ranking, sizing, universe_mode) -> str:
    """결정 식별자 — 같은 (날짜·종목·전략구성)이면 동일 id (reflection join 키).
    하루에 동일 종목은 1회 평가되므로 충돌 없음."""
    parts = [str(p) for p in (date, symbol, source, model, ranking, sizing, universe_mode)]
    return "|".join(parts)


def decision_log_path(run_id: str = None, live: bool = False) -> str:
    """저장 경로 결정. run_id 있으면 백테스트 전용, live면 라이브, 아니면 기본."""
    if run_id:
        return os.path.join(config.COMMUNITY_BACKTEST_DECISIONS_DIR, str(run_id), "decisions.jsonl")
    if live:
        return config.COMMUNITY_LIVE_DECISIONS_FILE
    return config.COMMUNITY_DECISIONS_FILE


def build_decision_record(
    *,
    decision,                 # DecisionResult
    snapshot=None,            # DailyOpinionSnapshot
    universe_decision=None,   # UniverseDecision
    cost_decision=None,       # CostAwareTradeDecision
    date="",
    symbol="",
    source="reddit",
    model="",
    ranking="",
    sizing="",
    universe_mode="",
    run_id=None,
    current_signal="",
    llm_enabled=False,
    llm_model="",
) -> dict:
    """DecisionResult + 도구 결과 → DecisionLog dict (37필드). 누락값 안전 기본."""
    d = decision
    rc = list(_get(d, "reason_codes", []) or [])
    warns = list(_get(d, "warnings", []) or [])
    # safety_overrides: reason_codes의 safety_* + override 관련 warning
    safety_overrides = [c for c in rc if str(c).startswith("safety_")]
    safety_overrides += [w for w in warns if "overrid" in str(w).lower()
                         or "blocked" in str(w).lower() or "차단" in str(w)]

    final_action = _get(d, "action", "")
    rule_action = _get(d, "rule_action", "") or final_action  # 미설정 시 final로 폴백
    llm_action = _get(d, "llm_action", "")

    return {
        "decision_id": make_decision_id(date, symbol, source, model, ranking, sizing, universe_mode),
        "run_id": run_id,
        "date": date,
        "symbol": symbol,
        "source": source,
        "model": model,
        "ranking": ranking,
        "sizing": sizing,
        "universe_mode": universe_mode,
        "current_signal": current_signal,
        "rule_action": rule_action,
        "llm_action": llm_action,
        "final_action": final_action,
        "router_mode": _get(d, "router_mode", "rule_based"),
        "confidence": _get(d, "confidence", 0.0),
        "size_factor": _get(d, "size_factor", 0.0),
        "risk_modifier": _get(d, "risk_modifier", 1.0),
        "stop_loss_pct": _get(d, "stop_loss_pct", None),
        "trailing_stop_pct": _get(d, "trailing_stop_pct", None),
        "reason_codes": rc,
        "reasoning": _get(d, "reasoning", ""),
        "tool_interpretation": _get(d, "tool_interpretation", {}) or {},
        "memory_hits_used": list(_get(d, "memory_hits_used", []) or []),
        "warnings": warns,
        "safety_overrides": safety_overrides,
        "snapshot_summary": _get(snapshot, "summary", ""),
        "opinion_score": _get(snapshot, "opinion_score", None),
        "consensus_ratio": _get(snapshot, "consensus_ratio", None),
        "neutral_ratio": _get(snapshot, "neutral_ratio", None),
        "velocity_state": _get(snapshot, "velocity_state", ""),
        "opinion_trend": _get(snapshot, "opinion_trend", ""),
        "persistence_days": _get(snapshot, "persistence_days", None),
        "universe_allowed": _get(universe_decision, "allowed", None),
        "universe_tier": _get(universe_decision, "universe_tier", ""),
        "universe_reason_codes": list(_get(universe_decision, "reason_codes", []) or []),
        "cost_allowed": _get(cost_decision, "allowed", None),
        "edge_to_cost_ratio": _get(cost_decision, "edge_to_cost_ratio", None),
        "cost_reason_codes": list(_get(cost_decision, "reason_codes", []) or []),
        "llm_enabled": bool(llm_enabled),
        "llm_model": llm_model,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def append_decision_log(record: dict, path: str = None) -> None:
    """DecisionLog 1건을 jsonl에 append. flag OFF면 no-op.
    어떤 예외에도 백테스트를 중단시키지 않는다(로깅만)."""
    if not config.COMMUNITY_DECISION_LOG_ENABLED:
        return
    path = path or config.COMMUNITY_DECISIONS_FILE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"DecisionLog append 실패(무시): {e}")


def load_decision_logs(start_date: str = None, end_date: str = None,
                       symbol: str = None, path: str = None) -> list[dict]:
    """jsonl 로드 + (날짜범위·종목) 필터. 파일 없으면 []."""
    path = path or config.COMMUNITY_DECISIONS_FILE
    if not os.path.exists(path):
        return []
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            dt = rec.get("date", "")
            if start_date and dt < start_date:
                continue
            if end_date and dt > end_date:
                continue
            if symbol and rec.get("symbol") != symbol:
                continue
            out.append(rec)
    return out
