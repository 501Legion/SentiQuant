# Design Ref: daily-decision-report §2 — Option C: ReportContext + 순수 Markdown 포매터
# run_live 종료 시 funnel(입력→중립→컨센서스→게이트→매수/매도)을 사람이 읽는 MD로 집계.
# read-only: 판단을 재계산하지 않고 기존 결과만 포맷한다(NFR-03).
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

# 매수/매도로 분류되는 액션 집합
_SELL_ACTIONS = {"SELL", "EXIT", "REDUCE"}

ACTION_LABELS = {
    "BUY": "매수",
    "STRONG_BUY": "강한 매수",
    "SELL": "매도",
    "STRONG_SELL": "강한 매도",
    "EXIT": "매도",
    "REDUCE": "비중 축소",
    "SKIP": "보류",
    "HOLD": "관망",
}

REASON_CODE_LABELS = {
    "universe_blocked": "투자 대상 조건 미충족",
    "cost_blocked": "비용/위험 기준 미충족",
    "EDGE_BELOW_COST_THRESHOLD": "비용 대비 기대수익 부족",
    "insufficient_cash": "현금 부족",
    "low_opinion_score": "여론 점수 미달",
    "weak_consensus": "매수 의견 합의 부족",
    "high_noise": "중립 의견 비율 과다",
    "neutral_spike": "중립 의견 급증",
    "consensus_break": "컨센서스 붕괴",
    "no_rule_signal": "룰 신호 없음",
    "bullish_trend": "상승 추세",
    "high_momentum": "강한 모멘텀",
    "trend_up_with_moderate_momentum": "완만한 상승 모멘텀",
    "community_hype_detected": "커뮤니티 과열 감지",
    "possible_pump_risk": "급등 과열 위험 가능성",
    "rsi_elevated": "RSI 과열권",
    "rsi_neutral_to_slightly_weak": "RSI 중립~소폭 약세",
    "core_universe_allowed": "핵심 유니버스 통과",
    "bullish_aggregate_but_mixed_social_sentiment": "종합 여론은 긍정이나 반응 혼재",
    "sarcasm_and_price-prediction_noise": "풍자/가격예측성 잡음",
    "approve_candidate_but_downsize": "후보 승인, 비중 축소",
    "history_downsize": "과거 유사 사례 부진 — 비중 축소",
    "low_persistence_downsize": "신호 지속일 부족 — 비중 축소",
    "new_spike_downsize": "신규 급등 종목 — 비중 축소",
    "buy_approved": "매수 기준 통과",
    "strong_consensus_upsize": "강한 매수 합의 — 비중 확대",
    "llm_assisted": "LLM 보조 판단",
    "llm_fallback_to_rule_based": "LLM 실패 — 룰 기반 대체",
    "llm_low_confidence_kept_rule": "LLM 저신뢰 — 룰 판단 유지",
    "llm_buy_overridden_by_rule_skip": "룰 우선 — LLM 매수 기각",
}


