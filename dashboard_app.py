# Design Ref: streamlit-dashboard-deploy §6.1 — 자립형 읽기전용 대시보드 (Option C)
# Streamlit Community Cloud 배포용. 커밋된 data만 읽어 렌더한다.
# 불가침 원칙: KIS·FinBERT·실주문·무거운 모듈(torch/transformers/community_live/backtester)
#            절대 import 금지. streamlit·pandas·altair·표준 라이브러리만.
# Plan SC: SC-01(heavy import 0), SC-05(실주문 호출 0)
import base64
import html
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent
DATA = ROOT / "data"
REPORTS = DATA / "community" / "live" / "reports"
LIVE_DECISIONS = DATA / "community" / "live" / "decisions.jsonl"
LIVE_RUN_SUMMARIES = DATA / "community" / "live" / "run_summaries.jsonl"
SNAPSHOTS = DATA / "community" / "daily_opinion_snapshots.jsonl"
PORTFOLIO = DATA / "portfolio.json"
TRADES = DATA / "trades.csv"
OHLCV_DIR = DATA / "backtest_snapshots" / "v2" / "ohlcv"   # 커밋된 가격 스냅샷(읽기전용)
LAST_SYNC = ROOT / "last_sync.json"
LOGO = ROOT / "assets" / "sentiquant-logo.jpeg"

st.set_page_config(page_title="SentiQuant Dashboard", page_icon="📈", layout="wide")

