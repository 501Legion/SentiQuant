"""Shared helpers for the read-only Streamlit dashboard.

This module intentionally stays light: standard library + pandas only.
It is copied to the Streamlit Cloud `dashboard-data` branch together with
`dashboard_app.py`, so do not import KIS, collector, backtester, or model code.
"""
from __future__ import annotations

import base64
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent
DATA = ROOT / "data"
REPORTS = DATA / "community" / "live" / "reports"
LIVE_DECISIONS = DATA / "community" / "live" / "decisions.jsonl"
LIVE_RUN_SUMMARIES = DATA / "community" / "live" / "run_summaries.jsonl"
SNAPSHOTS = DATA / "community" / "daily_opinion_snapshots.jsonl"
PORTFOLIO = DATA / "portfolio.json"
TRADES = DATA / "trades.csv"
OHLCV_DIR = DATA / "backtest_snapshots" / "v2" / "ohlcv"
LAST_SYNC = ROOT / "last_sync.json"
LOGO = ROOT / "assets" / "sentiquant-logo.jpeg"


def _logo_data_uri() -> str | None:
    try:
        encoded = base64.b64encode(LOGO.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:image/jpeg;base64,{encoded}"


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _format_kst(value: str | None) -> str | None:
    if not value:
        return None
    try:
        raw = value.replace("Z", "+00:00") if value.endswith("Z") else value
        parsed = datetime.fromisoformat(raw)
        kst = timezone(timedelta(hours=9))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=kst)
        return parsed.astimezone(kst).strftime("%Y-%m-%d %H:%M")
    except (AttributeError, ValueError):
        return value


