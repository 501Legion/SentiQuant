import streamlit as st
import pandas as pd
import os
import json
import logging
from datetime import datetime
import subprocess
import numpy as np

import config
import portfolio
import collector
import signals as sig_module
from backtester import BacktestEngine

# --- Page Config ---
st.set_page_config(page_title="Auto-Stock Dashboard", layout="wide")

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
        (synced_portfolio, prices) 튜플
    """
    from kis_broker import get_broker

    broker = get_broker()
    broker.connect()
    synced = portfolio.sync_from_kis(portfolio.load_portfolio(), broker)
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
    return synced, prices


def render_kis_panel(port, current_prices):
    """KIS 모의투자 계좌 영역 (FR-16, FR-17) — 헤더 상단에 표시."""
    st.markdown("##### 🏦 KIS 모의투자 계좌")
    total_eval = port.cash + sum(
        current_prices.get(sym, pos.avg_price) * pos.shares
        for sym, pos in port.positions.items()
    )
    c_acct, c_cash, c_eval, c_sync = st.columns([1.2, 1, 1, 1])
    with c_acct:
        st.metric("계좌번호", _mask_account(config.KIS_ACCOUNT_NO))
    with c_cash:
        st.metric("USD 가용현금", f"${port.cash:,.2f}")
    with c_eval:
        st.metric("총 평가금액", f"${total_eval:,.2f}")
    with c_sync:
        st.write("")
        if st.button("🔄 KIS 동기화", use_container_width=True):
            try:
                synced, prices = kis_sync()
                if prices:
                    st.session_state["current_prices"] = prices
                st.success(f"KIS 동기화 완료 — {len(synced.positions)}개 포지션")
                st.rerun()
            except Exception as e:
                st.error(f"KIS 동기화 실패: {e}")
    st.divider()

def get_current_prices(symbols):
    prices = {}
    for symbol in symbols:
        price = collector.get_latest_open_price(symbol)
        if price:
            prices[symbol] = price
    return prices

def run_signals_now():
    with st.spinner("신호 계산 중..."):
        result = sig_module.generate_signals_for_all(config.SYMBOLS)
        if result:
            portfolio.save_signals(result)
            return True, "신호 계산 완료"
        return False, "신호 계산 실패"

def main():
    # --- Custom CSS for Layout & Cards ---
    st.markdown("""
        <style>
        .main { background-color: #fcfcfc; }
        .stMetric {
            background-color: #ffffff;
            padding: 10px;
            border-radius: 5px;
            border: 1px solid #eee;
        }
        [data-testid="stMetricValue"] { font-size: 1.5rem !important; }
        
        .stock-card {
            border-radius: 5px;
            padding: 10px;
            margin-bottom: 10px;
            background-color: white;
            transition: 0.3s;
        }
        .profit-pos { color: #2962ff; font-weight: bold; }
        .profit-neg { color: #212121; font-weight: bold; }
        .sub-text { color: #757575; font-size: 0.75rem; }
        .price-large { font-size: 2rem; font-weight: bold; }
        </style>
    """, unsafe_allow_html=True)

    # --- Header Area ---
    port = portfolio.load_portfolio()
    current_prices = st.session_state.get("current_prices", {})

    # --- KIS 모의투자 계좌 영역 (FR-16, FR-17) ---
    render_kis_panel(port, current_prices)

    col_nav, col_total_info = st.columns([3, 1])
    with col_nav:
        st.caption("포트폴리오 / 상세 현황")
        st.subheader(f"보유 주식 개요 ({len(port.positions)}개 포지션)")
    
    # 상단 총 미실현 수익 계산
    total_unrealized_profit = 0
    for sym, pos in port.positions.items():
        p = current_prices.get(sym, pos.avg_price)
        total_unrealized_profit += (p - pos.avg_price) * pos.shares
        
    with col_total_info:
        st.markdown(f"""
            <div style='text-align: right;'>
                <span class='sub-text'>총 미실현 수익</span><br>
                <h3 class='profit-pos'>+${total_unrealized_profit:,.2f}</h3>
                <span class='sub-text'>UTC {datetime.now().strftime('%H:%M:%S')}</span>
            </div>
        """, unsafe_allow_html=True)

    # --- 1. Top Horizontal Stock Cards ---
    if port.positions:
        symbols = list(port.positions.keys())
        card_cols = st.columns(max(len(symbols), 5))
        
        if "selected_symbol" not in st.session_state:
            st.session_state["selected_symbol"] = symbols[0]
            
        for i, sym in enumerate(symbols):
            pos = port.positions[sym]
            p = current_prices.get(sym, pos.avg_price)
            profit = (p - pos.avg_price) * pos.shares
            p_class = "profit-pos" if profit >= 0 else "profit-neg"
            
            with card_cols[i % 5]:
                is_selected = (sym == st.session_state["selected_symbol"])
                bg_color = "#eef4ff" if is_selected else "#ffffff"
                border_color = "#2962ff" if is_selected else "#e0e0e0"
                
                if st.button(f"{sym}", key=f"btn_{sym}", use_container_width=True):
                    st.session_state["selected_symbol"] = sym
                    st.rerun()
                
                st.markdown(f"""
                    <div style='background-color: {bg_color}; border: 1px solid {border_color}; padding: 8px; border-radius: 4px;'>
                        <div class='sub-text' style='font-weight: bold;'>{sym}.US</div>
                        <div style='font-size: 0.85rem; margin-bottom: 5px;'>{config.COMPANY_NAMES.get(sym, sym)}</div>
                        <div class='{p_class}' style='font-size: 1.1rem;'>{profit:+.0f}</div>
                        <div style='text-align: right; font-size: 0.7rem; color: #9e9e9e;'>{pos.shares:,} sh</div>
                    </div>
                """, unsafe_allow_html=True)
    else:
        st.info("보유 종목이 없습니다.")

    st.divider()

    # --- 2. Main Detail Section ---
    sel_sym = st.session_state.get("selected_symbol")
    if sel_sym and sel_sym in port.positions:
        pos = port.positions[sel_sym]
        price = current_prices.get(sel_sym, pos.avg_price)
        
        col_main, col_side = st.columns([2.2, 0.8])
        
        with col_main:
            c_title, c_price_info = st.columns([2, 1])
            with c_title:
                st.markdown(f"<h1 style='margin-bottom:0;'>{config.COMPANY_NAMES.get(sel_sym, sel_sym)}</h1>", unsafe_allow_html=True)
                st.caption("기술 / AI / 반도체")
                st.write("가격 변동 (24시간)")
            with c_price_info:
                st.markdown(f"""
                    <div style='text-align: right;'>
                        <span class='sub-text'>현재 가격</span><br>
                        <span class='price-large'>{price:,.2f}</span><br>
                        <span class='profit-pos'>+1.24 (0.67%)</span>
                    </div>
                """, unsafe_allow_html=True)
            
            chart_data = pd.DataFrame(np.random.randn(40, 1), columns=['Price'])
            st.bar_chart(chart_data, height=220, use_container_width=True)
            
            m_col1, m_col2, m_col3 = st.columns(3)
            with m_col1:
                st.write("명목 가치")
                st.subheader(f"${price * pos.shares:,.2f}")
            with m_col2:
                st.write("매입 단가")
                st.subheader(f"${pos.avg_price:,.2f}")
            with m_col3:
                st.write("미실현 수익")
                prof = (price - pos.avg_price) * pos.shares
                st.markdown(f"<h3 class='profit-pos'>+${prof:,.2f}</h3>", unsafe_allow_html=True)
            
            st.divider()
            st.write(f"{sel_sym} 최근 거래")
            trades = load_trades()
            if not trades.empty:
                st.dataframe(trades[trades['symbol'] == sel_sym].head(5), use_container_width=True)

        with col_side:
            st.markdown(f"""
                <div style='border: 1px dashed #bdbdbd; padding: 20px; border-radius: 8px; background-color: #fafafa;'>
                    <div class='sub-text' style='font-size: 0.9rem; margin-bottom: 20px;'>포지션 상태</div>
                    <div style='margin-bottom: 25px;'>
                        <span class='sub-text'>보유 수량</span><br>
                        <span style='font-size: 1.6rem; font-weight: bold;'>{pos.shares:,} 주</span>
                    </div>
                    <div style='margin-bottom: 25px;'>
                        <span class='sub-text'>시장 가치</span><br>
                        <span style='font-size: 1.6rem; font-weight: bold;'>${price * pos.shares:,.2f}</span>
                    </div>
                    <div style='margin-bottom: 25px;'>
                        <span class='sub-text'>수익률</span><br>
                        <span style='font-size: 1.6rem; font-weight: bold; color: #2962ff;'>+{(price - pos.avg_price) / pos.avg_price * 100:.2f}%</span>
                    </div>
                    <hr style='border-top: 1px solid #eee;'>
                    <div style='text-align: right; display: flex; align-items: center; justify-content: flex-end;'>
                        <span style='color: #2962ff; font-size: 0.8rem; margin-right: 5px;'>●</span>
                        <span style='color: #2962ff; font-size: 0.8rem;'>자동 거래 활성화</span>
                    </div>
                </div>
            """, unsafe_allow_html=True)
            
            st.write("")
            if st.button("현재가 새로고침", use_container_width=True):
                st.session_state["current_prices"] = get_current_prices(port.positions.keys())
                st.rerun()
            
            if st.button("실시간 신호 갱신", use_container_width=True):
                run_signals_now()
                st.rerun()

    st.divider()

    # --- 3. Full Trade History Section ---
    st.subheader("📜 전체 거래 내역")
    all_trades = load_trades()
    if not all_trades.empty:
        st.dataframe(
            all_trades.sort_values("date", ascending=False),
            use_container_width=True
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
                st.success(f"{new_sym} 포지션이 저장되었습니다.")
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
