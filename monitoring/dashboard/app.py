import json
import os
from datetime import datetime
import pytz
import streamlit as st
import plotly.graph_objects as go
import redis

st.set_page_config(
    page_title="Trading System Monitor",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

DARK_CSS = """
<style>
    .stApp { background-color: #0d1117; color: #c9d1d9; }
    .block-container { padding-top: 1rem; }
    div[data-testid="metric-container"] { background: #161b22; border-radius: 8px; padding: 12px; border: 1px solid #30363d; }
    .positive { color: #00ff88 !important; }
    .negative { color: #ff4757 !important; }
    .warning-text { color: #ffa502 !important; }
    .stSelectbox label { color: #c9d1d9; }
</style>
"""
st.markdown(DARK_CSS, unsafe_allow_html=True)

IST = pytz.timezone("Asia/Kolkata")


@st.cache_resource
def get_redis_client():
    try:
        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", 0)),
            decode_responses=True,
            socket_connect_timeout=2,
        )
        r.ping()
        return r
    except Exception:
        return None


def safe_redis_scan(r, pattern: str) -> list:
    try:
        return list(r.scan_iter(pattern))
    except Exception:
        return []


def get_all_pnls(r) -> dict:
    result = {}
    for key in safe_redis_scan(r, "pnl:*"):
        strategy_id = key.split(":", 1)[1]
        data = r.get(key)
        if data:
            try:
                result[strategy_id] = json.loads(data)
            except Exception:
                pass
    return result


def get_all_positions(r) -> list:
    result = []
    for key in safe_redis_scan(r, "position:*:*"):
        parts = key.split(":")
        if len(parts) < 3:
            continue
        strategy_id, symbol = parts[1], parts[2]
        data = r.get(key)
        if data:
            try:
                pos = json.loads(data)
                pos["strategy_id"] = strategy_id
                pos["symbol"] = symbol
                result.append(pos)
            except Exception:
                pass
    return result


PLOTLY_LAYOUT = dict(
    paper_bgcolor="#161b22",
    plot_bgcolor="#0d1117",
    font=dict(color="#c9d1d9"),
    margin=dict(l=40, r=40, t=40, b=40),
)

page = st.sidebar.selectbox(
    "Navigation",
    ["📊 Live PnL", "📋 Positions", "🛡️ Risk Dashboard", "🔥 Signal Heatmap", "⚡ Execution Quality"],
)

r = get_redis_client()

if r is None:
    st.error("Cannot connect to Redis. Ensure Redis is running on localhost:6379.")
    st.stop()

# ──────────────────────────────────────────────────────────────────────────────
if page == "📊 Live PnL":
    st.title("Live PnL Monitor")

    pnl_data = get_all_pnls(r)
    total_realized = sum(v.get("realized_pnl", 0) for v in pnl_data.values())
    total_unrealized = sum(v.get("unrealized_pnl", 0) for v in pnl_data.values())
    total_pnl = total_realized + total_unrealized
    kill_active = r.get("kill_switch:active") == "true"

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Total PnL", f"₹{total_pnl:,.0f}", delta=f"₹{total_unrealized:,.0f} unrealized")
    with c2:
        st.metric("Realized PnL", f"₹{total_realized:,.0f}")
    with c3:
        st.metric("Active Strategies", str(len(pnl_data)))
    with c4:
        status = "🔴 KILL SWITCH ON" if kill_active else "🟢 TRADING"
        st.metric("System Status", status)

    if pnl_data:
        strategies = list(pnl_data.keys())
        pnls = [v.get("realized_pnl", 0) + v.get("unrealized_pnl", 0) for v in pnl_data.values()]
        colors = ["#00ff88" if p >= 0 else "#ff4757" for p in pnls]
        fig = go.Figure(go.Bar(x=strategies, y=pnls, marker_color=colors, name="PnL"))
        fig.update_layout(title="PnL by Strategy", **PLOTLY_LAYOUT)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No strategy PnL data yet. Start the system to see live data.")

    col_left, col_right = st.columns([3, 1])
    with col_right:
        if st.button("🔄 Refresh"):
            st.rerun()
    st.caption(f"Last updated: {datetime.now(IST).strftime('%H:%M:%S IST')}")

# ──────────────────────────────────────────────────────────────────────────────
elif page == "📋 Positions":
    st.title("Current Positions")
    positions = get_all_positions(r)

    if not positions:
        st.info("No open positions. System is flat.")
    else:
        import pandas as pd
        df = pd.DataFrame(positions)
        st.dataframe(df, use_container_width=True)

    if st.button("🔄 Refresh"):
        st.rerun()