st.markdown(
    """
    <style>
    .brand-bar {
        align-items: center;
        display: flex;
        gap: 12px;
        margin: 4px 0 24px 0;
    }
    .brand-mark {
        align-items: center;
        background: #2563eb;
        border-radius: 9px;
        color: #ffffff;
        display: inline-flex;
        font-size: 1.05rem;
        font-weight: 800;
        height: 42px;
        justify-content: center;
        width: 42px;
    }
    .brand-logo {
        border-radius: 9px;
        box-shadow: 0 8px 20px rgba(37, 99, 235, 0.28);
        display: block;
        height: 42px;
        object-fit: cover !important;
        width: 42px;
    }
    .brand-name {
        color: #f8fafc;
        font-size: 2.05rem;
        font-weight: 800;
        line-height: 1.05;
    }
    .brand-subtitle {
        color: #94a3b8;
        font-size: 0.92rem;
        margin-top: 3px;
    }
    .readonly-note {
        background: rgba(59, 130, 246, 0.10);
        border-left: 3px solid #3b82f6;
        border-radius: 6px;
        color: #9ca3af;
        font-size: 0.86rem;
        line-height: 1.35;
        margin: 10px 0 18px 0;
        padding: 8px 11px;
    }
    .readonly-note strong {
        color: #bfdbfe;
        font-weight: 800;
    }
    .stock-card-panel {
        background: #171b22;
        border: 1px solid #2f3744;
        border-radius: 6px;
        cursor: pointer;
        min-height: 112px;
        padding: 12px;
        position: relative;
        transition: background 0.15s ease, border-color 0.15s ease, transform 0.15s ease;
    }
    .stock-card-link {
        color: inherit !important;
        display: block;
        text-decoration: none !important;
    }
    .stock-card-link:hover .stock-card-panel,
    .stock-card-link:focus .stock-card-panel {
        background: #1b2434;
        border-color: #3b82f6;
        transform: translateY(-1px);
    }
    .stock-card-link:focus {
        outline: none;
    }
    .stock-card-link:focus-visible .stock-card-panel {
        box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.28);
    }
    .stock-card-panel.selected {
        background: #172033;
        border-color: #2563eb;
    }
    .stock-card-symbol {
        color: #cbd5e1;
        font-size: 0.76rem;
        font-weight: 700;
        margin-bottom: 4px;
    }
    .stock-card-name {
        color: #f8fafc;
        font-size: 0.88rem;
        margin-bottom: 17px;
    }
    .stock-card-profit {
        font-size: 1.08rem;
        font-weight: 800;
    }
    .stock-card-shares {
        bottom: 12px;
        color: #94a3b8;
        font-size: 0.72rem;
        position: absolute;
        right: 12px;
    }
    .profit-pos { color: #ef4444 !important; font-weight: 800; }
    .profit-neg { color: #3b82f6 !important; font-weight: 800; }
    .profit-flat { color: #94a3b8 !important; font-weight: 800; }
    .total-summary {
        align-items: flex-end;
        display: flex;
        flex-direction: column;
        gap: 7px;
        margin-left: auto;
        text-align: right;
    }
    .total-summary-label,
    .total-summary-status,
    .sub-text {
        color: #757575;
        font-size: 0.76rem;
        line-height: 1.25;
    }
    .total-summary-value {
        font-size: 1.75rem;
        font-weight: 800;
        line-height: 1;
        white-space: nowrap;
    }
    .price-large {
        font-size: 1.95rem;
        font-weight: 800;
        line-height: 1.15;
    }
    .detail-profit-value {
        font-size: 1.55rem;
        font-weight: 800;
        line-height: 1.2;
        margin: 0.35rem 0 0;
    }
    .position-panel {
        background: #171b22;
        border: 1px solid #2f3744;
        border-radius: 8px;
        padding: 18px;
    }
    .position-panel-label {
        color: #94a3b8;
        font-size: 0.76rem;
    }
    .position-panel-value {
        color: #f8fafc;
        font-size: 1.35rem;
        font-weight: 800;
        margin-bottom: 18px;
    }
    .chart-stale-note {
        color: #f59e0b;
        font-size: 0.78rem;
        line-height: 1.4;
        margin: 4px 0 0;
    }
    .empty-state {
        background: #111820;
        border: 1px solid #2f3744;
        border-radius: 8px;
        margin: 8px 0 18px 0;
        padding: 18px;
    }
    .empty-state-kicker {
        color: #94a3b8;
        font-size: 0.76rem;
        font-weight: 800;
        margin-bottom: 6px;
        text-transform: uppercase;
    }
    .empty-state-title {
        color: #f8fafc;
        font-size: 1.3rem;
        font-weight: 800;
        line-height: 1.2;
        margin-bottom: 6px;
    }
    .empty-state-copy {
        color: #9ca3af;
        font-size: 0.9rem;
        line-height: 1.45;
        margin-bottom: 16px;
    }
    .empty-state-grid {
        display: grid;
        gap: 10px;
        grid-template-columns: repeat(3, minmax(0, 1fr));
    }
    .empty-state-item {
        border-top: 1px solid #2f3744;
        padding-top: 10px;
    }
    .empty-state-label {
        color: #94a3b8;
        font-size: 0.74rem;
        margin-bottom: 4px;
    }
    .empty-state-value {
        color: #f8fafc;
        font-size: 1.02rem;
        font-weight: 800;
        line-height: 1.25;
    }
    .empty-state-sub {
        color: #6b7280;
        font-size: 0.74rem;
        line-height: 1.35;
        margin-top: 4px;
    }
    .funnel-stat-grid {
        display: grid;
        gap: 18px;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        margin: 6px 0 18px 0;
    }
    .funnel-stat-grid.stat-cols-2 {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .funnel-stat-grid.stat-cols-4 {
        grid-template-columns: repeat(4, minmax(0, 1fr));
    }
    .funnel-stat-grid.stat-cols-5 {
        grid-template-columns: repeat(5, minmax(0, 1fr));
    }
    .funnel-stat {
        border-top: 1px solid #2f3744;
        min-width: 0;
        padding-top: 10px;
    }
    .funnel-stat-label {
        color: #cbd5e1;
        font-size: 0.86rem;
        font-weight: 800;
        line-height: 1.25;
        margin-bottom: 8px;
        white-space: nowrap;
    }
    .funnel-stat-value {
        align-items: baseline;
        color: #f8fafc;
        display: flex;
        gap: 5px;
        line-height: 1;
        min-height: 2.15rem;
        white-space: nowrap;
    }
    .funnel-stat-number {
        font-size: 2rem;
        font-weight: 800;
        letter-spacing: 0;
    }
    .funnel-stat-number.is-long {
        font-size: 1.7rem;
    }
    .funnel-stat-unit {
        color: #cbd5e1;
        font-size: 0.95rem;
        font-weight: 700;
    }
    .decision-list {
        display: flex;
        flex-direction: column;
        gap: 10px;
        margin: 8px 0 16px 0;
    }
    .decision-row {
        background: #111820;
        border: 1px solid #2f3744;
        border-radius: 8px;
        padding: 12px 14px;
    }
    .decision-row-head {
        align-items: baseline;
        display: flex;
        flex-wrap: wrap;
        gap: 8px 14px;
        margin-bottom: 8px;
    }
    .decision-symbol {
        color: #f8fafc;
        font-size: 1.05rem;
        font-weight: 800;
    }
    .decision-meta {
        color: #cbd5e1;
        font-size: 0.82rem;
        font-weight: 700;
    }
    .decision-reasons {
        color: #d1d5db;
        font-size: 0.9rem;
        line-height: 1.45;
        overflow-wrap: anywhere;
        word-break: keep-all;
    }
    @media (max-width: 760px) {
        .empty-state-grid {
            grid-template-columns: 1fr;
        }
        .funnel-stat-grid,
        .funnel-stat-grid.stat-cols-2,
        .funnel-stat-grid.stat-cols-4,
        .funnel-stat-grid.stat-cols-5 {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


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
        return datetime.fromisoformat(value).astimezone(
            timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    except ValueError:
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


def _load_ohlcv(symbol: str) -> pd.DataFrame:
    """커밋된 ohlcv 스냅샷({symbol}_*.csv) 병합. Cloud-안전(Polygon/KIS 호출 없음).
    # Design Ref: A 포팅 — app.py load_ohlcv_snapshot 읽기전용 이식."""
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


def _stat_grid(stats: list[tuple[str, object, str]], columns: int = 5) -> str:
    items = []
    for label, value, unit in stats:
        unit_html = f"<span class=\"funnel-stat-unit\">{_html(unit)}</span>" if unit else ""
        number_class = "funnel-stat-number is-long" if len(str(value)) >= 8 else "funnel-stat-number"
        items.append(
            "<div class=\"funnel-stat\">"
            f"<div class=\"funnel-stat-label\">{_html(label)}</div>"
            "<div class=\"funnel-stat-value\">"
            f"<span class=\"{number_class}\">{_html(value)}</span>"
            f"{unit_html}"
            "</div>"
            "</div>"
        )
    safe_columns = columns if columns in {2, 4, 5} else 5
    return f"<div class=\"funnel-stat-grid stat-cols-{safe_columns}\">{''.join(items)}</div>"


def _profit_class(value) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "profit-flat"
    if amount > 0:
        return "profit-pos"
    if amount < 0:
        return "profit-neg"
    return "profit-flat"


def _position_values(symbol: str, raw: dict) -> dict:
    shares = float(raw.get("shares") or raw.get("quantity") or 0)
    entry = float(raw.get("entry_price") or raw.get("avg_price") or raw.get("average_price") or 0)
    last = _latest_close(symbol)
    price = last if last is not None else entry
    value = price * shares
    profit = (price - entry) * shares if last is not None and entry else None
    profit_pct = ((price - entry) / entry * 100) if last is not None and entry else None
    return {
        "symbol": symbol,
        "shares": shares,
        "entry": entry,
        "last": last,
        "price": price,
        "value": value,
        "profit": profit,
        "profit_pct": profit_pct,
    }


def _position_table(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "종목": r["symbol"],
            "수량": f"{r['shares']:,.0f}",
            "진입가": _money(r["entry"], 2),
            "최근 종가": _money(r["last"], 2) if r["last"] is not None else "-",
            "평가액": _money(r["value"], 2),
            "손익": _signed_money(r["profit"]) if r["profit"] is not None else "-",
            "손익%": _signed_percent(r["profit_pct"]) if r["profit_pct"] is not None else "-",
        }
        for r in rows
    ])


PRICE_RANGE_DAYS = {
    "1개월": 21,
    "3개월": 63,
    "6개월": 126,
    "1년": 252,
    "전체": None,
}


def _price_range_control(key: str, default: str = "3개월") -> str:
    return st.radio(
        "기간",
        list(PRICE_RANGE_DAYS.keys()),
        index=list(PRICE_RANGE_DAYS.keys()).index(default),
        horizontal=True,
        key=key,
    )


