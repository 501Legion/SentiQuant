# Design Ref: streamlit-dashboard-deploy §6.1 — 자립형 읽기전용 대시보드 (Option C)
# Streamlit Community Cloud 배포용. 커밋된 data만 읽어 렌더한다.
# 불가침 원칙: KIS·FinBERT·실주문·무거운 모듈(torch/transformers/community_live/backtester)
#            절대 import 금지. streamlit·pandas·altair·표준 라이브러리만.
# Plan SC: SC-01(heavy import 0), SC-05(실주문 호출 0)
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
SNAPSHOTS = DATA / "community" / "daily_opinion_snapshots.jsonl"
PORTFOLIO = DATA / "portfolio.json"
TRADES = DATA / "trades.csv"
OHLCV_DIR = DATA / "backtest_snapshots" / "v2" / "ohlcv"   # 커밋된 가격 스냅샷(읽기전용)
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


# ── 한글 라벨 매핑 (대시보드 표시 전용 — 데이터는 원문 유지) ─────────────────
ACTION_KO = {"BUY": "매수", "SELL": "매도", "SKIP": "보류", "HOLD": "보유 유지"}
TREND_KO = {"UP": "상승", "DOWN": "하락", "FLAT": "보합"}
VELOCITY_KO = {"SPIKE": "급증", "NORMAL": "보통", "FADING": "감소"}
REASON_KO = {
    "universe_blocked": "유동성 유니버스 미포함",
    "safety_universe_blocked": "안전장치 — 유니버스 차단",
    "cost_blocked": "거래비용 대비 기대수익 부족",
    "safety_cost_blocked": "안전장치 — 비용 차단",
    "insufficient_cash": "현금 부족",
    "low_opinion_score": "여론 점수 미달",
    "high_noise": "중립(노이즈) 비율 과다",
    "neutral_spike": "중립 의견 급증",
    "consensus_break": "컨센서스 붕괴",
    "no_rule_signal": "룰 신호 없음",
    "history_downsize": "과거 유사 사례 부진 — 비중 축소",
    "low_persistence_downsize": "신호 지속일 부족 — 비중 축소",
    "new_spike_downsize": "신규 급등 종목 — 비중 축소",
    "llm_fallback_to_rule_based": "LLM 실패 — 룰 기반 대체",
    "llm_low_confidence_kept_rule": "LLM 저신뢰 — 룰 판단 유지",
    "llm_buy_overridden_by_rule_skip": "룰 우선 — LLM 매수 기각",
}


def _reasons_ko(codes) -> str:
    if not codes:
        return "-"
    return ", ".join(REASON_KO.get(c, c) for c in codes)


def _parse_funnel(md: str) -> dict[str, int]:
    """일일 보고서의 funnel 표에서 단계별 수치 추출. 실패 시 빈 dict(원문만 표시)."""
    keys = {"①": "입력", "②": "중립 제외", "③": "컨센서스 미달",
            "④": "게이트 차단", "⑤": "매수", "⑥": "매도"}
    out: dict[str, int] = {}
    for line in md.splitlines():
        for mark, name in keys.items():
            if line.strip().startswith(f"| {mark}"):
                nums = re.findall(r"\d+", line)
                if nums:
                    out[name] = int(nums[0])
    return out if "입력" in out else {}


