import streamlit as st
import pandas as pd
import os
import json
import logging
import base64
from datetime import date, datetime, timedelta
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

import altair as alt
import pandas_market_calendars as mcal
import config
import portfolio
import collector
import signals as sig_module
from backtester import BacktestEngine

# --- Page Config ---
st.set_page_config(page_title="SentiQuant Dashboard", layout="wide")

PRICE_CHART_DEFAULT_DAYS = 63
PRICE_CHART_MAX_DAYS = 252

def get_logo_data_uri():
    logo_path = os.path.join(os.path.dirname(__file__), "assets", "sentiquant-logo.jpeg")
    try:
        with open(logo_path, "rb") as logo_file:
            encoded = base64.b64encode(logo_file.read()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"
    except OSError:
        return None

def load_trades():
    if os.path.exists(config.TRADES_FILE):
        df = pd.read_csv(config.TRADES_FILE)
        # FR-19: KIS 주문 추적 컬럼 — 구버전 trades.csv 호환 위해 없으면 빈 컬럼 추가
        for col in ("order_no", "kis_status"):
            if col not in df.columns:
                df[col] = None
        return df
    return pd.DataFrame()


def _mask_account(account_no: str) -> str:
    """KIS 계좌번호 마스킹 (Design §7) — '50123456-01' → '5012345*-01'."""
    if not account_no:
        return "미설정"
    head, _, tail = account_no.partition("-")
    if len(head) > 1:
        head = head[:-1] + "*"
    return f"{head}-{tail}" if tail else head


def kis_sync():
    """KIS 모의계좌 동기화 (FR-17) — 잔고를 Source of Truth로 portfolio.json 갱신.

    추가로 보유 종목 현재가를 KIS 실시간 시세로 갱신 (FR-18, 실패 시 collector 폴백).

    Returns:
        (synced_portfolio, prices, snapshot_messages) 튜플
    """
    from kis_broker import get_broker

    broker = get_broker()
    broker.connect()
    previous_portfolio = portfolio.load_portfolio()
    synced = portfolio.sync_from_kis(previous_portfolio, broker)
    portfolio.save_portfolio(synced)

    # FR-18: 보유 종목 현재가는 KIS API 시세 우선, 실패 시 collector 폴백
    prices = {}
    for sym in synced.positions:
        try:
            prices[sym] = broker.get_quote(sym)
        except Exception:
            fallback = collector.get_latest_open_price(sym)
            if fallback:
                prices[sym] = fallback

    snapshot_messages = refresh_price_history_snapshots(synced.positions)

    return synced, prices, snapshot_messages


def render_kis_panel(port, current_prices):
    """KIS 모의투자 계좌 영역 (FR-16, FR-17) — 헤더 상단에 표시."""
    st.markdown("### 🏦 계좌 현황")
    total_eval = port.cash + sum(
        current_prices.get(sym, pos.avg_price) * pos.shares
        for sym, pos in port.positions.items()
    )
    c_acct, c_cash, c_eval, c_sync = st.columns([1.2, 1, 1, 1])
    with c_acct:
        st.metric("계좌번호", _mask_account(config.KIS_ACCOUNT_NO))
    with c_cash:
        st.metric("예수금", f"${port.cash:,.2f}")
    with c_eval:
        st.metric("총 평가 금액", f"${total_eval:,.2f}")
    with c_sync:
        st.write("")
        if st.button("계좌 동기화", use_container_width=True):
            try:
                synced, prices, snapshot_messages = kis_sync()
                if prices:
                    st.session_state["current_prices"] = prices
                    st.session_state["current_prices_refreshed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                failed_snapshots = [
                    item["message"] for item in snapshot_messages if not item["ok"]
                ]
                st.session_state["last_position_message"] = {
                    "ok": not failed_snapshots,
                    "message": (
                        f"동기화 완료 — {len(synced.positions)}개 포지션, "
                        "가격 차트 확인 완료"
                    ),
                    "details": failed_snapshots,
                }
                st.rerun()
            except Exception as e:
                st.error(f"동기화 실패: {e}")
    st.divider()

def get_current_prices(symbols):
    prices = {}
    for symbol in symbols:
        price = collector.get_latest_open_price(symbol)
        if price:
            prices[symbol] = price
    return prices


def refresh_price_history_snapshots(symbols) -> list[dict]:
    snapshot_messages = []
    for symbol in sorted(symbols):
        ok, message = prime_price_history_snapshot(symbol)
        snapshot_messages.append({"ok": ok, "message": message})
    return snapshot_messages


def load_ohlcv_snapshot(symbol: str) -> pd.DataFrame:
    snapshot_dir = Path(config.BACKTEST_SNAPSHOT_DIR) / "v2" / "ohlcv"
    if not snapshot_dir.exists():
        return pd.DataFrame()

    snapshots = []
    snapshot_paths = sorted(snapshot_dir.glob(f"{symbol}_*.csv"))
    for snapshot_path in snapshot_paths:
        try:
            snapshot = pd.read_csv(snapshot_path)
        except (OSError, pd.errors.ParserError):
            continue
        if {"date", "close"}.issubset(snapshot.columns):
            snapshots.append(snapshot)

    if not snapshots:
        return pd.DataFrame()

    combined = pd.concat(snapshots, ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"], errors="coerce")
    combined = combined.dropna(subset=["date"]).sort_values("date")
    combined = combined.drop_duplicates(subset=["date"], keep="last")
    combined["date"] = combined["date"].dt.strftime("%Y-%m-%d")
    return combined.reset_index(drop=True)


def save_ohlcv_snapshot(symbol: str, ohlcv: pd.DataFrame) -> Path | None:
    if ohlcv.empty or not {"date", "close"}.issubset(ohlcv.columns):
        return None

    snapshot = ohlcv.copy()
    snapshot["date"] = pd.to_datetime(snapshot["date"], errors="coerce")
    snapshot = snapshot.dropna(subset=["date"]).sort_values("date")
    snapshot = snapshot.drop_duplicates(subset=["date"], keep="last")
    if snapshot.empty:
        return None

    snapshot["date"] = snapshot["date"].dt.strftime("%Y-%m-%d")
    start = snapshot["date"].iloc[0]
    end = snapshot["date"].iloc[-1]
    snapshot_dir = Path(config.BACKTEST_SNAPSHOT_DIR) / "v2" / "ohlcv"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"{symbol}_{start}_{end}.csv"
    snapshot.to_csv(snapshot_path, index=False)
    return snapshot_path


def get_last_completed_trading_day() -> date:
    """현재 시점 기준으로 완료된 마지막 NYSE 거래일을 반환한다."""
    now_utc = pd.Timestamp.now(tz="UTC")
    now_et = datetime.now(ZoneInfo(config.TIMEZONE))
    start = (now_et - timedelta(days=14)).strftime("%Y-%m-%d")
    end = now_et.strftime("%Y-%m-%d")

    try:
        nyse = mcal.get_calendar("NYSE")
        schedule = nyse.schedule(start_date=start, end_date=end)
        if not schedule.empty:
            completed = schedule[schedule["market_close"] <= now_utc]
            if not completed.empty:
                return completed.index[-1].date()
    except Exception as exc:
        logging.warning(f"NYSE 거래일 계산 실패, 주말 제외 방식으로 폴백: {exc}")

    fallback = now_et.date()
    while fallback.weekday() >= 5:
        fallback -= timedelta(days=1)
    return fallback


def snapshot_is_fresh_enough(snapshot: pd.DataFrame, days: int) -> bool:
    if snapshot.empty or "date" not in snapshot.columns or len(snapshot) < days:
        return False

    last_date = pd.to_datetime(snapshot["date"], errors="coerce").max()
    if pd.isna(last_date):
        return False

    return last_date.date() >= get_last_completed_trading_day()


def prime_price_history_snapshot(symbol: str, days: int = PRICE_CHART_MAX_DAYS) -> tuple[bool, str]:
    symbol = symbol.upper()
    existing = load_ohlcv_snapshot(symbol)
    if snapshot_is_fresh_enough(existing, days):
        return True, f"{symbol} 1년 가격 스냅샷 이미 준비됨"

    ohlcv = collector.get_ohlcv(symbol, days=days)
    snapshot_path = save_ohlcv_snapshot(symbol, ohlcv)
    if snapshot_path:
        load_price_history.clear()
        return True, f"{symbol} 1년 가격 스냅샷 저장 완료"

    if not existing.empty:
        return False, f"{symbol} 신규 가격 스냅샷 저장 실패, 기존 {len(existing)}개 종가 사용"
    return False, f"{symbol} 가격 스냅샷 저장 실패"


@st.cache_data(ttl=300, show_spinner=False)
def load_price_history(symbol: str, days: int = PRICE_CHART_MAX_DAYS) -> pd.DataFrame:
    frames = []
    snapshot = load_ohlcv_snapshot(symbol)
    if not snapshot.empty:
        frames.append(snapshot)

    if not snapshot_is_fresh_enough(snapshot, days):
        ohlcv = collector.get_ohlcv(symbol, days=days)
        if ohlcv.empty or not {"date", "close"}.issubset(ohlcv.columns):
            if not frames:
                return pd.DataFrame()
        else:
            save_ohlcv_snapshot(symbol, ohlcv)
            frames.append(ohlcv)

    chart_data = pd.concat(frames, ignore_index=True)
    chart_data["date"] = pd.to_datetime(chart_data["date"], errors="coerce")
    chart_data["Price"] = pd.to_numeric(chart_data["close"], errors="coerce")
    chart_data = chart_data.dropna(subset=["date", "Price"]).sort_values("date")
    chart_data = chart_data.drop_duplicates(subset=["date"], keep="last").tail(days)
    return chart_data.set_index("date")[["Price"]]


def render_price_chart(chart_data: pd.DataFrame) -> None:
    plot_data = chart_data.reset_index().rename(columns={"date": "Date"})
    visible_data = plot_data.tail(min(PRICE_CHART_DEFAULT_DAYS, len(plot_data)))

    initial_domain = [
        visible_data["Date"].min().to_pydatetime(),
        visible_data["Date"].max().to_pydatetime(),
    ]

    zoom = alt.selection_interval(
        bind="scales",
        encodings=["x"],
        value={"Date": initial_domain},
    )
    label = (
        alt.Chart(plot_data)
        .transform_filter(zoom)
        .transform_aggregate(start="min(Date)", end="max(Date)")
        .transform_calculate(
            span_days="(toDate(datum.end) - toDate(datum.start)) / 86400000",
            span_months="round(datum.span_days / 30.4375)",
            period=(
                "datum.span_months >= 12 ? '1년' : "
                "(datum.span_months < 1 ? '1개월' : format(datum.span_months, 'd') + '개월')"
            ),
            label="'가격 변동 (최근 ' + datum.period + ')'",
        )
        .mark_text(
            align="left",
            baseline="middle",
            color="#f8fafc",
            fontSize=15,
            fontWeight=700,
        )
        .encode(text="label:N")
        .properties(height=24)
    )
    line = (
        alt.Chart(plot_data)
        .mark_line(color="#2563eb", strokeWidth=2)
        .encode(
            x=alt.X(
                "Date:T",
                title="",
                scale=alt.Scale(domain=initial_domain),
            ),
            y=alt.Y(
                "Price:Q",
                title="종가",
                scale=alt.Scale(zero=False),
            ),
            tooltip=[
                alt.Tooltip("Date:T", title="날짜", format="%Y-%m-%d"),
                alt.Tooltip("Price:Q", title="종가", format=",.2f"),
            ],
        )
        .add_params(zoom)
        .properties(height=220)
    )
    st.altair_chart(alt.vconcat(label, line).resolve_scale(x="shared"), width="stretch")

def run_signals_now():
    with st.spinner("신호 계산 중..."):
        try:
            result = sig_module.generate_signals_for_all(config.SYMBOLS)
            if result:
                portfolio.save_signals(result)
                return True, "신호 계산 완료"
            return False, "신호 계산 실패"
        except Exception as exc:
            logging.exception("Signal refresh failed")
            return False, f"신호 계산 실패: {exc}"

def profit_class(value):
    if value > 0:
        return "profit-pos"
    if value < 0:
        return "profit-neg"
    return "profit-flat"

def format_signed_money(value, decimals=2):
    if value == 0:
        sign = ""
    else:
        sign = "+" if value > 0 else "-"
    return f"{sign}${abs(value):,.{decimals}f}"

def format_signed_percent(value):
    if value == 0:
        sign = ""
    else:
        sign = "+" if value > 0 else "-"
    return f"{sign}{abs(value):.2f}%"

def format_money(value):
    return f"${value:,.2f}"

def main():
    # --- Custom CSS for Layout & Cards ---
    st.markdown("""
        <style>
        .main { background-color: #fcfcfc; }
        .stMetric {
            background-color: #171b22;
            color: #f8fafc;
            padding: 10px;
            border-radius: 5px;
            border: 1px solid #2f3744;
        }
        [data-testid="stMetric"] * { color: #f8fafc !important; }
        [data-testid="stMetricLabel"] * { color: #cbd5e1 !important; }
        [data-testid="stMetricValue"] {
            color: #ffffff !important;
            font-size: 1.5rem !important;
        }
        .brand-bar {
            display: flex;
            align-items: center;
            gap: 10px;
            margin: 4px 0 28px 0;
        }
        .brand-mark {
            width: 28px;
            height: 28px;
            border-radius: 7px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: #2563eb;
            color: #ffffff;
            font-size: 0.95rem;
            font-weight: 800;
        }
        .brand-logo {
            width: 34px;
            height: 34px;
            display: block;
            border-radius: 9px;
            object-fit: cover !important;
            box-shadow: 0 8px 20px rgba(37, 99, 235, 0.28);
        }
        .brand-name {
            color: #f8fafc;
            font-size: 1.55rem;
            font-weight: 800;
            line-height: 1;
        }
        .brand-subtitle {
            color: #94a3b8;
            font-size: 0.78rem;
            margin-top: 2px;
        }
        
        .stock-card {
            border-radius: 5px;
            padding: 10px;
            margin-bottom: 10px;
            background-color: white;
            transition: 0.3s;
        }
        div[class*="st-key-stock_card_"] {
            position: relative;
        }
        .stock-card-panel {
            border-radius: 6px;
            min-height: 116px;
            padding: 12px;
            transition: border-color 0.18s ease, transform 0.18s ease, background-color 0.18s ease;
        }
        div[class*="st-key-stock_card_"]:hover .stock-card-panel {
            border-color: #3b82f6 !important;
        }
        .stock-card-symbol {
            color: #cbd5e1;
            font-size: 0.75rem;
            font-weight: 700;
            margin-bottom: 4px;
        }
        .stock-card-name {
            color: #f8fafc;
            font-size: 0.85rem;
            margin-bottom: 18px;
        }
        .stock-card-profit {
            font-size: 1.1rem;
        }
        .stock-card-shares {
            bottom: 12px;
            color: #94a3b8;
            font-size: 0.7rem;
            position: absolute;
            right: 12px;
        }
        div[class*="st-key-stock_card_btn_"] {
            inset: 0;
            position: absolute;
            z-index: 2;
        }
        div[class*="st-key-stock_card_btn_"] button {
            background: transparent !important;
            border: 0 !important;
            color: transparent !important;
            cursor: pointer;
            min-height: 116px;
            opacity: 0;
            padding: 0;
            width: 100%;
        }
        .profit-pos { color: #ef4444 !important; font-weight: bold; }
        .profit-neg { color: #3b82f6 !important; font-weight: bold; }
        .profit-flat { color: #94a3b8 !important; font-weight: bold; }
        .detail-profit-value {
            font-size: 1.75rem;
            font-weight: 800;
            line-height: 1.2;
            margin: 0.35rem 0 0;
        }
        .chart-stale-note {
            color: #f59e0b;
            font-size: 0.78rem;
            line-height: 1.4;
            margin: 4px 0 0;
        }
        .sub-text { color: #757575; font-size: 0.75rem; }
        .price-large { font-size: 2rem; font-weight: bold; }
        .total-summary {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 8px;
            text-align: right;
            width: max-content;
            margin-left: auto;
        }
        .total-summary-label,
        .total-summary-status {
            color: #757575;
            font-size: 0.75rem;
            line-height: 1.2;
            white-space: nowrap;
        }
        .total-summary-value {
            font-size: 1.85rem;
            font-weight: 800;
            line-height: 1;
            margin: 0;
            white-space: nowrap;
        }
        .total-summary-value.profit-flat { color: #f8fafc; }
        </style>
    """, unsafe_allow_html=True)

    # --- Header Area ---
    logo_uri = get_logo_data_uri()
    logo_html = (
        f'<img class="brand-logo" src="{logo_uri}" alt="SentiQuant logo">'
        if logo_uri
        else '<div class="brand-mark">SQ</div>'
    )
    st.markdown(f"""
        <div class="brand-bar">
            {logo_html}
            <div>
                <div class="brand-name">SentiQuant</div>
                <div class="brand-subtitle">Sentiment 분석 기반의 투자 지원</div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    port = portfolio.load_portfolio()
    current_prices = st.session_state.get("current_prices", {})

    # --- KIS 모의투자 계좌 영역 (FR-16, FR-17) ---
    render_kis_panel(port, current_prices)

    last_position_message = st.session_state.pop("last_position_message", None)
    if last_position_message:
        message = last_position_message["message"]
        details = last_position_message.get("details", [])
        if details:
            message += f" · {'; '.join(details)}"
        if last_position_message["ok"]:
            st.toast(message)
        else:
            st.warning(message)

    col_nav, col_total_info = st.columns([3, 1])
    with col_nav:
        st.subheader(f"📊보유 주식 개요 ({len(port.positions)}개 포지션)")
    
    # 상단 총 미실현 수익 계산
    total_unrealized_profit = 0
    priced_symbols = []
    missing_price_symbols = []
    for sym, pos in port.positions.items():
        if sym in current_prices:
            p = current_prices[sym]
            total_unrealized_profit += (p - pos.avg_price) * pos.shares
            priced_symbols.append(sym)
        else:
            missing_price_symbols.append(sym)
    total_profit_class = profit_class(total_unrealized_profit)
    price_status = f"총 {len(priced_symbols)}개 종목 가격 반영"
        
    with col_total_info:
        if port.positions and not priced_symbols:
            st.markdown(f"""
                <div class='total-summary'>
                    <div class='total-summary-label'>총 미실현 수익</div>
                    <div class='total-summary-value profit-flat'>가격 미조회</div>
                    <div class='total-summary-status'>{price_status}</div>
                </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
                <div class='total-summary'>
                    <div class='total-summary-label'>총 미실현 수익</div>
                    <div class='total-summary-value {total_profit_class}'>{format_signed_money(total_unrealized_profit)}</div>
                    <div class='total-summary-status'>{price_status}</div>
                </div>
            """, unsafe_allow_html=True)

    if port.positions and missing_price_symbols:
        st.info(
            f"가격을 아직 조회하지 않은 종목: {', '.join(missing_price_symbols)}\n\n"
            "현재가 새로고침 후 손익이 계산됩니다."
        )

    # --- 1. Top Horizontal Stock Cards ---
    if port.positions:
        symbols = list(port.positions.keys())
        card_cols = st.columns(max(len(symbols), 5))

        if "selected_symbol" not in st.session_state:
            st.session_state["selected_symbol"] = symbols[0]
            
        for i, sym in enumerate(symbols):
            pos = port.positions[sym]
            has_price = sym in current_prices
            if has_price:
                p = current_prices[sym]
                profit = (p - pos.avg_price) * pos.shares
                p_class = profit_class(profit)
                profit_text = format_signed_money(profit)
            else:
                p_class = "profit-flat"
                profit_text = "가격 미조회"
            
            with card_cols[i % 5]:
                is_selected = (sym == st.session_state["selected_symbol"])
                
                if len(symbols) > 1 and st.button(f"{sym}", key=f"btn_{sym}", width="stretch"):
                    st.session_state["selected_symbol"] = sym
                    st.rerun()

                bg_color = "#172033" if is_selected else "#171b22"
                border_color = "#2563eb" if is_selected else "#2f3744"
                with st.container(key=f"stock_card_{sym}"):
                    st.markdown(f"""
                        <div class='stock-card-panel' style='background-color: {bg_color}; border: 1px solid {border_color};'>
                            <div class='stock-card-symbol'>{sym}.US</div>
                            <div class='stock-card-name'>{config.COMPANY_NAMES.get(sym, sym)}</div>
                            <div class='stock-card-profit {p_class}'>{profit_text}</div>
                            <div class='stock-card-shares'>보유 {pos.shares:,}주</div>
                        </div>
                    """, unsafe_allow_html=True)
                    if st.button("종목 선택", key=f"stock_card_btn_{sym}", width="stretch"):
                        st.session_state["selected_symbol"] = sym
                        st.rerun()
    else:
        st.info("보유 종목이 없습니다.")

    st.divider()

    # --- 2. Main Detail Section ---
    sel_sym = st.session_state.get("selected_symbol")
    if sel_sym and sel_sym in port.positions:
        pos = port.positions[sel_sym]
        has_selected_price = sel_sym in current_prices
        price = current_prices.get(sel_sym, pos.avg_price)
        price_delta = price - pos.avg_price
        price_delta_rate = (price_delta / pos.avg_price * 100) if pos.avg_price else 0
        selected_profit = price_delta * pos.shares
        selected_profit_rate = price_delta_rate
        selected_profit_class = profit_class(selected_profit)
        price_label = "최근 시가" if has_selected_price else "매입 단가"
        current_price_refreshed_at = st.session_state.get("current_prices_refreshed_at")
        price_meta = (
            f"조회: {current_price_refreshed_at}"
            if has_selected_price and current_price_refreshed_at
            else ""
        )
        selected_value_label = "시장 가치" if has_selected_price else "매입 기준 금액"
        selected_value = price * pos.shares
        selected_delta_html = (
            f"<span class='{profit_class(price_delta)}'>{format_signed_money(price_delta)} ({format_signed_percent(price_delta_rate)})</span>"
            if has_selected_price
            else "<span class='sub-text'>가격 미조회 · 손익 계산 대기</span>"
        )
        selected_profit_html = (
            f"<div class='detail-profit-value {selected_profit_class}'>{format_signed_money(selected_profit)}</div>"
            if has_selected_price
            else "<div class='detail-profit-value profit-flat'>가격 미조회</div>"
        )
        selected_rate_text = format_signed_percent(selected_profit_rate) if has_selected_price else "가격 미조회"
        selected_rate_class = profit_class(selected_profit_rate) if has_selected_price else "profit-flat"
        
        col_main, col_side = st.columns([2.2, 0.8])
        
        with col_main:
            c_title, c_price_info = st.columns([2, 1])
            with c_title:
                st.markdown(f"<h1 style='margin-bottom:0;'>{config.COMPANY_NAMES.get(sel_sym, sel_sym)}</h1>", unsafe_allow_html=True)
            with c_price_info:
                st.markdown(f"""
                    <div style='text-align: right;'>
                        <span class='sub-text'>{price_label}</span><br>
                        <span class='price-large'>{price:,.2f}</span><br>
                        {selected_delta_html}
                        {f"<br><span class='sub-text'>{price_meta}</span>" if price_meta else ""}
                    </div>
                """, unsafe_allow_html=True)
            
            chart_data = load_price_history(sel_sym, days=PRICE_CHART_MAX_DAYS)
            if chart_data.empty:
                st.warning(f"{sel_sym} 가격 차트 데이터를 가져오지 못했습니다.")
            else:
                render_price_chart(chart_data)
                visible_data = chart_data.tail(min(PRICE_CHART_DEFAULT_DAYS, len(chart_data)))
                last_completed_trading_day = get_last_completed_trading_day()
                st.caption(
                    f"차트 데이터 기준일: {chart_data.index.max():%Y-%m-%d}  \n"
                    f"기본 표시: {visible_data.index.min():%Y-%m-%d} ~ "
                    f"{visible_data.index.max():%Y-%m-%d}  \n"
                    f"확대/축소 가능 범위: {chart_data.index.min():%Y-%m-%d} ~ "
                    f"{chart_data.index.max():%Y-%m-%d}"
                )
                if chart_data.index.max().date() < last_completed_trading_day:
                    st.markdown(
                        (
                            "<div class='chart-stale-note'>"
                            f"차트 데이터가 최신 거래일({last_completed_trading_day:%Y-%m-%d})보다 오래되었습니다."
                            "</div>"
                        ),
                        unsafe_allow_html=True,
                    )
            
            m_col1, m_col2, m_col3 = st.columns(3)
            with m_col1:
                st.write(selected_value_label)
                st.subheader(format_money(selected_value))
            with m_col2:
                st.write("매입 단가")
                st.subheader(format_money(pos.avg_price))
            with m_col3:
                st.write("미실현 수익")
                st.markdown(selected_profit_html, unsafe_allow_html=True)
            
            st.divider()
            st.write(f"{sel_sym} 최근 거래")
            trades = load_trades()
            if not trades.empty:
                st.dataframe(trades[trades['symbol'] == sel_sym].head(5), width="stretch")

        with col_side:
            st.markdown(f"""
                <div style='border: 1px solid #2f3744; padding: 20px; border-radius: 8px; background-color: #171b22;'>
                    <div style='color: #cbd5e1; font-size: 0.9rem; margin-bottom: 20px;'>포지션 상태</div>
                    <div style='margin-bottom: 25px;'>
                        <span style='color: #94a3b8; font-size: 0.75rem;'>보유 수량</span><br>
                        <span style='color: #f8fafc; font-size: 1.6rem; font-weight: bold;'>{pos.shares:,} 주</span>
                    </div>
                    <div style='margin-bottom: 25px;'>
                        <span style='color: #94a3b8; font-size: 0.75rem;'>{selected_value_label}</span><br>
                        <span style='color: #f8fafc; font-size: 1.6rem; font-weight: bold;'>{format_money(selected_value)}</span>
                    </div>
                    <div style='margin-bottom: 25px;'>
                        <span style='color: #94a3b8; font-size: 0.75rem;'>수익률</span><br>
                        <span class='{selected_rate_class}' style='font-size: 1.6rem;'>{selected_rate_text}</span>
                    </div>
                    <div style='margin-bottom: 25px;'>
                        <span style='color: #94a3b8; font-size: 0.75rem;'>미실현 수익</span><br>
                        <span class='{selected_profit_class}' style='font-size: 1.6rem;'>{format_signed_money(selected_profit) if has_selected_price else "가격 미조회"}</span>
                    </div>
                    <hr style='border-top: 1px solid #2f3744;'>
                    <div style='text-align: right; display: flex; align-items: center; justify-content: flex-end;'>
                        <span style='color: #60a5fa; font-size: 0.8rem; margin-right: 5px;'>●</span>
                        <span style='color: #60a5fa; font-size: 0.8rem;'>자동 거래 활성화</span>
                    </div>
                </div>
            """, unsafe_allow_html=True)
            
            st.write("")
            if st.button("가격/차트 갱신", width="stretch"):
                with st.spinner("현재가와 가격 차트 갱신 중..."):
                    refreshed_prices = get_current_prices(port.positions.keys())
                    snapshot_messages = refresh_price_history_snapshots(port.positions.keys())
                st.session_state["current_prices"] = refreshed_prices
                st.session_state["current_prices_refreshed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                failed_symbols = [symbol for symbol in port.positions if symbol not in refreshed_prices]
                failed_snapshots = [
                    item["message"] for item in snapshot_messages if not item["ok"]
                ]
                st.session_state["last_price_refresh"] = {
                    "ok": not failed_symbols and not failed_snapshots,
                    "message": (
                        f"{len(refreshed_prices)}개 종목 가격/차트 갱신 완료"
                        if refreshed_prices
                        else "가격 조회 실패"
                    ),
                    "failed_symbols": failed_symbols,
                    "failed_snapshots": failed_snapshots,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                st.rerun()
            
            if st.button("실시간 신호 갱신", width="stretch"):
                ok, message = run_signals_now()
                st.session_state["last_signal_refresh"] = {
                    "ok": ok,
                    "message": message,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                st.rerun()

            last_price_refresh = st.session_state.get("last_price_refresh")
            if last_price_refresh:
                price_refresh_text = f"{last_price_refresh['message']} · {last_price_refresh['time']}"
                if last_price_refresh["failed_symbols"]:
                    price_refresh_text += f" · 미조회: {', '.join(last_price_refresh['failed_symbols'])}"
                if last_price_refresh.get("failed_snapshots"):
                    price_refresh_text += f" · {'; '.join(last_price_refresh['failed_snapshots'])}"
                if last_price_refresh["ok"]:
                    st.toast(price_refresh_text)
                else:
                    st.warning(price_refresh_text)

            last_signal_refresh = st.session_state.get("last_signal_refresh")
            if last_signal_refresh:
                status_text = f"{last_signal_refresh['message']} · {last_signal_refresh['time']}"
                if last_signal_refresh["ok"]:
                    st.toast(status_text)
                else:
                    st.error(status_text)

    st.divider()

    # --- 3. Full Trade History Section ---
    st.subheader("📜 전체 거래 내역")
    all_trades = load_trades()
    if not all_trades.empty:
        st.dataframe(
            all_trades.sort_values("date", ascending=False),
            width="stretch"
        )
    else:
        st.info("거래 내역이 없습니다.")

    st.divider()

    # --- 4. Position Management Section ---
    st.subheader("⚙️ 포지션 관리")
    with st.expander("포지션 수동 추가/수정"):
        with st.form("add_position_form"):
            col1, col2, col3 = st.columns(3)
            new_sym = col1.text_input("티커 (예: AAPL)").upper()
            new_shares = col2.number_input("수량", min_value=1, step=1)
            new_avg_price = col3.number_input("평균 매입가", min_value=0.01, step=0.01)
            
            submit_pos = st.form_submit_button("포지션 저장")
            
            if submit_pos and new_sym:
                temp_port = portfolio.load_portfolio()
                from portfolio import Position
                new_pos = Position(
                    symbol=new_sym,
                    shares=int(new_shares),
                    avg_price=float(new_avg_price),
                    buy_date=datetime.now().isoformat(),
                    total_cost=float(new_shares * new_avg_price)
                )
                temp_port.positions[new_sym] = new_pos
                portfolio.save_portfolio(temp_port)
                with st.spinner(f"{new_sym} 1년 가격 스냅샷 준비 중..."):
                    snapshot_ok, snapshot_message = prime_price_history_snapshot(new_sym)
                st.session_state["last_position_message"] = {
                    "ok": snapshot_ok,
                    "message": f"{new_sym} 포지션 저장 완료",
                    "details": [] if snapshot_ok else [snapshot_message],
                }
                st.rerun()

    with st.expander("포지션 삭제"):
        if port.positions:
            del_sym = st.selectbox("삭제할 종목 선택", list(port.positions.keys()))
            if st.button("선택한 포지션 삭제", type="primary"):
                temp_port = portfolio.load_portfolio()
                if del_sym in temp_port.positions:
                    del temp_port.positions[del_sym]
                    portfolio.save_portfolio(temp_port)
                    st.success(f"{del_sym} 포지션이 삭제되었습니다.")
                    st.rerun()
        else:
            st.info("삭제할 포지션이 없습니다.")

if __name__ == "__main__":
    main()