def _price_chart(hist: pd.DataFrame, range_label: str = "3개월", enable_zoom: bool = True):
    data = hist.copy()
    data["Price"] = pd.to_numeric(data["close"], errors="coerce")
    data = data.dropna(subset=["date", "Price"]).sort_values("date")
    window = PRICE_RANGE_DAYS.get(range_label)
    visible = data.tail(min(window, len(data))) if window else data
    line = alt.Chart(visible).mark_line(color="#3b82f6").encode(
        x=alt.X("date:T", title=None),
        y=alt.Y("Price:Q", title="가격", scale=alt.Scale(zero=False)),
        tooltip=[
            alt.Tooltip("date:T", title="날짜"),
            alt.Tooltip("Price:Q", title="종가", format=",.2f"),
        ],
    )
    points = alt.Chart(visible).mark_point(filled=True, size=36, color="#3b82f6").encode(
        x="date:T",
        y="Price:Q",
        tooltip=[
            alt.Tooltip("date:T", title="날짜"),
            alt.Tooltip("Price:Q", title="종가", format=",.2f"),
        ],
    )
    chart = (line + points).properties(height=300)
    return chart.interactive() if enable_zoom else chart


def _compact_date_axis(tick_count: int = 6) -> alt.Axis:
    return alt.Axis(format="%m/%d", tickCount=tick_count, labelAngle=0, labelOverlap=True)


def _latest_decision_summary() -> dict:
    decisions = _read_jsonl(LIVE_DECISIONS)
    if not decisions:
        return {}
    latest_date = max((d.get("date") or "") for d in decisions)
    latest = [d for d in decisions if d.get("date") == latest_date]
    if not latest:
        return {}
    buy = sum(1 for d in latest if d.get("final_action") == "BUY")
    sell = sum(1 for d in latest if d.get("final_action") == "SELL")
    hold = len(latest) - buy - sell
    unique_by_symbol = {}
    for item in latest:
        symbol = item.get("symbol") or "-"
        current = unique_by_symbol.get(symbol)
        if current is None:
            unique_by_symbol[symbol] = item
            continue
        current_key = (float(current.get("opinion_score") or 0), current.get("created_at") or "")
        item_key = (float(item.get("opinion_score") or 0), item.get("created_at") or "")
        if item_key > current_key:
            unique_by_symbol[symbol] = item
    top = sorted(
        unique_by_symbol.values(),
        key=lambda d: (float(d.get("opinion_score") or 0), d.get("created_at") or ""),
        reverse=True,
    )[:3]
    symbols = [d.get("symbol", "-") for d in top]
    return {
        "date": latest_date,
        "total": len(latest),
        "unique_total": len(unique_by_symbol),
        "buy": buy,
        "sell": sell,
        "hold": hold,
        "symbols": symbols,
    }


def _latest_live_run_summary() -> dict:
    summaries = _read_jsonl(LIVE_RUN_SUMMARIES)
    if not summaries:
        return {}
    valid = [s for s in summaries if s.get("date")]
    if not valid:
        return {}
    return max(valid, key=lambda s: (s.get("date") or "", s.get("created_at") or ""))