def _get(obj, key, default=None):
    """dict/객체 공용 안전 getter (decision_log._get와 동일 패턴)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _fmt_action(value) -> str:
    text = re.sub(r"[\s\-]+", "_", str(value or "-").strip()).upper()
    return ACTION_LABELS.get(text, str(value or "-"))


def _code_key(value) -> str:
    return re.sub(r"[\s\-]+", "_", str(value or "").strip()).lower()


def _fmt_reason_code(code) -> str:
    raw = str(code or "").strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        return "-"
    return REASON_CODE_LABELS.get(raw, REASON_CODE_LABELS.get(_code_key(raw), raw.replace("_", " ")))


def _fmt_reason_codes(codes) -> str:
    if isinstance(codes, str):
        parts = [part.strip() for part in re.split(r"[,;]", codes) if part.strip()]
    else:
        parts = [str(part).strip() for part in (codes or []) if str(part).strip()]
    labels = [_fmt_reason_code(part) for part in parts]
    return ", ".join(labels) if labels else "-"


def _fmt_reason_text(text) -> str:
    result = str(text or "").strip()
    if not result:
        return ""
    tokens = sorted(
        set(REASON_CODE_LABELS) | {key.upper().replace("_", " ") for key in REASON_CODE_LABELS},
        key=len,
        reverse=True,
    )
    for token in tokens:
        label = _fmt_reason_code(token)
        result = re.sub(rf"(?<![\w-]){re.escape(token)}(?![\w-])", label, result)
    result = re.sub(r"\bSTRONG_BUY\b", ACTION_LABELS["STRONG_BUY"], result)
    result = re.sub(r"\bBUY\b", ACTION_LABELS["BUY"], result)
    result = re.sub(r"\bHOLD\b", ACTION_LABELS["HOLD"], result)
    result = re.sub(r"\bSKIP\b", ACTION_LABELS["SKIP"], result)
    return result


@dataclass
class ReportContext:
    """community_live → decision_report 입력 (Design §5.1)."""
    date: str
    signal_details: list = field(default_factory=list)   # run_pipeline 출력(funnel 플래그)
    decisions: list = field(default_factory=list)         # {symbol, action, size_factor, decision_id, router_mode}
    orders: list = field(default_factory=list)            # executor 결과 {symbol, side, shares, executed}
    snapshots: dict = field(default_factory=dict)         # (sym,date)→DailyOpinionSnapshot
    summary: dict = field(default_factory=dict)           # run_live summary
    decision_records: list = field(default_factory=list)  # (선택) decision_log 레코드(게이트 사유 보강)


def _derive_funnel(ctx: ReportContext) -> dict:
    """signal_details + decisions + orders로 funnel 단계별 결과 도출 (Design §6.1 / D4).
    # Plan SC: SC-02 funnel 4단계, SC-04 탈락 관문 표기
    순수 함수 — 부수효과 없음."""
    date = ctx.date
    sigs = ctx.signal_details or []
    dec_by_sym = {d.get("symbol"): d for d in (ctx.decisions or [])}
    rec_by_sym = {r.get("symbol"): r for r in (ctx.decision_records or [])}

    bought = {o.get("symbol") for o in (ctx.orders or []) if o.get("side") == "BUY"}
    sold = {o.get("symbol") for o in (ctx.orders or []) if o.get("side") == "SELL"}

    neutral_dropped, consensus_dropped, gate_dropped = [], [], []
    for d in sigs:
        sym = d.get("symbol")
        if d.get("neutral_filtered"):
            neutral_dropped.append({"symbol": sym, "neutral_ratio": d.get("neutral_ratio", 0.0)})
        elif not d.get("passed_consensus"):
            consensus_dropped.append({
                "symbol": sym, "bullish": d.get("bullish", 0), "bearish": d.get("bearish", 0),
                "reason": (
                    f"상승 {d.get('bullish', 0)} / 하락 {d.get('bearish', 0)}로 "
                    "매수 우세 기준 미달"
                ),
            })
        elif sym not in bought and sym not in sold:
            # 컨센서스는 통과했으나 universe/cost/router 게이트에서 탈락 (SKIP/HOLD)
            rec = rec_by_sym.get(sym, {})
            reasons = (list(rec.get("reason_codes", []) or [])
                       + list(rec.get("universe_reason_codes", []) or [])
                       + list(rec.get("cost_reason_codes", []) or []))
            gate_dropped.append({
                "symbol": sym,
                "final_action": _get(dec_by_sym.get(sym), "action", "SKIP"),
                "reason_codes": reasons,
            })

    # 최종 매수 — orders BUY를 snapshot/decision으로 보강
    buys = []
    for o in (ctx.orders or []):
        if o.get("side") != "BUY":
            continue
        sym = o.get("symbol")
        snap = ctx.snapshots.get((sym, date)) if ctx.snapshots else None
        dec = dec_by_sym.get(sym)
        buys.append({
            "symbol": sym,
            "score": _get(snap, "opinion_score"),
            "consensus_ratio": _get(snap, "consensus_ratio"),
            "size_factor": _get(dec, "size_factor", 0.0),
            "shares": o.get("shares", 0),
            "decision_id": _get(dec, "decision_id", ""),
            "executed": o.get("executed", False),
        })

    # 최종 매도 — orders SELL을 decision action/reason으로 보강 (보유-only 종목 포함, D5)
    sells = []
    for o in (ctx.orders or []):
        if o.get("side") != "SELL":
            continue
        sym = o.get("symbol")
        dec = dec_by_sym.get(sym)
        rec = rec_by_sym.get(sym, {})
        reason = _get(dec, "reason", "") or ",".join(rec.get("reason_codes", []) or []) or _get(dec, "action", "")
        sells.append({
            "symbol": sym,
            "action": _get(dec, "action", "SELL"),
            "reason": _fmt_reason_text(reason),
            "shares": o.get("shares", 0),
            "executed": o.get("executed", False),
        })

    return {
        "input_n": len(sigs),
        "neutral_dropped": neutral_dropped,
        "consensus_dropped": consensus_dropped,
        "gate_dropped": gate_dropped,
        "buys": buys,
        "sells": sells,
    }


def _console_summary(funnel: dict, report_path: str = "") -> str:
    """콘솔 한 줄 요약 (Design §6.1 / Plan FR-05 / SC-05)."""
    return (
        f"입력 {funnel['input_n']} · 중립탈락 {len(funnel['neutral_dropped'])}"
        f" · 컨센탈락 {len(funnel['consensus_dropped'])}"
        f" · 게이트탈락 {len(funnel['gate_dropped'])}"
        f" · 매수 {len(funnel['buys'])} · 매도 {len(funnel['sells'])}"
        f"{' → ' + report_path if report_path else ''}"
    )


def _fmt_pct(v) -> str:
    return f"{v:.0%}" if isinstance(v, (int, float)) else "-"


def _fmt_num(v, nd=2) -> str:
    return f"{v:.{nd}f}" if isinstance(v, (int, float)) else "-"


def _status_sentence(funnel: dict) -> str:
    buy_n = len(funnel["buys"])
    sell_n = len(funnel["sells"])
    if buy_n or sell_n:
        return f"오늘은 매수 {buy_n}건, 매도 {sell_n}건의 주문 판단이 있었습니다."
    return "오늘은 매수/매도 주문이 없었습니다."


def _hold_reason_sentence(funnel: dict) -> str:
    input_n = funnel["input_n"]
    neutral_n = len(funnel["neutral_dropped"])
    consensus_n = len(funnel["consensus_dropped"])
    gate_n = len(funnel["gate_dropped"])
    candidate_n = len(funnel["buys"]) + len(funnel["sells"])
    if input_n == 0:
        return "검토할 종목이 없어 새 주문 판단을 만들지 않았습니다."
    if candidate_n == 0:
        if neutral_n and not (consensus_n or gate_n):
            return f"검토한 {input_n}개 종목 모두 여론 방향성이 충분히 뚜렷하지 않아 보류되었습니다."
        if neutral_n or consensus_n or gate_n:
            return f"검토한 {input_n}개 종목 중 매매 기준을 통과한 후보가 없었습니다."
        return f"검토한 {input_n}개 종목에서 새 주문 후보가 나오지 않았습니다."
    return f"검토한 {input_n}개 종목 중 최종 주문 후보 {candidate_n}개가 확인되었습니다."


def _observation_candidates(funnel: dict, limit: int = 5) -> list[dict]:
    """주문 후보는 아니지만 다음 실행에서 이어서 볼 종목을 추린다."""
    rows = []
    for g in funnel["gate_dropped"]:
        rows.append({
            "symbol": g["symbol"],
            "reason": f"최종 판단: {_fmt_action(g.get('final_action'))}",
            "detail": (
                _fmt_reason_codes(g.get("reason_codes"))
                if g.get("reason_codes")
                else _fmt_action(g.get("final_action"))
            ),
        })
    for c in funnel["consensus_dropped"]:
        rows.append({
            "symbol": c["symbol"],
            "reason": "매수 의견 합의 부족",
            "detail": f"상승 {c['bullish']} / 하락 {c['bearish']}",
        })
    return rows[:limit]


def _candidate_details(ctx: ReportContext, funnel: dict) -> list[dict]:
    """컨센서스를 통과해 라우터까지 도달한 후보(게이트보류·매수·매도)의 판단 근거를
    decision_records에서 추려 반환한다. 사람이 매수/보류 사유를 그대로 검토할 수 있게 함."""
    rec_by_sym = {r.get("symbol"): r for r in (ctx.decision_records or [])}
    order = ([b["symbol"] for b in funnel["buys"]]
             + [s["symbol"] for s in funnel["sells"]]
             + [g["symbol"] for g in funnel["gate_dropped"]])
    rows = []
    for sym in dict.fromkeys(order):
        r = rec_by_sym.get(sym)
        if not r:
            continue
        rows.append(r)
    return rows


def _fmt_candidate_block(r: dict) -> list[str]:
    """단일 후보 record → Markdown 블록 (헤더 + 지표줄 + 판단 근거)."""
    sym = r.get("symbol", "?")
    final = _fmt_action(r.get("final_action") or r.get("action") or "-")
    rule = r.get("rule_action")
    llm = r.get("llm_action")
    if rule and llm:
        route = f"룰 {_fmt_action(rule)} → LLM {_fmt_action(llm)}"
    elif rule:
        route = f"룰 {_fmt_action(rule)}"
    else:
        route = r.get("router_mode", "")
    metrics = (
        f"여론점수 {_fmt_num(r.get('opinion_score'), 1)} · "
        f"합의 {_fmt_num(r.get('consensus_ratio'))} · "
        f"중립 {_fmt_pct(r.get('neutral_ratio'))} · "
        f"속도 {r.get('velocity_state') or '-'} · "
        f"추세 {r.get('opinion_trend') or '-'} · "
        f"지속 {r.get('persistence_days', 0)}d"
    )
    reasoning = _fmt_reason_text(r.get("reasoning") or "").strip() or "_근거 기록 없음_"
    return [
        f"#### {sym} — 최종 판단: {final}  ({route})",
        f"- {metrics}",
        f"- 근거: {reasoning}",
        "",
    ]


# ── LLM 총평 (하이브리드: 숫자·표는 템플릿, 서술 2~4문장만 LLM) ────────────────
# 설계 원칙: LLM에는 _commentary_facts가 추린 "구조화된 숫자"만 전달하고 숫자 생성은
# 절대 맡기지 않는다(환각 차단 — 표가 사실의 단일 출처). 호출 실패/비활성 시 None을
# 반환해 총평 섹션만 생략하며, 보고서 본문(_format_markdown)은 그대로 정상 생성된다.
def _commentary_facts(ctx: ReportContext, funnel: dict) -> dict:
    """funnel/ctx → LLM에 줄 구조화 사실(숫자만). 자유서술·원문은 넣지 않는다."""
    def _slim_orders(items, keys):
        return [{k: o.get(k) for k in keys} for o in items]

    return {
        "date": ctx.date,
        "input_n": funnel["input_n"],
        "buys": _slim_orders(funnel["buys"], ("symbol", "score", "size_factor", "executed")),
        "sells": _slim_orders(funnel["sells"], ("symbol", "action", "executed")),
        "neutral_dropped_n": len(funnel["neutral_dropped"]),
        "consensus_dropped_n": len(funnel["consensus_dropped"]),
        "gate_dropped": _slim_orders(funnel["gate_dropped"][:5], ("symbol", "final_action", "reason_codes")),
        "llm_router_calls": _get(ctx.summary, "llm_calls", 0),
    }


def _format_commentary_prompt(facts: dict) -> str:
    """구조화 사실 → 한국어 총평 프롬프트. 숫자/종목은 facts에 있는 것만 쓰도록 강하게 제약."""
    return (
        "당신은 미국주 커뮤니티 여론 기반 페이퍼 트레이딩 봇의 일일 보고서 작성 보조자입니다. "
        "아래 JSON은 오늘 판단의 집계 수치입니다. 이를 바탕으로 투자자가 읽을 '오늘의 총평'을 "
        "한국어로 2~4문장 작성하세요.\n"
        "규칙:\n"
        "- JSON에 있는 숫자/종목명만 사용하고, 없는 수치·종목·예측을 새로 지어내지 마세요.\n"
        "- 매수/매도가 없으면 왜 보류가 많았는지(방향성 부족/합의 부족/위험·비용) 맥락을 짚으세요.\n"
        "- 중립적·분석적 톤. 과장된 매수 권유 금지. 마크다운 헤더 없이 본문 문장만 출력하세요.\n"
        f"JSON:\n{json.dumps(facts, ensure_ascii=False, default=str)}"
    )


def _openai_complete(prompt: str) -> str:
    """기존 OpenAI(config.GPT_MODEL) 호출 — decision_router._openai_complete와 동일 패턴.
    키/패키지 없으면 예외 → _llm_commentary가 잡아 None 폴백. 신모델 temperature 미지원 시 재시도."""
    from openai import OpenAI, BadRequestError
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    kwargs = dict(
        model=config.GPT_MODEL,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=config.COMMUNITY_REPORT_LLM_MAX_TOKENS,
        temperature=config.COMMUNITY_REPORT_LLM_TEMPERATURE,
    )
    try:
        resp = client.chat.completions.create(**kwargs)
    except BadRequestError as e:
        if "temperature" in str(e):
            kwargs.pop("temperature", None)
            resp = client.chat.completions.create(**kwargs)
        else:
            raise
    return resp.choices[0].message.content or ""


def _log_commentary(facts: dict, prompt: str, response: str, ok: bool,
                    error: str = "") -> None:
    """프롬프트+응답 영속 로깅(재현성). 실패는 무시 — 보고서 생성에 영향 없음."""
    path = getattr(config, "COMMUNITY_REPORT_LLM_LOG_FILE", "")
    if not path:
        return
    try:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "date": facts.get("date"),
            "model": config.GPT_MODEL,
            "ok": ok,
            "prompt": prompt,
            "response": response,
            "error": error,
        }
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"총평 로깅 실패(무시): {e}")


def _llm_commentary(ctx: ReportContext, funnel: dict, *, complete_fn=None) -> str | None:
    """LLM 총평 1건 생성. flag OFF면 None. 호출 실패 시에도 None(보고서는 정상).
    complete_fn(prompt)->str 주입 가능(테스트). None이면 OpenAI 호출."""
    if not getattr(config, "COMMUNITY_REPORT_LLM_COMMENTARY_ENABLED", False):
        return None
    facts = _commentary_facts(ctx, funnel)
    prompt = _format_commentary_prompt(facts)
    complete = complete_fn or _openai_complete
    try:
        text = (complete(prompt) or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"총평 LLM 호출 실패 → 총평 생략: {e}")
        _log_commentary(facts, prompt, "", ok=False, error=str(e))
        return None
    if not text:
        _log_commentary(facts, prompt, "", ok=False, error="empty_response")
        return None
    _log_commentary(facts, prompt, text, ok=True)
    return text


def _format_markdown(ctx: ReportContext, funnel: dict, commentary: str = None) -> str:
    """funnel → 한국어 Markdown 보고서 본문 (Design §6.1 / D7). 순수 함수.
    commentary가 있으면 상단에 '오늘의 총평' 섹션을 덧붙인다(LLM 생성, 숫자는 표가 담보)."""
    date = ctx.date
    neutral_n = len(funnel["neutral_dropped"])
    consensus_n = len(funnel["consensus_dropped"])
    gate_n = len(funnel["gate_dropped"])
    hold_n = neutral_n + consensus_n + gate_n
    observation = _observation_candidates(funnel)
    L = [
        f"# 오늘의 매매 판단 — {date}",
        "",
        _status_sentence(funnel),
        "",
        _hold_reason_sentence(funnel),
        "",
    ]
    if commentary:
        L += ["## 오늘의 총평", "", commentary.strip(), ""]
    L += [
        "## 요약",
        "",
        "| 항목 | 결과 |",
        "|------|------|",
        f"| 수집 게시글 출처일 | {_get(ctx.summary, 'posts_date', date)} |",
        f"| 검토 종목 | {funnel['input_n']}개 |",
        f"| 매매 후보 | {len(funnel['buys']) + len(funnel['sells'])}개 |",
        f"| 매수 | {len(funnel['buys'])}개 |",
        f"| 매도 | {len(funnel['sells'])}개 |",
        f"| 보류 | {hold_n}개 |",
        f"| LLM 판단 호출 | {_get(ctx.summary, 'llm_calls', 0)}회 |",
        "",
    ]

    L += ["## 관찰 후보", ""]
    if observation:
        L += [
            "매수 후보는 아니지만, 다음 실행에서도 이어서 볼 종목입니다.",
            "",
            "| 종목 | 관찰 이유 | 참고 |",
            "|------|----------|------|",
        ]
        for row in observation:
            L.append(f"| {row['symbol']} | {row['reason']} | {row['detail']} |")
    else:
        L.append("_관찰 후보 없음._")
    L.append("")

    # ⑤ 매수
    L += ["## 매수", ""]
    if funnel["buys"]:
        L += ["| 종목 | 여론 점수 | 합의 비율 | 비중 | 수량 | 체결 |",
              "|------|------|---------|------|--------|------|"]
        for b in funnel["buys"]:
            L.append(f"| {b['symbol']} | {_fmt_num(b['score'], 1)} | {_fmt_num(b['consensus_ratio'])}"
                     f" | {_fmt_num(b['size_factor'])} | {b['shares']} | {'✅' if b['executed'] else '❌'} |")
    else:
        L.append("_매수 주문 없음._")
    L.append("")

    # ⑥ 매도
    L += ["## 매도", ""]
    if funnel["sells"]:
        L += ["| 종목 | 판단 | 사유 | 수량 | 체결 |",
              "|------|--------|------|--------|------|"]
        for s in funnel["sells"]:
            L.append(f"| {s['symbol']} | {_fmt_action(s['action'])} | {s['reason']} | {s['shares']}"
                     f" | {'✅' if s['executed'] else '❌'} |")
    else:
        L.append("_매도 주문 없음._")
    L.append("")

    L += ["## 보류된 이유", ""]
    if hold_n:
        L += [
            "| 이유 | 종목 수 |",
            "|------|--------|",
            f"| 여론 방향성이 충분히 뚜렷하지 않음 | {neutral_n}개 |",
            f"| 매수 의견 합의 부족 | {consensus_n}개 |",
            f"| 위험/비용 기준에서 보류 | {gate_n}개 |",
            "",
        ]
    else:
        L.append("_보류된 종목 없음._")
        L.append("")

    # ④ 상세: 후보별 판단 근거 (컨센서스 통과 후 라우터까지 간 종목)
    L += ["## 상세 기록", "", "### 후보 상세 판단 (근거)", ""]
    details = _candidate_details(ctx, funnel)
    if details:
        L += ["컨센서스를 통과해 최종 판단까지 간 후보의 지표·근거입니다.", ""]
        for r in details:
            L += _fmt_candidate_block(r)
    else:
        L += ["_라우터까지 도달한 후보 없음._", ""]

    L += ["### 최종 판단: 보류/관망", ""]
    if funnel["gate_dropped"]:
        L += ["| 종목 | 최종 판단 | 참고 |", "|------|-----------|------|"]
        for g in funnel["gate_dropped"]:
            L.append(
                f"| {g['symbol']} | {_fmt_action(g['final_action'])} | "
                f"{_fmt_reason_codes(g['reason_codes'])} |"
            )
    else:
        L.append("_없음._")
    L.append("")

    # ③ 컨센서스 탈락
    L += ["### 매수 의견 합의 부족", ""]
    if funnel["consensus_dropped"]:
        L += ["| 종목 | 상승 | 하락 | 사유 |", "|------|------|------|------|"]
        for c in funnel["consensus_dropped"]:
            L.append(f"| {c['symbol']} | {c['bullish']} | {c['bearish']} | {c['reason']} |")
    else:
        L.append("_없음._")
    L.append("")

    # ② 중립필터 탈락
    L += ["### 여론 방향성 부족", ""]
    if funnel["neutral_dropped"]:
        L += ["| 종목 | 중립비율 |", "|------|----------|"]
        for n in funnel["neutral_dropped"]:
            L.append(f"| {n['symbol']} | {_fmt_pct(n['neutral_ratio'])} |")
    else:
        L.append("_없음._")
    L.append("")

    L += ["---", f"_생성: {datetime.now(timezone.utc).isoformat()}_"]
    return "\n".join(L)


def build_daily_report(ctx: ReportContext, path: str = None) -> str | None:
    """funnel 도출 → MD 생성·저장 → 경로 반환 (Design §6.1 / Plan FR-01·06 / SC-01).
    flag OFF면 no-op(None). 저장 실패는 예외 전파(호출부가 try/except로 격리 — D3/NFR-01)."""
    if not config.COMMUNITY_DECISION_REPORT_ENABLED:
        return None
    funnel = _derive_funnel(ctx)
    # 총평: LLM 호출(부수효과: OpenAI + 로깅)은 IO 경계인 여기서 수행하고,
    # 순수 포매터에는 결과 문자열만 주입한다. 비활성/실패 시 None → 섹션 생략.
    commentary = _llm_commentary(ctx, funnel)
    md = _format_markdown(ctx, funnel, commentary=commentary)
    path = path or os.path.join(config.COMMUNITY_LIVE_REPORTS_DIR, f"{ctx.date}.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    logger.info(_console_summary(funnel, path))
    return path