def _read_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _normalize_opinion_snapshots(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    required = {"date", "symbol"}
    if not required.issubset(df.columns):
        return df
    df = df.copy()
    df["_row_order"] = range(len(df))
    df["date"] = df["date"].astype(str)
    df["symbol"] = df["symbol"].astype(str).str.strip()
    for col in [
        "opinion_score",
        "total_mentions",
        "bullish_count",
        "bearish_count",
        "persistence_days",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    sort_cols = ["date", "symbol", "_row_order"]
    if "created_at" in df.columns:
        sort_cols = ["date", "symbol", "created_at", "_row_order"]
    df = (
        df.dropna(subset=["date", "symbol"])
        .sort_values(sort_cols)
        .drop_duplicates(["date", "symbol"], keep="last")
        .drop(columns=["_row_order"])
        .reset_index(drop=True)
    )
    return df


def _load_ohlcv(symbol: str) -> pd.DataFrame:
    """Merge committed OHLCV snapshots. No Polygon/KIS calls on Cloud."""
    if not OHLCV_DIR.exists():
        return pd.DataFrame()
    frames = []
    for p in sorted(OHLCV_DIR.glob(f"{symbol}_*.csv")):
        try:
            df = pd.read_csv(p)
        except Exception:
            continue
        if {"date", "close"}.issubset(df.columns):
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    c = pd.concat(frames, ignore_index=True)
    c["date"] = pd.to_datetime(c["date"], errors="coerce")
    c = c.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last")
    return c.reset_index(drop=True)


def _available_symbols() -> list[str]:
    if not OHLCV_DIR.exists():
        return []
    return sorted({p.name.split("_")[0] for p in OHLCV_DIR.glob("*.csv")})


def _latest_close(symbol: str) -> float | None:
    df = _load_ohlcv(symbol)
    if df.empty:
        return None
    return float(df["close"].iloc[-1])


def _money(value, digits: int = 0) -> str:
    try:
        return f"${float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "-"


def _signed_money(value) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "-"
    sign = "+" if amount >= 0 else "-"
    return f"{sign}${abs(amount):,.2f}"


def _signed_percent(value) -> str:
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{pct:+.2f}%"


def _html(value) -> str:
    return html.escape(str(value), quote=True)


# Korean label mapping for dashboard display. Raw data remains unchanged.
ACTION_KO = {
    "BUY": "매수",
    "STRONG_BUY": "강한 매수",
    "SELL": "매도",
    "STRONG_SELL": "강한 매도",
    "NEUTRAL": "중립",
    "SKIP": "보류",
    "HOLD": "관망",
}
TREND_KO = {"UP": "상승", "DOWN": "하락", "FLAT": "보합"}
VELOCITY_KO = {
    "SPIKE": "급증",
    "NEW_SPIKE": "신규 급증",
    "NORMAL": "보통",
    "HIGH_MOMENTUM": "강한 모멘텀",
    "DECLINING": "약화",
    "FADING": "감소",
}
SOURCE_KO = {
    "reddit_agent": "여론 에이전트",
    "community_agent": "커뮤니티 여론",
}
REASON_KO = {
    "universe_blocked": "유동성 유니버스 미포함",
    "safety_universe_blocked": "안전장치 — 유니버스 차단",
    "cost_blocked": "거래비용 대비 기대수익 부족",
    "safety_cost_blocked": "안전장치 — 비용 차단",
    "insufficient_cash": "현금 부족",
    "low_opinion_score": "여론 점수 미달",
    "weak_consensus": "매매 합의 부족",
    "high_noise": "중립(노이즈) 비율 과다",
    "neutral_spike": "중립 의견 급증",
    "consensus_break": "매수 의견 약화",
    "no_rule_signal": "매매 신호 없음",
    "bullish_trend": "상승 추세",
    "high_momentum": "강한 모멘텀",
    "trend_up_with_moderate_momentum": "완만한 상승 모멘텀",
    "community_hype_detected": "커뮤니티 과열 감지",
    "possible_pump_risk": "펌프 위험 가능성",
    "rsi_elevated": "RSI 과열권",
    "rsi_neutral_to_slightly_weak": "RSI 중립~소폭 약세",
    "core_universe_allowed": "핵심 유니버스 통과",
    "bullish_aggregate_but_mixed_social_sentiment": "종합 여론은 긍정이나 반응 혼재",
    "sarcasm_and_price-prediction_noise": "풍자/가격예측성 잡음",
    "approve_candidate_but_downsize": "후보 승인, 비중 축소",
    "history_downsize": "과거 유사 사례 부진 — 비중 축소",
    "low_persistence_downsize": "신호 지속일 부족 — 비중 축소",
    "new_spike_downsize": "신규 급등 종목 — 비중 축소",
    "llm_assisted": "LLM 보조 판단",
    "llm_fallback_to_rule_based": "LLM 실패 — 자동 기준으로 대체",
    "llm_low_confidence_kept_rule": "LLM 저신뢰 — 자동 기준 유지",
    "llm_buy_overridden_by_rule_skip": "자동 기준 우선 — LLM 매수 기각",
    "buy_approved": "매수 기준 통과",
    "strong_consensus_upsize": "강한 매수 합의 — 비중 확대",
}


def _code_key(value) -> str:
    text = str(value or "").strip()
    return re.sub(r"[\s\-]+", "_", text).lower()


def _translate_code(value) -> str:
    raw = str(value or "").strip()
    if not raw or raw.lower() in {"nan", "none", "null"}:
        return "-"
    upper = re.sub(r"[\s\-]+", "_", raw).upper()
    key = _code_key(raw)
    if upper in ACTION_KO:
        return ACTION_KO[upper]
    if key in REASON_KO:
        return REASON_KO[key]
    if key in SOURCE_KO:
        return SOURCE_KO[key]
    if upper in TREND_KO:
        return TREND_KO[upper]
    if upper in VELOCITY_KO:
        return VELOCITY_KO[upper]
    return raw


def _reasons_ko(codes) -> str:
    if not codes:
        return "-"
    if isinstance(codes, str):
        parts = [part.strip() for part in re.split(r"[,;]", codes) if part.strip()]
    else:
        parts = [str(part).strip() for part in codes if str(part).strip()]
    if not parts:
        return "-"
    return ", ".join(_translate_code(part) for part in parts)


def _parse_funnel(md: str) -> dict[str, int]:
    """Extract daily report funnel counts. Return empty dict on parse failure."""
    keys = {"①": "입력", "②": "중립 제외", "③": "컨센서스 미달",
            "④": "게이트 차단", "⑤": "매수", "⑥": "매도"}
    user_keys = {
        "검토 종목": "입력",
        "매매 후보": "후보",
        "매수": "매수",
        "매도": "매도",
        "보류": "보류",
        "여론 방향성이 충분히 뚜렷하지 않음": "중립 제외",
        "매매 합의 기준 미충족": "컨센서스 미달",
        "매수 의견 합의 부족": "컨센서스 미달",
        "최종 위험/비용 기준에서 보류": "게이트 차단",
        "위험/비용 기준에서 보류": "게이트 차단",
    }
    out: dict[str, int] = {}
    for line in md.splitlines():
        stripped = line.strip()
        for mark, name in keys.items():
            if stripped.startswith(f"| {mark}"):
                nums = re.findall(r"\d+", line)
                if nums:
                    out[name] = int(nums[0])
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        name = user_keys.get(cells[0])
        if not name:
            continue
        nums = re.findall(r"\d+", cells[1])
        if nums:
            out[name] = int(nums[0])
    if "후보" in out:
        out.setdefault("매수", 0)
        out.setdefault("매도", 0)
        out.setdefault("중립 제외", 0)
        out.setdefault("컨센서스 미달", 0)
        out.setdefault("게이트 차단", 0)
    return out if "입력" in out else {}


def _parse_observation_candidates(md: str) -> list[dict]:
    rows: list[dict] = []
    in_section = False
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped == "## 관찰 후보"
            continue
        if not in_section or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3 or cells[0] in {"종목", "------"}:
            continue
        if set(cells[0]) == {"-"}:
            continue
        rows.append({"종목": cells[0], "관찰 이유": cells[1], "참고": cells[2]})
    return rows


def _compact_report_markdown(md: str) -> str:
    """Keep report content but polish headings and terminology in the dashboard."""
    out = []
    for line in md.splitlines():
        line = line.replace("최종 위험/비용 기준에서 보류", "위험/비용 기준에서 보류")
        line = line.replace("매매 합의 기준 미충족", "매수 의견 합의 부족")
        line = line.replace("컨센서스를 통과해 최종 판단까지 간 후보의 지표·근거입니다.", "자동 기준으로 상세 검토까지 진행된 후보의 지표와 근거입니다.")
        line = line.replace("컨센서스 붕괴", "매수 의견 약화")
        line = line.replace("합의 붕괴", "매수 의견 약화")
        line = line.replace("size_factor", "비중")
        line = line.replace("weak_consensus", "매수 의견 합의 부족")
        line = line.replace("WEAK CONSENSUS", "매수 의견 합의 부족")
        line = line.replace("buy_approved", "매수 기준 통과")
        line = line.replace("strong_consensus_upsize", "강한 매수 합의 — 비중 확대")
        line = line.replace("참고 코드", "참고")
        line = line.replace("최종 기준에서 보류", "최종 판단: 보류/관망")
        line = line.replace("룰 매수", "자동 기준: 매수")
        line = line.replace("룰 매도", "자동 기준: 매도")
        line = line.replace("룰 관망", "자동 기준: 관망")
        line = line.replace("룰 보류", "자동 기준: 보류")
        line = line.replace("| 수량 | 체결 |", "| 수량 | 주문 상태 |")
        line = line.replace("| ✅ |", "| 체결 |")
        line = line.replace("| ❌ |", "| 미확정 |")
        line = re.sub(r"속도\s+HIGH_MOMENTUM", "속도 강한 모멘텀", line)
        line = re.sub(r"속도\s+NEW_SPIKE", "속도 신규 급증", line)
        line = re.sub(r"속도\s+DECLINING", "속도 약화", line)
        line = re.sub(r"속도\s+NORMAL", "속도 보통", line)
        line = re.sub(r"추세\s+UP", "추세 상승", line)
        line = re.sub(r"추세\s+DOWN", "추세 하락", line)
        line = re.sub(r"추세\s+FLAT", "추세 보합", line)
        line = re.sub(r"지속\s+(\d+)d", r"지속 \1일", line)
        line = re.sub(
            r"([A-Z.]+) 매수: consensus ([0-9.]+), score ([0-9.]+), persist (\d+)d, size ([0-9.]+)",
            r"\1 매수: 합의 \2, 여론 점수 \3, 지속 \4일, 비중 \5",
            line,
        )
        line = re.sub(
            r"([A-Z.]+) SELL: 매수 의견 약화 consensus ([0-9.]+)",
            r"\1 매도: 매수 의견 약화(합의 \2)",
            line,
        )
        line = re.sub(r"\bscore\s+([0-9.]+)", r"여론 점수 \1", line)
        line = re.sub(r"\bSTRONG_BUY\b", "강한 매수", line)
        line = re.sub(r"\bBUY\b", "매수", line)
        line = re.sub(r"\bNEUTRAL\b", "중립", line)
        line = re.sub(r"\bSTRONG_SELL\b", "강한 매도", line)
        line = re.sub(r"\bSELL\b", "매도", line)
        line = re.sub(r"\bSKIP\b", "보류", line)
        line = re.sub(r"\bHOLD\b", "관망", line)
        line = re.sub(
            r"bull (\d+)/bear (\d+) < 컨센서스 기준",
            r"상승 \1 / 하락 \2로 매수 우세 기준 미달",
            line,
        )
        line = re.sub(r"bull (\d+)/bear (\d+)", r"상승 \1 / 하락 \2", line)
        if line.startswith("### "):
            out.append("##### " + line[4:])
        elif line.startswith("## "):
            out.append("#### " + line[3:])
        elif line.startswith("# "):
            out.append("### " + line[2:])
        else:
            out.append(line)
    return "\n".join(out)