def _run_int(summary: dict, key: str) -> int:
    try:
        return int(summary.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _missing_snapshot_message(summary: dict, date_label: str) -> str:
    reason = summary.get("no_snapshot_reason") or ""
    if reason == "no_posts":
        detail = "수집된 Reddit 입력이 없습니다."
    elif reason == "no_scored_symbols":
        detail = "입력은 있었지만 점수화된 종목이 없습니다."
    elif reason == "filtered_out_all":
        detail = "점수화 이후 랭킹/필터를 통과한 표시 후보가 없습니다."
    elif reason == "snapshot_write_failed":
        detail = "후보는 있었지만 종목별 기록 저장에 실패했습니다."
    elif reason == "partial_snapshot_write_failure":
        detail = "일부 종목 스냅샷만 저장됐습니다."
    elif reason == "no_candidate_snapshots":
        detail = "표시 가능한 후보가 없어 종목별 스냅샷이 없습니다."
    else:
        detail = "이 날짜의 종목별 표시 스냅샷은 생성되지 않았습니다."
    return f"{date_label} 실행은 완료됐지만 {detail}"


def _render_missing_snapshot_notice(summary: dict, date_label: str) -> None:
    reason = summary.get("no_snapshot_reason") or ""
    message = _missing_snapshot_message(summary, date_label)
    if reason in {"snapshot_write_failed", "partial_snapshot_write_failure"}:
        st.warning(message)
    else:
        st.info(message)

    if any(k in summary for k in ("input_symbols", "scored_symbols", "ranked_symbols", "snapshot_count")):
        st.caption(
            "진단: "
            f"입력 {_run_int(summary, 'input_symbols')}개 · "
            f"점수화 {_run_int(summary, 'scored_symbols')}개 · "
            f"랭킹 통과 {_run_int(summary, 'ranked_symbols')}개 · "
            f"스냅샷 저장 {_run_int(summary, 'snapshot_count')}개"
        )


def _render_opinion_freshness(run_date: str | None, snapshot_date: str | None) -> None:
    st.markdown(
        _stat_grid([
            ("최신 실행일", run_date or "없음", ""),
            ("최신 종목별 스냅샷", snapshot_date or "없음", ""),
        ], columns=2),
        unsafe_allow_html=True,
    )

    if run_date and snapshot_date and run_date != snapshot_date:
        st.warning(
            f"실행은 {run_date}까지 완료됐지만, 종목별 여론 스냅샷은 {snapshot_date} 기준입니다. "
            "아래 종목별 흐름은 스냅샷 기준일까지만 반영됩니다."
        )
    elif run_date and not snapshot_date:
        st.info(f"{run_date} 실행은 완료됐지만 아직 종목별 여론 스냅샷이 생성되지 않았습니다.")
    elif run_date and snapshot_date:
        st.caption(f"실행일과 종목별 스냅샷 기준일이 모두 {run_date}입니다.")


def _latest_trade_summary() -> dict:
    if not TRADES.exists():
        return {}
    try:
        df = pd.read_csv(TRADES)
    except Exception:
        return {}
    if df.empty:
        return {"total": 0}
    if "date" in df.columns:
        df = df.copy()
        df["_date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values("_date", na_position="first")
    last = df.iloc[-1]
    return {
        "total": len(df),
        "date": str(last.get("date", "-"))[:10],
        "symbol": last.get("symbol", "-"),
        "action": last.get("action", "-"),
    }


def _trade_action(value) -> str:
    text = str(value or "-").upper()
    return ACTION_KO.get(text, text)


def _trade_date(value) -> str:
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return str(value or "-")[:16]
    return (parsed + pd.Timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")


def _format_signed_pct_value(value) -> str:
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return "-"
    if pct == 0:
        return "0.00%"
    return f"{pct:+.2f}%"


def _format_signed_money_value(value) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "-"
    if amount == 0:
        return "$0.00"
    return _signed_money(amount)


def _trade_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    src = df.copy()
    if "date" in src.columns:
        src["_sort_date"] = pd.to_datetime(src["date"], errors="coerce", utc=True)
        src = src.sort_values("_sort_date", na_position="first")

    rows = []
    for _, row in src.tail(200).iloc[::-1].iterrows():
        rows.append({
            "일시": _trade_date(row.get("date")),
            "종목": row.get("symbol", "-"),
            "구분": _trade_action(row.get("action")),
            "가격": _money(row.get("price"), 2),
            "수량": f"{float(row.get('shares') or 0):,.0f}주",
            "거래 금액": _money(row.get("amount"), 2),
            "실현 손익": _format_signed_money_value(row.get("net_profit_usd")),
            "수익률": _format_signed_pct_value(row.get("net_profit_pct")),
        })
    return pd.DataFrame(rows)


# ── 한글 라벨 매핑 (대시보드 표시 전용 — 데이터는 원문 유지) ─────────────────
ACTION_KO = {"BUY": "매수", "SELL": "매도", "SKIP": "보류", "HOLD": "관망"}
TREND_KO = {"UP": "상승", "DOWN": "하락", "FLAT": "보합"}
VELOCITY_KO = {"SPIKE": "급증", "NORMAL": "보통", "FADING": "감소"}
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
    "consensus_break": "컨센서스 붕괴",
    "no_rule_signal": "룰 신호 없음",
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
    "llm_fallback_to_rule_based": "LLM 실패 — 룰 기반 대체",
    "llm_low_confidence_kept_rule": "LLM 저신뢰 — 룰 판단 유지",
    "llm_buy_overridden_by_rule_skip": "룰 우선 — LLM 매수 기각",
}


def _reasons_ko(codes) -> str:
    if not codes:
        return "-"
    return ", ".join(REASON_KO.get(c, c) for c in codes)


def _decision_cards(rows: list[dict]) -> str:
    cards = []
    for row in rows:
        cards.append(
            "<div class=\"decision-row\">"
            "<div class=\"decision-row-head\">"
            f"<span class=\"decision-symbol\">{_html(row.get('종목', '-'))}</span>"
            f"<span class=\"decision-meta\">신호: {_html(row.get('신호', '-'))}</span>"
            f"<span class=\"decision-meta\">최종 판단: {_html(row.get('최종 판단', '-'))}</span>"
            f"<span class=\"decision-meta\">여론 점수: {_html(row.get('여론 점수', '-'))}</span>"
            f"<span class=\"decision-meta\">확신도: {_html(row.get('확신도', '-'))}</span>"
            "</div>"
            f"<div class=\"decision-reasons\">{_html(row.get('판단 사유', '-'))}</div>"
            "</div>"
        )
    return f"<div class=\"decision-list\">{''.join(cards)}</div>"


def _watch_candidate_cards(rows: list[dict]) -> str:
    cards = []
    for row in rows:
        cards.append(
            "<div class=\"decision-row\">"
            "<div class=\"decision-row-head\">"
            f"<span class=\"decision-symbol\">{_html(row.get('종목', '-'))}</span>"
            f"<span class=\"decision-meta\">{_html(row.get('관찰 이유', '-'))}</span>"
            "</div>"
            f"<div class=\"decision-reasons\">{_html(row.get('참고', '-'))}</div>"
            "</div>"
        )
    return f"<div class=\"decision-list\">{''.join(cards)}</div>"


def _parse_funnel(md: str) -> dict[str, int]:
    """일일 보고서 표에서 단계별 수치 추출. 실패 시 빈 dict(원문만 표시)."""
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
        "최종 위험/비용 기준에서 보류": "게이트 차단",
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
    """보고서 원문 내용을 유지하되 대시보드 안에서는 제목 크기를 낮춘다."""
    out = []
    for line in md.splitlines():
        if line.startswith("### "):
            out.append("##### " + line[4:])
        elif line.startswith("## "):
            out.append("#### " + line[3:])
        elif line.startswith("# "):
            out.append("### " + line[2:])
        else:
            out.append(line)
    return "\n".join(out)


def _daily_no_order_message(funnel: dict) -> str:
    if not funnel:
        return ""
    if funnel.get("매수", 0) or funnel.get("매도", 0):
        return ""
    input_n = funnel.get("입력", 0)
    if input_n <= 0:
        return "이 날은 검토할 종목이 없어 새 주문 판단을 만들지 않았습니다."
    reasons = [
        ("여론 방향성이 충분히 뚜렷하지 않음", funnel.get("중립 제외", 0)),
        ("매매 합의 기준 미충족", funnel.get("컨센서스 미달", 0)),
        ("최종 위험/비용 기준에서 보류", funnel.get("게이트 차단", 0)),
    ]
    top_reason, top_count = max(reasons, key=lambda item: item[1])
    if top_count > 0:
        return (
            f"이 날은 {input_n}개 종목을 검토했지만 매수/매도 주문은 없었습니다. "
            f"가장 큰 보류 이유는 '{top_reason}'으로, {top_count}개 종목이 해당했습니다."
        )
    return f"이 날은 {input_n}개 종목을 검토했지만 새 주문 후보가 나오지 않았습니다."


# ── 헤더 + 마지막 sync 배지 (D6) ─────────────────────────────────────────────
_logo_uri = _logo_data_uri()
_logo_html = (
    f'<img class="brand-logo" src="{_logo_uri}" alt="SentiQuant logo">'
    if _logo_uri
    else '<div class="brand-mark">SQ</div>'
)
st.markdown(
    f"""
    <div class="brand-bar">
        {_logo_html}
        <div>
            <div class="brand-name">SentiQuant</div>
            <div class="brand-subtitle">Sentiment 분석 기반의 투자 지원</div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)
_sync = _read_json(LAST_SYNC, {})
if _sync.get("synced_at"):
    _server_kst = _format_kst(_sync.get("synced_at"))
    _data_kst = _format_kst(_sync.get("payload_changed_at")) or _server_kst
    _changed_flag = "변경 있음" if _sync.get("payload_changed") else "변경 없음"
    st.caption(
        f"🔄 서버 최종 동기화: **{_server_kst} (한국시간)** · "
        f"데이터 최종 변경: **{_data_kst} (한국시간)** · {_changed_flag}"
    )
else:
    st.caption("🔄 동기화 기록 없음 — 로컬 데이터 기준")
st.markdown(
    """
    <div class="readonly-note">
        <strong>읽기 전용</strong> · 실제 매매와 주문은 우분투 서버에서만 수행됩니다. KIS 모의투자 기준입니다.
    </div>
    """,
    unsafe_allow_html=True,
)

tab_pf, tab_trades, tab_funnel, tab_opinion = st.tabs(
    ["💼 포트폴리오", "📜 매매 이력", "🔎 일일 판단", "🗣️ 여론 흐름"])

# ── ① 포트폴리오 (보유 개요 + 평가) ──────────────────────────────────────────
with tab_pf:
    pf = _read_json(PORTFOLIO, {})
    if not pf:
        st.warning("포트폴리오 데이터가 아직 동기화되지 않았습니다.")
    else:
        positions = pf.get("positions", {}) or {}
        cash = float(pf.get("cash", 0) or 0)
        rows = [_position_values(s, v) for s, v in positions.items()]
        holdings_val = sum(r["value"] for r in rows)
        total_profit = sum(r["profit"] for r in rows if r["profit"] is not None)
        priced_count = sum(1 for r in rows if r["last"] is not None)
        equity = cash + holdings_val

        st.markdown(
            _stat_grid([
                ("현금(모의)", f"${cash:,.0f}", ""),
                ("보유 평가액", f"${holdings_val:,.0f}", ""),
                ("총 자산", f"${equity:,.0f}", ""),
                ("보유 종목 수", len(positions), "개"),
            ], columns=4),
            unsafe_allow_html=True,
        )

        col_nav, col_total_info = st.columns([3, 1])
        with col_nav:
            st.subheader(f"📊 보유 주식 개요 ({len(positions)}개 포지션)")
        with col_total_info:
            st.markdown(
                f"""
                <div class="total-summary">
                    <div class="total-summary-label">총 미실현 수익</div>
                    <div class="total-summary-value {_profit_class(total_profit)}">{_signed_money(total_profit)}</div>
                    <div class="total-summary-status">총 {priced_count}개 종목 가격 반영</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.caption("서버가 동기화한 종가로 평가한 읽기 전용 화면입니다. 주문은 우분투 서버에서만 처리됩니다.")

        if not rows:
            run_summary = _latest_live_run_summary()
            decision = _latest_decision_summary()
            trade = _latest_trade_summary()
            if run_summary:
                run_candidates = _run_int(run_summary, "candidate_symbols")
                if run_candidates == 0:
                    run_candidates = _run_int(run_summary, "candidates")
                run_value = f"{run_summary.get('date', '-')} · 검토 {run_candidates}개"
                run_sub = (
                    f"언급 종목 {_run_int(run_summary, 'input_symbols')} / "
                    f"점수화 {_run_int(run_summary, 'scored_symbols')} / "
                    f"상세 검토 {_run_int(run_summary, 'ranked_symbols')} · "
                    f"매수 {_run_int(run_summary, 'buys')} / 매도 {_run_int(run_summary, 'sells')}"
                )
                if run_summary.get("no_snapshot_reason") == "filtered_out_all":
                    run_sub += " · 매매 후보 없음"
            else:
                run_value = "실행 기록 없음"
                run_sub = "라이브 실행 후 표시됩니다."
            decision_symbols = ", ".join(decision.get("symbols") or []) if decision else ""
            decision_value = (
                f"{decision['unique_total']}개 종목" if decision else "관찰 기록 없음"
            )
            decision_sub = (
                f"{decision['date']} · {decision_symbols}" if decision_symbols else
                f"{decision['date']} · 기록 {decision['total']}건" if decision else
                "일일 판단 후 표시됩니다."
            )
            trade_value = (
                f"{trade.get('symbol', '-')} {trade.get('action', '-')}"
                if trade.get("total") else "거래 기록 없음"
            )
            trade_sub = (
                f"{trade.get('date', '-')} · 누적 {trade.get('total', 0)}건"
                if trade.get("total") else "아직 청산/진입 이력이 없습니다."
            )
            st.markdown(
                f"""
                <div class="empty-state">
                    <div class="empty-state-kicker">포트폴리오 상태</div>
                    <div class="empty-state-title">현재 보유 포지션 없음</div>
                    <div class="empty-state-copy">
                        현재는 보유 종목 없이 현금으로 대기 중입니다. 서버의 판단과 주문 기록은 계속 동기화됩니다.
                    </div>
                    <div class="empty-state-grid">
                        <div class="empty-state-item">
                            <div class="empty-state-label">최근 관찰 후보</div>
                            <div class="empty-state-value">{_html(decision_value)}</div>
                            <div class="empty-state-sub">{_html(decision_sub)}</div>
                        </div>
                        <div class="empty-state-item">
                            <div class="empty-state-label">최근 실행</div>
                            <div class="empty-state-value">{_html(run_value)}</div>
                            <div class="empty-state-sub">{_html(run_sub)}</div>
                        </div>
                        <div class="empty-state-item">
                            <div class="empty-state-label">최근 거래</div>
                            <div class="empty-state-value">{_html(trade_value)}</div>
                            <div class="empty-state-sub">{_html(trade_sub)}</div>
                        </div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            syms = _available_symbols()
            if syms:
                with st.expander("참고 가격 차트", expanded=False):
                    sel = st.selectbox("종목 선택", syms)
                    range_label = _price_range_control("reference_price_range")
                    hist = _load_ohlcv(sel)
                    if not hist.empty:
                        st.altair_chart(_price_chart(hist, range_label), width="stretch")
            else:
                st.info("가격 데이터가 아직 동기화되지 않았습니다.")
        else:
            symbols = [r["symbol"] for r in rows]
            query_symbol = st.query_params.get("holding")
            if isinstance(query_symbol, list):
                query_symbol = query_symbol[0] if query_symbol else None
            if query_symbol in symbols:
                st.session_state["dashboard_selected_symbol"] = query_symbol
            current = st.session_state.get("dashboard_selected_symbol")
            if current not in symbols:
                st.session_state["dashboard_selected_symbol"] = symbols[0]
                current = symbols[0]

            card_cols = st.columns(min(max(len(rows), 1), 5))
            for idx, row in enumerate(rows):
                profit_text = _signed_money(row["profit"]) if row["profit"] is not None else "가격 미조회"
                profit_cls = _profit_class(row["profit"])
                selected_cls = " selected" if row["symbol"] == current else ""
                with card_cols[idx % len(card_cols)]:
                    card_href = f"?holding={_html(row['symbol'])}"
                    st.markdown(
                        f"""
                        <a class="stock-card-link" href="{card_href}" target="_self"
                           aria-label="{_html(row['symbol'])} 포지션 보기">
                            <div class="stock-card-panel{selected_cls}">
                                <div class="stock-card-symbol">{row['symbol']}.US</div>
                                <div class="stock-card-name">{row['symbol']}</div>
                                <div class="stock-card-profit {profit_cls}">{profit_text}</div>
                                <div class="stock-card-shares">보유 {row['shares']:,.0f}주</div>
                            </div>
                        </a>
                        """,
                        unsafe_allow_html=True,
                    )

            st.divider()
            selected = next(r for r in rows if r["symbol"] == current)
            hist = _load_ohlcv(selected["symbol"])
            price_label = "최근 종가" if selected["last"] is not None else "매입 단가"
            delta_html = (
                f"<span class='{_profit_class(selected['price'] - selected['entry'])}'>"
                f"{_signed_money(selected['price'] - selected['entry'])} "
                f"({_signed_percent(selected['profit_pct'])})</span>"
                if selected["profit_pct"] is not None else
                "<span class='sub-text'>가격 미조회 · 손익 계산 대기</span>"
            )

            col_main, col_side = st.columns([2.2, 0.8])
            with col_main:
                c_title, c_price = st.columns([2, 1])
                with c_title:
                    st.markdown(f"<h1 style='margin-bottom:0;'>{selected['symbol']}</h1>", unsafe_allow_html=True)
                with c_price:
                    st.markdown(
                        f"""
                        <div style="text-align:right;">
                            <span class="sub-text">{price_label}</span><br>
                            <span class="price-large">{_money(selected['price'], 2)}</span><br>
                            {delta_html}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

                if hist.empty:
                    st.warning(f"{selected['symbol']} 가격 스냅샷 없음")
                else:
                    range_label = _price_range_control(
                        f"position_price_range_{selected['symbol']}")
                    st.altair_chart(_price_chart(hist, range_label), width="stretch")

                m_col1, m_col2, m_col3 = st.columns(3)
                with m_col1:
                    st.write("시장 가치")
                    st.subheader(_money(selected["value"], 2))
                with m_col2:
                    st.write("매입 단가")
                    st.subheader(_money(selected["entry"], 2))
                with m_col3:
                    st.write("미실현 수익")
                    profit_html = (
                        f"<div class='detail-profit-value {_profit_class(selected['profit'])}'>{_signed_money(selected['profit'])}</div>"
                        if selected["profit"] is not None else
                        "<div class='detail-profit-value profit-flat'>가격 미조회</div>"
                    )
                    st.markdown(profit_html, unsafe_allow_html=True)

                st.subheader(f"{selected['symbol']} 최근 거래")
                if TRADES.exists():
                    trades = pd.read_csv(TRADES)
                    if "symbol" in trades.columns:
                        st.dataframe(trades[trades["symbol"] == selected["symbol"]].tail(5),
                                     width="stretch", hide_index=True)
                    else:
                        st.caption("거래 기록 형식을 확인할 수 없습니다.")
                else:
                    st.caption("거래 기록이 아직 동기화되지 않았습니다.")

            with col_side:
                profit_rate = _signed_percent(selected["profit_pct"]) if selected["profit_pct"] is not None else "가격 미조회"
                profit_money = _signed_money(selected["profit"]) if selected["profit"] is not None else "가격 미조회"
                st.markdown(
                    f"""
                    <div class="position-panel">
                        <div class="position-panel-label">보유 수량</div>
                        <div class="position-panel-value">{selected['shares']:,.0f} 주</div>
                        <div class="position-panel-label">시장 가치</div>
                        <div class="position-panel-value">{_money(selected['value'], 2)}</div>
                        <div class="position-panel-label">수익률</div>
                        <div class="position-panel-value {_profit_class(selected['profit_pct'])}">{profit_rate}</div>
                        <div class="position-panel-label">미실현 수익</div>
                        <div class="position-panel-value {_profit_class(selected['profit'])}">{profit_money}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            st.subheader("📋 보유 종목 표")
            st.dataframe(_position_table(rows), width="stretch", hide_index=True)

# ── ② 매매 이력 ──────────────────────────────────────────────────────────────
with tab_trades:
    if not TRADES.exists():
        st.warning("매매 이력이 아직 동기화되지 않았습니다.")
    else:
        df = pd.read_csv(TRADES)
        if df.empty:
            st.info("아직 기록된 매매 이력이 없습니다.")
        else:
            buy_count = int((df.get("action", pd.Series(dtype=str)).astype(str).str.upper() == "BUY").sum())
            sell_count = int((df.get("action", pd.Series(dtype=str)).astype(str).str.upper() == "SELL").sum())
            realized = float(pd.to_numeric(df.get("net_profit_usd", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
            closed = pd.to_numeric(df.get("net_profit_pct", pd.Series(dtype=float)), errors="coerce")
            closed = closed[closed.notna() & (closed != 0)]
            win_rate = f"{(closed > 0).mean() * 100:.0f}%" if len(closed) else "-"

            st.markdown(
                _stat_grid([
                    ("총 거래", len(df), "건"),
                    ("매수", buy_count, "건"),
                    ("매도", sell_count, "건"),
                    ("실현 손익", _format_signed_money_value(realized), ""),
                    ("수익 거래 비율", win_rate, ""),
                ]),
                unsafe_allow_html=True,
            )
            st.caption("최근 거래부터 표시합니다. 시간은 한국시간 기준이며, 수익 거래 비율은 실현 손익이 있는 거래 기준입니다.")
            st.dataframe(_trade_table(df), width="stretch", hide_index=True)

# ── ③ 일일 결정 ──────────────────────────────────────────────────────────────
with tab_funnel:
    md_files = sorted(REPORTS.glob("*.md"), reverse=True) if REPORTS.exists() else []
    decisions = _read_jsonl(LIVE_DECISIONS)
    if not md_files and not decisions:
        st.warning("아직 매매 판단 기록이 없습니다 (라이브 구동 전이거나 미동기화).")
    else:
        pick = st.selectbox("날짜 선택", [p.stem for p in md_files]) if md_files else None
        md = (REPORTS / f"{pick}.md").read_text(encoding="utf-8") if pick else ""

        funnel = _parse_funnel(md)
        if funnel:
            # 단계별 핵심 지표
            st.markdown(
                _stat_grid([
                    ("분석 종목", funnel.get("입력", 0), "개"),
                    ("방향 약함", funnel.get("중립 제외", 0), "개 제외"),
                    ("합의 부족", funnel.get("컨센서스 미달", 0), "개 미달"),
                    ("안전장치 보류", funnel.get("게이트 차단", 0), "개 보류"),
                    ("매수 / 매도", f"{funnel.get('매수', 0)} / {funnel.get('매도', 0)}", ""),
                ]),
                unsafe_allow_html=True,
            )

            # 단계별 생존 종목 수 — 어디서 걸러졌는지 한눈에
            survive = [funnel.get("입력", 0)]
            for k in ("중립 제외", "컨센서스 미달", "게이트 차단"):
                survive.append(max(survive[-1] - funnel.get(k, 0), 0))
            fdf = pd.DataFrame({
                "단계": ["① 언급 종목", "② 방향성 확인", "③ 매매 합의 확인", "④ 안전장치 확인"],
                "종목 수": survive,
            })
            st.altair_chart(
                alt.Chart(fdf).mark_bar(cornerRadius=4).encode(
                    x=alt.X("종목 수:Q", title="남은 종목 수"),
                    y=alt.Y("단계:N", sort=None, title=None),
                    color=alt.Color("단계:N", legend=None, scale=alt.Scale(scheme="blues", reverse=True)),
                    tooltip=["단계", "종목 수"],
                ).properties(height=170),
                width="stretch")
            st.caption("커뮤니티에서 언급된 종목은 위 조건을 모두 통과해야 주문 후보가 됩니다.")
            no_order_message = _daily_no_order_message(funnel)
            if no_order_message:
                st.info(no_order_message)

        watch_rows = _parse_observation_candidates(md)
        if watch_rows:
            st.subheader("관찰 후보")
            st.caption("매수 후보는 아니지만 여론 흐름을 이어서 볼 종목입니다.")
            st.markdown(_watch_candidate_cards(watch_rows), unsafe_allow_html=True)

        # 당일 종목별 판단 내역
        day_recs = [d for d in decisions if d.get("date") == pick] if pick else decisions[-20:]
        if day_recs:
            st.subheader("종목별 최종 판단")
            rows = []
            for d in day_recs:
                tool = d.get("tool_interpretation") or {}
                rows.append({
                    "종목": d.get("symbol", "-"),
                    "신호": ACTION_KO.get(d.get("current_signal"), d.get("current_signal", "-")),
                    "최종 판단": ACTION_KO.get(d.get("final_action"), d.get("final_action", "-")),
                    "판단 사유": _reasons_ko(d.get("reason_codes")),
                    "여론 점수": (tool.get("opinion_signal") or "").replace("score ", "") or "-",
                    "확신도": f"{d.get('confidence', 0) * 100:.0f}%" if d.get("confidence") is not None else "-",
                })
            st.markdown(_decision_cards(rows), unsafe_allow_html=True)
        elif pick:
            st.caption("이 날은 종목별 최종 판단 표에 표시할 종목이 없습니다.")

        if md:
            with st.expander("📄 상세 보고서 원문"):
                st.markdown(_compact_report_markdown(md))

# ── ④ 여론 추세 ──────────────────────────────────────────────────────────────
with tab_opinion:
    snaps = _read_jsonl(SNAPSHOTS)
    run_summary = _latest_live_run_summary()
    run_date = run_summary.get("date")
    df = pd.DataFrame(snaps) if snaps else pd.DataFrame()
    snapshot_latest = df["date"].max() if not df.empty and "date" in df else None
    _render_opinion_freshness(run_date, snapshot_latest)

    if not snaps:
        if run_date:
            _render_missing_snapshot_notice(run_summary, run_date)
            st.markdown(
                _stat_grid([
                    ("신규 표시 종목", 0, "개"),
                    ("서버 판단 종목", int(run_summary.get("candidates") or 0), "개"),
                    ("매수 / 매도", f"{int(run_summary.get('buys') or 0)} / {int(run_summary.get('sells') or 0)}", ""),
                    ("종목별 스냅샷", "미생성", ""),
                ], columns=4),
                unsafe_allow_html=True,
            )
        else:
            st.warning("여론 스냅샷 데이터가 없습니다 (미동기화).")
    else:
        latest = max([d for d in [snapshot_latest, run_date] if d], default=None)
        today = df[df["date"] == latest].copy() if latest else pd.DataFrame()

        if not today.empty:
            # 최신일 요약
            st.markdown(
                _stat_grid([
                    ("분석 종목", len(today), "개"),
                    ("매수 합의 후보", int(today["is_consensus_buy"].fillna(False).astype(bool).sum()), "개"),
                    ("평균 여론 점수", f"{today['opinion_score'].mean():.1f}", "점"),
                    ("총 언급 수", f"{int(today['total_mentions'].sum()):,}", "건"),
                ], columns=4),
                unsafe_allow_html=True,
            )
            st.caption(f"{latest} 기준 — 점수는 0~100 (50 중립, 높을수록 매수 여론 우세)")

            # 최신 여론 상위 종목
            st.subheader(f"🔥 최신 여론 상위 종목 ({latest})")
            top = today.sort_values("opinion_score", ascending=False).head(10).copy()
            top["여론 방향"] = top["opinion_trend"].map(TREND_KO).fillna("보합")
            st.altair_chart(
                alt.Chart(top).mark_bar(cornerRadius=4).encode(
                    x=alt.X("opinion_score:Q", title="여론 점수", scale=alt.Scale(domain=[0, 100])),
                    y=alt.Y("symbol:N", sort="-x", title=None),
                    color=alt.Color("여론 방향:N", title="추세",
                                    scale=alt.Scale(domain=["상승", "보합", "하락"],
                                                    range=["#e4584c", "#9aa0a6", "#4c7be4"])),
                    tooltip=[alt.Tooltip("symbol", title="종목"),
                             alt.Tooltip("opinion_score", title="여론 점수", format=".1f"),
                             alt.Tooltip("total_mentions", title="언급 수"),
                             alt.Tooltip("여론 방향", title="추세")],
                ).properties(height=300),
                width="stretch")

            rows = [{
                "종목": r["symbol"],
                "여론 점수": round(r.get("opinion_score") or 0, 1),
                "추세": TREND_KO.get(r.get("opinion_trend"), "보합")
                        + (f" {int(r.get('persistence_days') or 0)}일째" if r.get("persistence_days") else ""),
                "언급": int(r.get("total_mentions") or 0),
                "긍정/부정": f"{r.get('bullish_count', 0)} / {r.get('bearish_count', 0)}",
                "언급량 변화": VELOCITY_KO.get(r.get("velocity_state"), "보통"),
                "주요 키워드": ", ".join((r.get("top_keywords") or [])[:4]) or "-",
            } for _, r in top.iterrows()]
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        elif latest:
            _render_missing_snapshot_notice(run_summary, latest)
            st.markdown(
                _stat_grid([
                    ("신규 표시 종목", 0, "개"),
                    ("서버 판단 종목", int(run_summary.get("candidates") or 0), "개"),
                    ("매수 / 매도", f"{int(run_summary.get('buys') or 0)} / {int(run_summary.get('sells') or 0)}", ""),
                    ("종목별 스냅샷", "미생성", ""),
                ], columns=4),
                unsafe_allow_html=True,
            )

        # 종목별 추이 — 최근 스냅샷 기준 언급 많은 순으로 선택
        st.subheader("📊 종목별 여론 흐름")
        if snapshot_latest:
            st.caption(f"종목별 흐름은 생성된 스냅샷 기준입니다. 최신 기준일: {snapshot_latest}")
        recent_syms = (df[df["date"] >= sorted(df["date"].unique())[-7:][0]]
                       .groupby("symbol")["total_mentions"].sum()
                       .sort_values(ascending=False).index.tolist()) if "date" in df else []
        if recent_syms:
            sym = st.selectbox("종목 선택 (최근 생성된 스냅샷 기준 언급 많은 순)", recent_syms)
            sdf = df[df["symbol"] == sym].sort_values("date").tail(40)
            score_line = alt.Chart(sdf).mark_line(point=True, color="#e4584c").encode(
                x=alt.X("date:T", title="날짜", axis=_compact_date_axis()),
                y=alt.Y("opinion_score:Q", title="여론 점수", scale=alt.Scale(domain=[0, 100])),
                tooltip=[alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"),
                         alt.Tooltip("opinion_score:Q", title="여론 점수", format=".1f")])
            base50 = alt.Chart(pd.DataFrame({"y": [50]})).mark_rule(
                strokeDash=[4, 4], color="#9aa0a6").encode(y="y:Q")
            st.altair_chart((score_line + base50).properties(height=240), width="stretch")
            st.altair_chart(
                alt.Chart(sdf).mark_bar(color="#6b9bd1").encode(
                    x=alt.X("date:T", title="날짜", axis=_compact_date_axis()),
                    y=alt.Y("total_mentions:Q", title="언급 수"),
                    tooltip=[alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"),
                             alt.Tooltip("total_mentions:Q", title="언급 수")],
                ).properties(height=140),
                width="stretch")

        # 시장 전체 분위기 추이
        st.subheader("🌡️ 전체 여론 흐름")
        g = df.groupby("date").agg(
            매수합의=("is_consensus_buy", lambda s: int(pd.Series(s).fillna(False).astype(bool).sum())),
            평균점수=("opinion_score", "mean"),
            종목수=("symbol", "count"),
        ).reset_index().tail(40)
        col_a, col_b = st.columns(2)
        with col_a:
            st.altair_chart(
                alt.Chart(g).mark_bar(color="#6b9bd1").encode(
                    x=alt.X("date:T", title="날짜", axis=_compact_date_axis(5)),
                    y=alt.Y("매수합의:Q", title="매수 합의 종목 수"),
                    tooltip=[alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"), "매수합의", "종목수"],
                ).properties(height=220, title="일자별 매수 합의 종목 수"),
                width="stretch")
        with col_b:
            st.altair_chart(
                alt.Chart(g).mark_line(point=True, color="#e4584c").encode(
                    x=alt.X("date:T", title="날짜", axis=_compact_date_axis(5)),
                    y=alt.Y("평균점수:Q", title="평균 여론 점수"),
                    tooltip=[alt.Tooltip("date:T", title="날짜", format="%Y-%m-%d"),
                             alt.Tooltip("평균점수:Q", format=".1f")],
                ).properties(height=220, title="일자별 평균 여론 점수"),
                width="stretch")
        st.caption(f"누적 여론 스냅샷 {len(df):,}건 · 최근 40일 표시")
