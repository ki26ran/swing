import streamlit as st
import pandas as pd
import os, sys, json
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, os.path.dirname(BASE_DIR))
    
from core.registry import get_all_strategies, load_config
from core.atomic import load_json
from cfg.settings import CAPITAL_TOTAL, MAX_POSITIONS_TOTAL
from cfg.universes import get_lot_size


def show():
    st.title("📡 Live Monitor")
    st.markdown("Real-time view of positions, signals, and strategy status.")
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Strategies", len(get_all_strategies()))
    
    # Load current positions
    pos_path = os.path.join(BASE_DIR, "data", "live_positions.json")
    if os.path.exists(pos_path):
        with open(pos_path) as f:
            positions = json.load(f)
        col2.metric("Open Positions", len(positions))
        
        total_pnl = 0
        rows = []
        for sym, p in positions.items():
            qty = int(p.get("qty", 0))
            entry = float(p.get("entry_price", 0))
            ltp = float(p.get("last_price", entry))
            pnl = (ltp - entry) * qty if p.get("direction") == "LONG" else (entry - ltp) * qty
            total_pnl += pnl
            rows.append({
                "Symbol": sym, "Dir": p.get("direction", ""),
                "Qty": qty, "Entry": f"{entry:.2f}",
                "LTP": f"{ltp:.2f}", "P&L": f"{pnl:+,.0f}",
                "Strategy": p.get("strategy", "")
            })
        
        col3.metric("Unrealized P&L", f"₹{total_pnl:+,.0f}")
        col4.metric("Used Capital", f"₹{len(positions) * 50000:+,.0f}")
        
        if rows:
            st.subheader("📂 Open Positions")
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No open positions found.")
    
    # Strategy status
    st.subheader("⚡ Strategy Status")
    cfg_list = load_config().get("strategies", [])
    strat_map = {s.get("id"): s for s in cfg_list}
    for s in get_all_strategies():
        sid = s.strategy_id() if hasattr(s, "strategy_id") else str(s)
        sc = strat_map.get(sid, {})
        enabled = sc.get("enabled", True)
        status = "✅" if enabled else "⛔"
        st.write(f"{status} **{s.name() if hasattr(s, 'name') else sid}**")
    
    # Recent signals
    st.subheader("📊 Recent Signals")
    signals_path = os.path.join(BASE_DIR, "data", "signals.csv")
    if os.path.exists(signals_path):
        try:
            sig_df = pd.read_csv(signals_path)
            st.dataframe(sig_df.tail(20), use_container_width=True)
        except Exception:
            st.info("No signals recorded yet.")
    else:
        st.info("No signals file found.")