# ──────────────────────────────────────────────────────────────────────────────
elif page == "🛡️ Risk Dashboard":
    st.title("Risk Dashboard")

    max_drawdown_pct = 5.0
    max_gross_inr = 2_000_000

    pnl_data = get_all_pnls(r)
    total_pnl = sum(v.get("total_pnl", 0) for v in pnl_data.values())
    starting_capital = 1_000_000
    drawdown_pct = max(0.0, -total_pnl / starting_capital * 100)

    positions = get_all_positions(r)
    gross_exposure = sum(abs(p.get("qty", 0)) * p.get("avg_price", 0) for p in positions)

    kill_active = r.get("kill_switch:active") == "true"
    kill_reason = r.get("kill_switch:reason") or "N/A"

    if kill_active:
        st.error(f"🚨 KILL SWITCH ACTIVE — {kill_reason}")
    else:
        st.success("✅ Kill switch inactive — system is trading")

    c1, c2 = st.columns(2)
    with c1:
        dd_util = min(100, drawdown_pct / max_drawdown_pct * 100)
        fig = go.Figure(go.Indicator(
            mode="gauge+number+delta",
            value=dd_util,
            title={"text": "Daily Drawdown Utilization %"},
            delta={"reference": 80},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "#ff4757" if dd_util > 85 else ("#ffa502" if dd_util > 60 else "#00ff88")},
                "steps": [
                    {"range": [0, 60], "color": "#162032"},
                    {"range": [60, 85], "color": "#2d2016"},
                    {"range": [85, 100], "color": "#2d1616"},
                ],
                "threshold": {"line": {"color": "white", "width": 2}, "thickness": 0.75, "value": 80},
            }
        ))
        fig.update_layout(**PLOTLY_LAYOUT, height=300)
        st.plotly_chart(fig, use_container_width=True)

    with c2:
        exp_util = min(100, gross_exposure / max_gross_inr * 100)
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=exp_util,
            title={"text": "Gross Exposure Utilization %"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "#ff4757" if exp_util > 85 else ("#ffa502" if exp_util > 60 else "#00ff88")},
            }
        ))
        fig.update_layout(**PLOTLY_LAYOUT, height=300)
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Portfolio Greeks")
    greeks = {k: r.get(f"portfolio:net_{k}") or "0" for k in ["delta", "gamma", "vega", "theta"]}
    gc1, gc2, gc3, gc4 = st.columns(4)
    gc1.metric("Net Delta", f"{float(greeks['delta']):.1f}")
    gc2.metric("Net Gamma", f"{float(greeks['gamma']):.3f}")
    gc3.metric("Net Vega", f"{float(greeks['vega']):.0f}")
    gc4.metric("Net Theta", f"{float(greeks['theta']):.0f}")

    if st.button("🔄 Refresh"):
        st.rerun()

# ──────────────────────────────────────────────────────────────────────────────
elif page == "🔥 Signal Heatmap":
    st.title("Signal Strength Heatmap — OFI")

    symbols = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "WIPRO", "SBIN", "BAJFINANCE", "KOTAKBANK", "HINDUNILVR", "NIFTY50", "BANKNIFTY"]
    signal_values = []
    for sym in symbols:
        ofi = r.get(f"features:{sym}:ofi_1s")
        signal_values.append(float(ofi) if ofi else 0.0)

    max_val = max(abs(v) for v in signal_values) or 1.0
    normalized = [v / max_val for v in signal_values]

    n_cols = 4
    n_rows = (len(symbols) + n_cols - 1) // n_cols
    grid = []
    label_grid = []
    for i in range(n_rows):
        row = normalized[i * n_cols:(i + 1) * n_cols]
        lrow = symbols[i * n_cols:(i + 1) * n_cols]
        while len(row) < n_cols:
            row.append(0.0)
            lrow.append("")
        grid.append(row)
        label_grid.append(lrow)

    fig = go.Figure(go.Heatmap(
        z=grid,
        text=label_grid,
        texttemplate="%{text}",
        colorscale=[[0, "#ff4757"], [0.5, "#161b22"], [1, "#00ff88"]],
        zmin=-1, zmax=1,
        showscale=True,
    ))
    fig.update_layout(
        title="OFI Signal (green=buy pressure, red=sell pressure)",
        **PLOTLY_LAYOUT,
        height=400,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"Updated: {datetime.now(IST).strftime('%H:%M:%S IST')}")

    col_data = []
    for sym, val, norm_val in zip(symbols, signal_values, normalized):
        col_data.append({"Symbol": sym, "OFI (raw)": round(val, 6), "Signal": round(norm_val, 3)})
    if col_data:
        import pandas as pd
        st.dataframe(pd.DataFrame(col_data), use_container_width=True)

    if st.button("🔄 Refresh"):
        st.rerun()

# ──────────────────────────────────────────────────────────────────────────────
elif page == "⚡ Execution Quality":
    st.title("Execution Quality")

    total_commission = float(r.get("total_commission_paid") or 0)
    total_slippage = float(r.get("total_slippage_cost_inr") or 0)

    c1, c2 = st.columns(2)
    c1.metric("Total Commission Paid", f"₹{total_commission:,.2f}")
    c2.metric("Total Slippage Cost", f"₹{total_slippage:,.2f}")

    st.info("Slippage and latency breakdown will populate as fills arrive.")

    if st.button("🔄 Refresh"):
        st.rerun()
