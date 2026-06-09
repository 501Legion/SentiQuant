# Design Ref: community-opinion-agent §3.5 — DecisionRouter (rule-based 기본)
# Plan FR-3.1~3.4: 도구 결과(신호·snapshot·memory·universe·cost·기술지표)를 해석해
# BUY/HOLD/SELL/REDUCE/SKIP/EXIT 판단 + 구조화된 reasoning 기록.
# LLM은 자율 매매자가 아니라 rule-based 1차 판단을 보정하는 보조 라우터 (module-8).
# 8개 하드 안전장치(_enforce_safety)는 rule-based·LLM 양쪽에 적용.
import json
import logging
import re
from dataclasses import dataclass, field

import config

logger = logging.getLogger(__name__)

_ACTIONS = {"BUY", "HOLD", "SELL", "REDUCE", "SKIP", "EXIT"}
_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass
class DecisionResult:
    action: str = "SKIP"
    confidence: float = 0.0
    size_factor: float = 0.0
    risk_modifier: float = 1.0
    stop_loss_pct: float | None = None
    trailing_stop_pct: float | None = None
    reason_codes: list = field(default_factory=list)
    reasoning: str = ""
    tool_interpretation: dict = field(default_factory=dict)
    memory_hits_used: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    router_mode: str = "rule_based"
    # DecisionLog용 — rule 1차 판단 / LLM 원raw 판단 (로깅·추적용, 동작엔 미영향)
    rule_action: str = ""
    llm_action: str = ""


@dataclass
class LLMDecisionResult:
    """LLM 보정 결과 (Design Ref §3.5.3). size_factor 대신 modifier(곱) 사용.
    router_mode는 없음 — 최종 병합은 DecisionResult가 담당."""
    action: str
    confidence: float
    size_factor_modifier: float = 1.0
    risk_modifier: float = 1.0
    stop_loss_pct: float | None = None
    trailing_stop_pct: float | None = None
    reason_codes: list = field(default_factory=list)
    reasoning: str = ""
    tool_interpretation: dict = field(default_factory=dict)
    memory_hits_used: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def parse_llm_decision(text: str) -> "LLMDecisionResult | None":
    """LLM 원문 → LLMDecisionResult. strict JSON 위반/파싱 실패 시 None (fallback)."""
    if not text:
        return None
    raw = text.strip()
    m = _JSON_FENCE.search(raw)
    if m:
        raw = m.group(1)
    else:
        # 첫 '{' ~ 마지막 '}' 추출
        s, e = raw.find("{"), raw.rfind("}")
        if s != -1 and e != -1 and e > s:
            raw = raw[s:e + 1]
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action", "")).upper()
    if action not in _ACTIONS:
        return None
    conf = data.get("confidence")
    if not isinstance(conf, (int, float)):
        if config.COMMUNITY_LLM_ROUTER_REQUIRE_STRICT_JSON:
            return None
        conf = 0.0
    mod = data.get("size_factor_modifier", 1.0)
    if not isinstance(mod, (int, float)):
        mod = 1.0
    interp = data.get("tool_interpretation")
    if not isinstance(interp, dict):
        interp = {}
    return LLMDecisionResult(
        action=action, confidence=float(conf), size_factor_modifier=float(mod),
        risk_modifier=float(data.get("risk_modifier", 1.0) or 1.0),
        stop_loss_pct=data.get("stop_loss_pct"),
        trailing_stop_pct=data.get("trailing_stop_pct"),
        reason_codes=list(data.get("reason_codes", []) or []),
        reasoning=str(data.get("reasoning", "")),
        tool_interpretation=interp,
        memory_hits_used=list(data.get("memory_hits_used", []) or []),
        warnings=list(data.get("warnings", []) or []),
    )


