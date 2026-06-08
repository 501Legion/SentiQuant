# Design Ref: daily-decision-report §2 — Option C: ReportContext + 순수 Markdown 포매터
# run_live 종료 시 funnel(입력→중립→컨센서스→게이트→매수/매도)을 사람이 읽는 MD로 집계.
# read-only: 판단을 재계산하지 않고 기존 결과만 포맷한다(NFR-03).
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import config

logger = logging.getLogger(__name__)

# 매수/매도로 분류되는 액션 집합
_SELL_ACTIONS = {"SELL", "EXIT", "REDUCE"}


def _get(obj, key, default=None):
    """dict/객체 공용 안전 getter (decision_log._get와 동일 패턴)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


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
                "reason": f"bull {d.get('bullish', 0)}/bear {d.get('bearish', 0)} < 컨센서스 기준",
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
            "reason": reason,
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


def _format_markdown(ctx: ReportContext, funnel: dict) -> str:
    """funnel → 한국어 Markdown 보고서 본문 (Design §6.1 / D7). 순수 함수."""
    date = ctx.date
    L = [
        f"# 일일 매매 결정 보고서 — {date}",
        "",
        "> run_live 판단 funnel: 입력 → 중립필터 → 컨센서스 → 게이트 → 매수/매도. read-only 집계.",
        "",
        "## 요약 (Funnel)",
        "",
        "| 단계 | 통과/처리 | 탈락 |",
        "|------|----------|------|",
        f"| ① 입력 | {funnel['input_n']} 종목 | — |",
        f"| ② 중립필터 | — | {len(funnel['neutral_dropped'])} |",
        f"| ③ 컨센서스 | — | {len(funnel['consensus_dropped'])} |",
        f"| ④ 게이트(universe/cost/router) | — | {len(funnel['gate_dropped'])} |",
        f"| ⑤ 최종 매수 | {len(funnel['buys'])} | — |",
        f"| ⑥ 최종 매도 | {len(funnel['sells'])} | — |",
        "",
    ]

    # ⑤ 매수
    L += ["## 🟢 매수", ""]
    if funnel["buys"]:
        L += ["| 종목 | score | 합의비율 | size | shares | 체결 |",
              "|------|------|---------|------|--------|------|"]
        for b in funnel["buys"]:
            L.append(f"| {b['symbol']} | {_fmt_num(b['score'], 1)} | {_fmt_num(b['consensus_ratio'])}"
                     f" | {_fmt_num(b['size_factor'])} | {b['shares']} | {'✅' if b['executed'] else '❌'} |")
    else:
        L.append("_매수 없음._")
    L.append("")

    # ⑥ 매도
    L += ["## 🔴 매도", ""]
    if funnel["sells"]:
        L += ["| 종목 | action | 사유 | shares | 체결 |",
              "|------|--------|------|--------|------|"]
        for s in funnel["sells"]:
            L.append(f"| {s['symbol']} | {s['action']} | {s['reason']} | {s['shares']}"
                     f" | {'✅' if s['executed'] else '❌'} |")
    else:
        L.append("_매도 없음._")
    L.append("")

    # ④ 게이트 탈락
    L += ["## ⚠️ 게이트 탈락 (컨센서스는 통과했으나 미매수)", ""]
    if funnel["gate_dropped"]:
        L += ["| 종목 | 최종 action | reason_codes |", "|------|-----------|--------------|"]
        for g in funnel["gate_dropped"]:
            L.append(f"| {g['symbol']} | {g['final_action']} | {', '.join(g['reason_codes']) or '-'} |")
    else:
        L.append("_없음._")
    L.append("")

    # ③ 컨센서스 탈락
    L += ["## 컨센서스 탈락", ""]
    if funnel["consensus_dropped"]:
        L += ["| 종목 | 상승 | 하락 | 사유 |", "|------|------|------|------|"]
        for c in funnel["consensus_dropped"]:
            L.append(f"| {c['symbol']} | {c['bullish']} | {c['bearish']} | {c['reason']} |")
    else:
        L.append("_없음._")
    L.append("")

    # ② 중립필터 탈락
    L += ["## 중립필터 탈락", ""]
    if funnel["neutral_dropped"]:
        L += ["| 종목 | 중립비율 |", "|------|----------|"]
        for n in funnel["neutral_dropped"]:
            L.append(f"| {n['symbol']} | {_fmt_pct(n['neutral_ratio'])} |")
    else:
        L.append("_없음._")
    L.append("")

    L += ["---", f"_생성: {datetime.now(timezone.utc).isoformat()} · summary: {ctx.summary}_"]
    return "\n".join(L)


def build_daily_report(ctx: ReportContext, path: str = None) -> str | None:
    """funnel 도출 → MD 생성·저장 → 경로 반환 (Design §6.1 / Plan FR-01·06 / SC-01).
    flag OFF면 no-op(None). 저장 실패는 예외 전파(호출부가 try/except로 격리 — D3/NFR-01)."""
    if not config.COMMUNITY_DECISION_REPORT_ENABLED:
        return None
    funnel = _derive_funnel(ctx)
    md = _format_markdown(ctx, funnel)
    path = path or os.path.join(config.COMMUNITY_LIVE_REPORTS_DIR, f"{ctx.date}.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    logger.info(_console_summary(funnel, path))
    return path
