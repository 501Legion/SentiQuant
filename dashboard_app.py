# Design Ref: streamlit-dashboard-deploy §6.1 — 자립형 읽기전용 대시보드 (Option C)
# Streamlit Community Cloud 배포용. 커밋된 data만 읽어 렌더한다.
# 불가침 원칙: KIS·FinBERT·실주문·무거운 모듈(torch/transformers/community_live/backtester)
#            절대 import 금지. streamlit·pandas·altair·표준 라이브러리만.
# Plan SC: SC-01(heavy import 0), SC-05(실주문 호출 0)
import json
from datetime import datetime, timezone
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).parent
DATA = ROOT / "data"
REPORTS = DATA / "community" / "live" / "reports"
LIVE_DECISIONS = DATA / "community" / "live" / "decisions.jsonl"
SNAPSHOTS = DATA / "community" / "daily_opinion_snapshots.jsonl"
PORTFOLIO = DATA / "portfolio.json"
TRADES = DATA / "trades.csv"
LAST_SYNC = ROOT / "last_sync.json"

st.set_page_config(page_title="auto-stock dashboard", page_icon="📈", layout="wide")


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


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


# ── 헤더 + 마지막 sync 배지 (D6) ─────────────────────────────────────────────
st.title("📈 auto-stock — 여론 에이전트 대시보드")
_sync = _read_json(LAST_SYNC, {})
if _sync.get("synced_at"):
    st.caption(f"🔄 데이터 기준 시각: **{_sync['synced_at']}** (준실시간 — 우분투 박스가 주기 동기화)")
else:
    st.caption("🔄 last_sync.json 없음 — 로컬/미동기화 데이터")
st.info("읽기 전용 대시보드입니다. 실제 매매·주문은 우분투 서버에서만 수행됩니다. (KIS 모의투자)", icon="ℹ️")

tab_pf, tab_trades, tab_funnel, tab_opinion = st.tabs(
    ["💼 포트폴리오", "📜 매매 이력", "🔎 일일 결정 funnel", "🗣️ 여론 추세"])

# ── ① 포트폴리오 ─────────────────────────────────────────────────────────────
with tab_pf:
    pf = _read_json(PORTFOLIO, {})
    if not pf:
        st.warning("portfolio.json 없음/비어있음")
    else:
        positions = pf.get("positions", {}) or {}
        c1, c2, c3 = st.columns(3)
        c1.metric("현금(모의)", f"${pf.get('cash', 0):,.0f}")
        c2.metric("보유 종목 수", len(positions))
        c3.metric("갱신 시각", str(pf.get("updated_at", "-"))[:19])
        if positions:
            rows = [{"종목": s, "수량": v.get("shares"), "진입가": v.get("entry_price")}
                    for s, v in positions.items()]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.write("현재 보유 포지션 없음.")

# ── ② 매매 이력 ──────────────────────────────────────────────────────────────
with tab_trades:
    if not TRADES.exists():
        st.warning("trades.csv 없음")
    else:
        df = pd.read_csv(TRADES)
        st.metric("총 거래", len(df))
        if "net_profit_pct" in df.columns and len(df):
            closed = df[df["net_profit_pct"].notna() & (df["net_profit_pct"] != 0)]
            if len(closed):
                win = (closed["net_profit_pct"] > 0).mean() * 100
                st.metric("승률(청산 기준)", f"{win:.0f}%")
        st.dataframe(df.tail(200), use_container_width=True, hide_index=True)

# ── ③ 일일 결정 funnel ───────────────────────────────────────────────────────
with tab_funnel:
    md_files = sorted(REPORTS.glob("*.md"), reverse=True) if REPORTS.exists() else []
    if md_files:
        pick = st.selectbox("날짜 선택", [p.stem for p in md_files])
        target = REPORTS / f"{pick}.md"
        st.markdown(target.read_text(encoding="utf-8"))
    else:
        # 폴백: live decisions.jsonl 당일 action 집계
        recs = _read_jsonl(LIVE_DECISIONS)
        if recs:
            df = pd.DataFrame(recs)
            last_date = df["date"].max() if "date" in df else None
            st.write(f"최근 결정 일자: {last_date}")
            if "final_action" in df:
                st.bar_chart(df[df.get("date") == last_date]["final_action"].value_counts())
            st.dataframe(df.tail(100), use_container_width=True, hide_index=True)
        else:
            st.warning("리포트/decision 데이터 없음 (아직 라이브 구동 전이거나 미동기화)")

# ── ④ 여론 추세 ──────────────────────────────────────────────────────────────
with tab_opinion:
    snaps = _read_jsonl(SNAPSHOTS)
    if not snaps:
        st.warning("daily_opinion_snapshots.jsonl 없음")
    else:
        df = pd.DataFrame(snaps)
        if "date" in df:
            # 일자별 컨센서스 매수 종목 수 + 평균 opinion_score
            g = df.groupby("date").agg(
                consensus_buy=("is_consensus_buy", lambda s: int(pd.Series(s).fillna(False).astype(bool).sum())),
                avg_score=("opinion_score", "mean"),
                n=("symbol", "count"),
            ).reset_index().tail(40)
            st.subheader("일자별 컨센서스 매수 종목 수")
            st.altair_chart(
                alt.Chart(g).mark_bar().encode(x="date:T", y="consensus_buy:Q",
                                                tooltip=["date", "consensus_buy", "n"]),
                use_container_width=True)
            st.subheader("일자별 평균 opinion_score")
            st.altair_chart(
                alt.Chart(g).mark_line(point=True).encode(x="date:T", y="avg_score:Q",
                                                          tooltip=["date", "avg_score"]),
                use_container_width=True)
        st.caption(f"스냅샷 {len(df):,}건")