# ── 헤더 + 마지막 sync 배지 (D6) ─────────────────────────────────────────────
st.title("📈 auto-stock — 여론 에이전트 대시보드")
_sync = _read_json(LAST_SYNC, {})
if _sync.get("synced_at"):
    try:
        _kst = datetime.fromisoformat(_sync["synced_at"]).astimezone(
            timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        _kst = _sync["synced_at"]
    st.caption(f"🔄 데이터 기준 시각: **{_kst} (한국시간)** — 매매 서버가 30분마다 자동 동기화")
else:
    st.caption("🔄 동기화 기록 없음 — 로컬 데이터 기준")
st.info("읽기 전용 대시보드입니다. 실제 매매·주문은 우분투 서버에서만 수행됩니다. (KIS 모의투자)", icon="ℹ️")

tab_pf, tab_trades, tab_funnel, tab_opinion = st.tabs(
    ["💼 포트폴리오", "📜 매매 이력", "🔎 일일 결정", "🗣️ 여론 추세"])

# ── ① 포트폴리오 (보유 개요 + 평가) ──────────────────────────────────────────
with tab_pf:
    pf = _read_json(PORTFOLIO, {})
    if not pf:
        st.warning("portfolio.json 없음/비어있음")
    else:
        positions = pf.get("positions", {}) or {}
        cash = float(pf.get("cash", 0) or 0)
        # 보유 평가액 (커밋 스냅샷 최신 종가 기준)
        rows, holdings_val = [], 0.0
        for s, v in positions.items():
            shares = v.get("shares") or 0
            entry = v.get("entry_price") or 0
            last = _latest_close(s)
            eval_val = (last or entry) * shares
            holdings_val += eval_val
            pnl = ((last - entry) / entry * 100) if (last and entry) else None
            rows.append({"종목": s, "수량": shares, "진입가": round(entry, 2),
                         "현재가": round(last, 2) if last else "-",
                         "평가액": round(eval_val, 2),
                         "손익%": round(pnl, 2) if pnl is not None else "-"})
        equity = cash + holdings_val
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("현금(모의)", f"${cash:,.0f}")
        c2.metric("보유 평가액", f"${holdings_val:,.0f}")
        c3.metric("총 자산", f"${equity:,.0f}")
        c4.metric("보유 종목 수", len(positions))
        st.caption("⚠️ 현재가는 커밋된 가격 스냅샷의 최신 종가 — 실시간 아님(준실시간).")
        if rows:
            st.subheader("📊 보유 종목 개요")
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        else:
            st.write("현재 보유 포지션 없음.")

        # 가격 차트 (보유 종목 우선, 없으면 스냅샷 보유 종목 전체)
        st.subheader("📈 가격 차트")
        syms = list(positions.keys()) or _available_symbols()
        if syms:
            sel = st.selectbox("종목 선택", syms)
            hist = _load_ohlcv(sel)
            if hist.empty:
                st.info(f"{sel} 가격 스냅샷 없음")
            else:
                chart = alt.Chart(hist).mark_line().encode(
                    x="date:T", y=alt.Y("close:Q", scale=alt.Scale(zero=False)),
                    tooltip=["date:T", "close:Q"])
                st.altair_chart(chart, width="stretch")
                st.caption(f"{sel}: {len(hist)}일치 ({hist['date'].min():%Y-%m-%d} ~ {hist['date'].max():%Y-%m-%d})")
        else:
            st.info("가격 스냅샷(data/backtest_snapshots) 없음")

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
        st.dataframe(df.tail(200), width="stretch", hide_index=True)

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
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("분석 종목", f"{funnel.get('입력', 0)}개")
            c2.metric("중립 제외", f"-{funnel.get('중립 제외', 0)}")
            c3.metric("컨센서스 미달", f"-{funnel.get('컨센서스 미달', 0)}")
            c4.metric("게이트 차단", f"-{funnel.get('게이트 차단', 0)}")
            c5.metric("매수 / 매도", f"{funnel.get('매수', 0)} / {funnel.get('매도', 0)}")

            # 단계별 생존 종목 수 — 어디서 걸러졌는지 한눈에
            survive = [funnel.get("입력", 0)]
            for k in ("중립 제외", "컨센서스 미달", "게이트 차단"):
                survive.append(max(survive[-1] - funnel.get(k, 0), 0))
            fdf = pd.DataFrame({
                "단계": ["① 전체 수집", "② 중립 걸러냄", "③ 여론 합의 확인", "④ 안전장치 통과"],
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
            st.caption("커뮤니티에서 언급된 모든 종목이 위 단계를 통과해야 실제 주문으로 이어집니다.")

        # 당일 종목별 판단 내역 (게이트까지 올라온 종목)
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
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        elif pick:
            st.caption("이 날은 게이트까지 도달한 종목이 없습니다.")

        if md:
            with st.expander("📄 상세 보고서 원문"):
                st.markdown(md)

# ── ④ 여론 추세 ──────────────────────────────────────────────────────────────
with tab_opinion:
    snaps = _read_jsonl(SNAPSHOTS)
    if not snaps:
        st.warning("여론 스냅샷 데이터가 없습니다 (미동기화).")
    else:
        df = pd.DataFrame(snaps)
        latest = df["date"].max() if "date" in df else None
        today = df[df["date"] == latest].copy() if latest else pd.DataFrame()

        if not today.empty:
            # 최신일 요약
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("분석 종목", f"{len(today)}개")
            c2.metric("매수 합의 후보", f"{int(today['is_consensus_buy'].fillna(False).astype(bool).sum())}개")
            c3.metric("평균 여론 점수", f"{today['opinion_score'].mean():.1f}점")
            c4.metric("총 언급 수", f"{int(today['total_mentions'].sum()):,}건")
            st.caption(f"{latest} 기준 — 점수는 0~100 (50 중립, 높을수록 매수 여론 우세)")

            # 오늘의 여론 상위 종목
            st.subheader(f"🔥 오늘의 여론 상위 종목 ({latest})")
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

        # 종목별 추이 — 최근 언급 많은 순으로 선택
        st.subheader("📊 종목별 여론 흐름")
        recent_syms = (df[df["date"] >= sorted(df["date"].unique())[-7:][0]]
                       .groupby("symbol")["total_mentions"].sum()
                       .sort_values(ascending=False).index.tolist()) if "date" in df else []
        if recent_syms:
            sym = st.selectbox("종목 선택 (최근 1주 언급 많은 순)", recent_syms)
            sdf = df[df["symbol"] == sym].sort_values("date").tail(40)
            score_line = alt.Chart(sdf).mark_line(point=True, color="#e4584c").encode(
                x=alt.X("date:T", title="날짜"),
                y=alt.Y("opinion_score:Q", title="여론 점수", scale=alt.Scale(domain=[0, 100])),
                tooltip=[alt.Tooltip("date:T", title="날짜"),
                         alt.Tooltip("opinion_score:Q", title="여론 점수", format=".1f")])
            base50 = alt.Chart(pd.DataFrame({"y": [50]})).mark_rule(
                strokeDash=[4, 4], color="#9aa0a6").encode(y="y:Q")
            st.altair_chart((score_line + base50).properties(height=240), width="stretch")
            st.altair_chart(
                alt.Chart(sdf).mark_bar(color="#6b9bd1").encode(
                    x=alt.X("date:T", title="날짜"),
                    y=alt.Y("total_mentions:Q", title="언급 수"),
                    tooltip=[alt.Tooltip("date:T", title="날짜"),
                             alt.Tooltip("total_mentions:Q", title="언급 수")],
                ).properties(height=140),
                width="stretch")

        # 시장 전체 분위기 추이
        st.subheader("🌡️ 전체 시장 여론 추이")
        g = df.groupby("date").agg(
            매수합의=("is_consensus_buy", lambda s: int(pd.Series(s).fillna(False).astype(bool).sum())),
            평균점수=("opinion_score", "mean"),
            종목수=("symbol", "count"),
        ).reset_index().tail(40)
        col_a, col_b = st.columns(2)
        with col_a:
            st.altair_chart(
                alt.Chart(g).mark_bar(color="#6b9bd1").encode(
                    x=alt.X("date:T", title="날짜"),
                    y=alt.Y("매수합의:Q", title="매수 합의 종목 수"),
                    tooltip=[alt.Tooltip("date:T", title="날짜"), "매수합의", "종목수"],
                ).properties(height=220, title="일자별 매수 합의 종목 수"),
                width="stretch")
        with col_b:
            st.altair_chart(
                alt.Chart(g).mark_line(point=True, color="#e4584c").encode(
                    x=alt.X("date:T", title="날짜"),
                    y=alt.Y("평균점수:Q", title="평균 여론 점수"),
                    tooltip=[alt.Tooltip("date:T", title="날짜"),
                             alt.Tooltip("평균점수:Q", format=".1f")],
                ).properties(height=220, title="일자별 평균 여론 점수"),
                width="stretch")
        st.caption(f"누적 여론 스냅샷 {len(df):,}건 · 최근 40일 표시")