def _get(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _empty_interpretation() -> dict:
    return {k: "" for k in (
        "opinion_signal", "consensus_signal", "noise_signal", "memory_signal",
        "reflection_signal", "technical_signal", "universe_signal",
        "cost_signal", "risk_signal",
    )}


def historical_success_score(reflections: list) -> float | None:
    """retrieved reflection들의 성공/실패 비율 (0~1). 데이터 없으면 None."""
    succ = fail = 0
    for r in reflections or []:
        label = str(_get(r, "result_label", "") or _get(r, "decision_quality", ""))
        if label.startswith("success") or label.startswith("good") or "success" in label:
            succ += 1
        elif label.startswith("failed") or label.startswith("bad") or "failure" in label:
            fail += 1
    total = succ + fail
    return (succ / total) if total else None


class DecisionRouter:
    """rule-based 1차 판단 (+ 선택적 LLM 보정은 module-8에서 확장)."""

    def __init__(self, llm_router: bool = False, llm: "LLMRouter" = None):
        # --llm-router 플래그 OR config flag 둘 중 하나면 활성 (기본은 둘 다 OFF)
        self.llm_router = bool(llm_router) or bool(config.COMMUNITY_LLM_ROUTER_ENABLED)
        self._llm = llm   # 테스트 주입용 (None이면 _apply_llm에서 lazy 생성)

    def decide(self, *, symbol, current_signal, daily_opinion_snapshot,
               retrieved_similar_opinions=None, retrieved_low_level_reflections=None,
               retrieved_high_level_reflections=None, rsi=None, atr=None,
               market_filter_status=None, universe_decision=None,
               cost_filter_decision=None, current_position=None,
               cash=0.0, equity=0.0, risk_settings=None) -> DecisionResult:
        ctx = dict(
            symbol=symbol, current_signal=(current_signal or "").upper(),
            snap=daily_opinion_snapshot,
            sim=retrieved_similar_opinions or [],
            low=retrieved_low_level_reflections or [],
            high=retrieved_high_level_reflections or [],
            rsi=rsi, atr=atr, market_filter_status=market_filter_status,
            universe=universe_decision, cost=cost_filter_decision,
            position=current_position, cash=cash, equity=equity,
            risk=risk_settings or {},
        )
        result = self._rule_based(ctx)
        rule_action = result.action          # DecisionLog용 1차 판단 보존
        if self.llm_router:
            result = self._apply_llm(result, ctx)   # module-8 (llm_action 설정)
        result = self._enforce_safety(result, ctx)
        result.rule_action = rule_action
        return result

    # ------------------------------------------------------------------
    # Rule-based (Design Ref §3.5.2)
    # ------------------------------------------------------------------
    def _rule_based(self, ctx) -> DecisionResult:
        snap = ctx["snap"]
        score = _get(snap, "opinion_score", 0.0) or 0.0
        consensus = _get(snap, "consensus_ratio", 0.0) or 0.0
        neutral = _get(snap, "neutral_ratio", 0.0) or 0.0
        persistence = _get(snap, "persistence_days", 0) or 0
        velocity = _get(snap, "velocity_state", "NORMAL")
        trend = _get(snap, "opinion_trend", _get(snap, "sentiment_trend", "FLAT"))
        tier = _get(snap, "universe_tier", "CORE")
        is_sell = _get(snap, "is_consensus_sell", False)

        univ = ctx["universe"]
        cost = ctx["cost"]
        univ_allowed = _get(univ, "allowed", True)
        cost_allowed = _get(cost, "allowed", True)
        size_mult = _get(univ, "size_multiplier", 1.0) or 1.0
        cost_factor = _get(cost, "cost_risk_factor", 1.0)
        cost_factor = 1.0 if cost_factor is None else cost_factor
        has_position = ctx["position"] is not None
        hist = historical_success_score(ctx["low"] + ctx["high"])

        interp = _empty_interpretation()
        interp["opinion_signal"] = f"score {score:.0f}"
        interp["consensus_signal"] = f"consensus {consensus:.2f}"
        interp["noise_signal"] = f"neutral {neutral:.2f}"
        interp["technical_signal"] = f"rsi {rsi}" if (rsi := ctx['rsi']) is not None else "rsi n/a"
        interp["universe_signal"] = f"{tier} allowed={univ_allowed}"
        interp["cost_signal"] = f"allowed={cost_allowed} factor={cost_factor}"
        interp["memory_signal"] = f"{len(ctx['sim'])} similar"
        interp["reflection_signal"] = (
            f"hist_success={hist:.2f}" if hist is not None else "no history")
        interp["risk_signal"] = f"trend {trend} persist {persistence}d vel {velocity}"

        reasons: list[str] = []
        warnings: list[str] = []
        mem_hits = [_get(r, "symbol", "") for r in (ctx["sim"][:3])]

        # === 보유 포지션: SELL / REDUCE / EXIT / HOLD ===
        if has_position:
            if neutral > config.COMMUNITY_NEUTRAL_RATIO_MAX:
                reasons.append("neutral_spike")
                return self._mk("EXIT", 0.8, 0.0, reasons, interp, mem_hits, warnings,
                                f"{ctx['symbol']} EXIT: neutral {neutral:.0%} 급증")
            if consensus <= 1.0 or is_sell:
                reasons.append("consensus_break")
                return self._mk("SELL", 0.75, 0.0, reasons, interp, mem_hits, warnings,
                                f"{ctx['symbol']} SELL: 합의 붕괴 consensus {consensus:.2f}")
            if trend == "DOWN" or (hist is not None and hist < 0.4):
                reasons.append("opinion_weakening")
                return self._mk("REDUCE", 0.6, 0.5, reasons, interp, mem_hits, warnings,
                                f"{ctx['symbol']} REDUCE: 추세 약화/과거 실패 패턴")
            reasons.append("opinion_intact")
            return self._mk("HOLD", 0.6, 0.0, reasons, interp, mem_hits, warnings,
                            f"{ctx['symbol']} HOLD: 의견 유지")

        # === 신규 진입 평가: SKIP / BUY ===
        if not univ_allowed:
            reasons.append("universe_blocked")
            return self._mk("SKIP", 0.9, 0.0, reasons, interp, mem_hits, warnings,
                            f"{ctx['symbol']} SKIP: universe blocked")
        if not cost_allowed:
            reasons.append("cost_blocked")
            return self._mk("SKIP", 0.9, 0.0, reasons, interp, mem_hits, warnings,
                            f"{ctx['symbol']} SKIP: cost-aware filter blocked")
        if neutral > config.COMMUNITY_NEUTRAL_RATIO_MAX:
            reasons.append("high_noise")
            return self._mk("SKIP", 0.85, 0.0, reasons, interp, mem_hits, warnings,
                            f"{ctx['symbol']} SKIP: neutral {neutral:.0%} > max")
        if consensus < config.COMMUNITY_CONSENSUS_MIN_RATIO:
            reasons.append("weak_consensus")
            return self._mk("SKIP", 0.85, 0.0, reasons, interp, mem_hits, warnings,
                            f"{ctx['symbol']} SKIP: consensus {consensus:.2f} < min")
        if score < config.COMMUNITY_OPINION_SCORE_LOW:
            reasons.append("low_opinion_score")
            return self._mk("SKIP", 0.8, 0.0, reasons, interp, mem_hits, warnings,
                            f"{ctx['symbol']} SKIP: score {score:.0f} < low")
        if ctx["current_signal"] not in ("BUY", "STRONG_BUY"):
            reasons.append("no_rule_signal")
            return self._mk("SKIP", 0.7, 0.0, reasons, interp, mem_hits, warnings,
                            f"{ctx['symbol']} SKIP: rule 신호 없음({ctx['current_signal']})")
        if ctx["cash"] <= 0:
            reasons.append("insufficient_cash")
            return self._mk("SKIP", 0.9, 0.0, reasons, interp, mem_hits, warnings,
                            f"{ctx['symbol']} SKIP: 현금 부족")

        # --- BUY 승인 + size factor 산정 ---
        size = size_mult * cost_factor
        reasons.append("buy_approved")
        if velocity == "NEW_SPIKE" and persistence < config.COMMUNITY_OPINION_PERSISTENCE_MIN_DAYS:
            size *= config.COMMUNITY_NEW_SPIKE_FACTOR
            reasons.append("new_spike_downsize")
        if persistence < config.COMMUNITY_OPINION_PERSISTENCE_MIN_DAYS:
            size *= config.COMMUNITY_OPINION_PERSISTENCE_WEAK_FACTOR
            reasons.append("low_persistence_downsize")
        if hist is not None and hist < 0.4:
            size *= 0.6
            reasons.append("history_downsize")
            warnings.append("과거 유사 사례 실패 비중 높음")
        if consensus >= config.WSB_OPINION_CONSENSUS_STRONG_RATIO and trend == "UP":
            size *= 1.1
            reasons.append("strong_consensus_upsize")

        size = max(0.0, min(config.COMMUNITY_SIZE_FACTOR_MAX, size))
        confidence = max(0.0, min(1.0,
            0.5 + (consensus - config.COMMUNITY_CONSENSUS_MIN_RATIO) * 0.1
            + (score - config.COMMUNITY_OPINION_SCORE_LOW) / 100.0
            + persistence * 0.03 - max(0.0, neutral - 0.3) * 0.3))

        return self._mk(
            "BUY", round(confidence, 3), round(size, 4), reasons, interp, mem_hits, warnings,
            f"{ctx['symbol']} BUY: consensus {consensus:.2f}, score {score:.0f},"
            f" persist {persistence}d, size {size:.2f}",
            stop_loss_pct=config.STOP_LOSS_PCT,
            trailing_stop_pct=config.TRAILING_STOP_PCT,
        )

    @staticmethod
    def _mk(action, confidence, size, reasons, interp, mem_hits, warnings, reasoning,
            *, stop_loss_pct=None, trailing_stop_pct=None, router_mode="rule_based") -> DecisionResult:
        return DecisionResult(
            action=action, confidence=confidence, size_factor=size,
            risk_modifier=1.0, stop_loss_pct=stop_loss_pct,
            trailing_stop_pct=trailing_stop_pct, reason_codes=list(reasons),
            reasoning=reasoning, tool_interpretation=interp,
            memory_hits_used=mem_hits, warnings=list(warnings),
            router_mode=router_mode,
        )

    # ------------------------------------------------------------------
    # 8개 하드 안전장치 (Design Ref §3.5.4) — rule-based·LLM 공통 최종 가드
    # ------------------------------------------------------------------
    def _enforce_safety(self, result: DecisionResult, ctx) -> DecisionResult:
        snap = ctx["snap"]
        neutral = _get(snap, "neutral_ratio", 0.0) or 0.0
        consensus = _get(snap, "consensus_ratio", 0.0) or 0.0
        univ = ctx["universe"]
        cost = ctx["cost"]
        univ_allowed = _get(univ, "allowed", True)
        cost_allowed = _get(cost, "allowed", True)
        ambiguity = "TICKER_AMBIGUOUS" in (_get(univ, "reason_codes", []) or [])
        has_position = ctx["position"] is not None

        if result.action == "BUY":
            block_reason = None
            if neutral > config.COMMUNITY_NEUTRAL_RATIO_MAX:
                block_reason = "safety_neutral_exceeds_max"
            elif consensus < config.COMMUNITY_CONSENSUS_MIN_RATIO:
                block_reason = "safety_consensus_below_min"
            elif ambiguity:
                block_reason = "safety_ticker_ambiguous"
            elif not univ_allowed:
                block_reason = "safety_universe_blocked"
            elif not cost_allowed:
                block_reason = "safety_cost_blocked"
            elif ctx["cash"] <= 0:
                block_reason = "safety_insufficient_cash"
            if block_reason:
                result.action = "SKIP"
                result.size_factor = 0.0
                result.reason_codes = list(result.reason_codes) + [block_reason]
                result.warnings = list(result.warnings) + [f"BUY 차단: {block_reason}"]

        # 보유 없으면 SELL/EXIT/REDUCE 금지
        if result.action in ("SELL", "EXIT", "REDUCE") and not has_position:
            result.action = "SKIP"
            result.size_factor = 0.0
            result.reason_codes = list(result.reason_codes) + ["safety_no_position"]

        # size_factor clamp
        result.size_factor = max(0.0, min(config.COMMUNITY_SIZE_FACTOR_MAX, result.size_factor))
        return result

    # ------------------------------------------------------------------
    # LLM 보정 (Design Ref §3.5.3) — rule-based 1차 판단을 승인/축소/보류만.
    # 안전장치: rule SKIP을 LLM 단독 BUY로 못 뒤집음 / low confidence면 rule 우선
    #          / invalid JSON이면 fallback. 최종 _enforce_safety가 BUY 금지조건 재차단.
    # ------------------------------------------------------------------
    def _apply_llm(self, base: DecisionResult, ctx) -> DecisionResult:
        llm = self._llm or LLMRouter()
        res = llm.query(ctx)
        if res is None:
            base.warnings = list(base.warnings) + ["llm_fallback_to_rule_based"]
            return base   # invalid/실패 → rule-based 유지

        base.llm_action = res.action   # LLM 원raw 판단 기록 (보정 전)

        # confidence 낮으면 rule-based 우선
        if res.confidence < config.COMMUNITY_LLM_ROUTER_MIN_CONFIDENCE:
            base.warnings = list(base.warnings) + ["llm_low_confidence_kept_rule"]
            return base

        # rule SKIP을 LLM이 단독 BUY로 못 뒤집음
        if base.action == "SKIP" and res.action == "BUY":
            base.warnings = list(base.warnings) + ["llm_buy_overridden_by_rule_skip"]
            return base

        merged = DecisionResult(
            action=res.action,
            confidence=round((base.confidence + res.confidence) / 2, 3),
            size_factor=max(0.0, min(config.COMMUNITY_SIZE_FACTOR_MAX,
                                     base.size_factor * res.size_factor_modifier)),
            risk_modifier=res.risk_modifier or 1.0,
            stop_loss_pct=res.stop_loss_pct if res.stop_loss_pct is not None else base.stop_loss_pct,
            trailing_stop_pct=res.trailing_stop_pct if res.trailing_stop_pct is not None else base.trailing_stop_pct,
            reason_codes=list(base.reason_codes) + list(res.reason_codes) + ["llm_assisted"],
            reasoning=f"{base.reasoning} | LLM: {res.reasoning}",
            tool_interpretation=(res.tool_interpretation or base.tool_interpretation),
            memory_hits_used=base.memory_hits_used or res.memory_hits_used,
            warnings=list(base.warnings) + list(res.warnings),
            router_mode="llm_assisted",
            llm_action=res.action,
        )
        return merged


# ---------------------------------------------------------------------------
# LLMRouter — strict JSON 출력. 실제 호출은 기존 OpenAI(config.GPT_MODEL).
# 활성 게이팅은 DecisionRouter(self.llm_router = --llm-router 플래그 OR config flag)에서
# 처리하므로, query()는 호출 시 항상 시도한다(실패/무효 JSON이면 None → rule-based fallback).
# ---------------------------------------------------------------------------
class LLMRouter:
    def __init__(self, complete_fn=None):
        """complete_fn(prompt:str)->str 주입 가능(테스트). None이면 OpenAI 호출."""
        self._complete = complete_fn or self._openai_complete

    def query(self, ctx) -> "LLMDecisionResult | None":
        try:
            prompt = self.build_prompt(ctx)
            raw = self._complete(prompt)
            return parse_llm_decision(raw)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"LLMRouter query 실패 → rule-based fallback: {e}")
            return None

    @staticmethod
    def build_prompt(ctx) -> str:
        snap = ctx.get("snap")
        univ = ctx.get("universe")
        cost = ctx.get("cost")
        return (
            "You are a trading decision ROUTER, not an autonomous trader. "
            "Interpret the rule-based tools and choose one action among "
            "BUY/HOLD/SELL/REDUCE/SKIP/EXIT. You may approve, downsize, hold, or "
            "reject the rule candidate but MUST NOT invent new buys. "
            "Respond with STRICT JSON only.\n"
            f"symbol={ctx.get('symbol')} signal={ctx.get('current_signal')}\n"
            f"opinion_score={_get(snap,'opinion_score')} consensus={_get(snap,'consensus_ratio')}"
            f" neutral={_get(snap,'neutral_ratio')} trend={_get(snap,'opinion_trend')}"
            f" persistence={_get(snap,'persistence_days')} velocity={_get(snap,'velocity_state')}\n"
            f"universe_allowed={_get(univ,'allowed')} tier={_get(univ,'universe_tier')}"
            f" cost_allowed={_get(cost,'allowed')}\n"
            f"rsi={ctx.get('rsi')} cash={ctx.get('cash')}\n"
            'JSON schema: {"action","confidence","size_factor_modifier","risk_modifier",'
            '"reason_codes","reasoning"}'
        )

    @staticmethod
    def _openai_complete(prompt: str) -> str:
        """기존 OpenAI(config.GPT_MODEL) 호출. 키/패키지 없으면 예외 → query가 fallback.
        신모델 호환: max_completion_tokens 사용, temperature 미지원 시 자동 재시도."""
        from openai import OpenAI, BadRequestError
        client = OpenAI(api_key=config.OPENAI_API_KEY)
        kwargs = dict(
            model=config.GPT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=config.COMMUNITY_LLM_ROUTER_MAX_TOKENS,
            temperature=config.COMMUNITY_LLM_ROUTER_TEMPERATURE,
        )
        try:
            resp = client.chat.completions.create(**kwargs)
        except BadRequestError as e:
            # 일부 신모델은 temperature 커스텀 미지원 → 제거 후 재시도
            if "temperature" in str(e):
                kwargs.pop("temperature", None)
                resp = client.chat.completions.create(**kwargs)
            else:
                raise
        return resp.choices[0].message.content or ""
