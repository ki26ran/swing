"""
Live Scanner Dashboard — shows 2-min range scanner + 15-min breakout signals.
Auto-refreshes every 30 seconds.
"""
import streamlit as st
import pandas as pd
import os, json
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_FILE = os.path.join(BASE_DIR, "data", "live_scanner_results.json")


def _color_range(val):
    try:
        v = float(val)
        if v >= 5: return "color: red; font-weight: bold"
        if v >= 3: return "color: orange; font-weight: bold"
        return "color: green"
    except:
        return ""


def _color_dir(val):
    return "color: green" if "LONG" in str(val) else "color: red"


def show():
    st.title("📡 Live Scanner")
    st.markdown("High-volatility stock scanner — 2-min candles & 15-min breakouts")

    # Auto-refresh via query param
    auto = st.checkbox("Auto-refresh (30s)", value=True)
    if auto:
        st.rerun()

    if not os.path.exists(RESULTS_FILE):
        st.info("Scanner hasn't run yet. Results appear after the first scan.")
        return

    with open(RESULTS_FILE) as f:
        data = json.load(f)

    last_upd = data.get("last_updated", "unknown")
    scanned = data.get("scanned", 0)
    scanner_data = data.get("scanner", {})
    breakout_data = data.get("breakout_15m", {})

    tab1, tab2 = st.tabs(["⚡ 2-min Range Scanner", "📊 15-min Breakout Signals"])

    # ── Tab 1: Original 2-min scanner ──
    with tab1:
        results = scanner_data.get("results", [])
        st.subheader(f"2-min Range Scanner (>{scanner_data.get('min_range_pct', 2)}%)")
        col1, col2, col3 = st.columns(3)
        col1.metric("Scanned", scanned)
        col2.metric("Flagged", scanner_data.get("total_flagged", 0))
        col3.metric("Updated", last_upd.split()[-1] if " " in last_upd else last_upd)

        if results:
            df = pd.DataFrame(results)
            cols = ["symbol", "ltp", "range_pct", "range", "direction", "high", "low", "volume", "updated"]
            df = df[[c for c in cols if c in df.columns]]
            styled = df.style.map(_color_range, subset=["range_pct"])
            st.dataframe(styled, use_container_width=True, hide_index=True)
        else:
            st.success("No stocks flagged — market is calm")

    # ── Tab 2: 15-min Breakout Signals ──
    with tab2:
        signals = breakout_data.get("results", [])
        st.subheader(f"15-min Range Breakout Signals ({len(signals)} active)")
        st.caption("Entry / SL / Target levels for high-volatility stocks only")
        st.caption("**Risk:** 0.5x range | **Target:** 1.0x range | **RR:** 2.0")

        if signals:
            for s in signals:
                emoji = "🟢" if "LONG" in s.get("type", "") else "🔴"
                with st.expander(f"{emoji} {s['symbol']} — {s['type']} @ ₹{s['entry']:.1f}"):
                    c1, c2, c3, c4, c5 = st.columns(5)
                    c1.metric("Entry", f"₹{s['entry']:.2f}")
                    c2.metric("SL", f"₹{s['sl']:.2f}", delta=f"-₹{s['span']:.1f}" if s.get("span") else None)
                    c3.metric("Target", f"₹{s['target']:.2f}", delta=f"+₹{abs(s['target']-s['entry']):.1f}")
                    c4.metric("LTP", f"₹{s['ltp']:.2f}")
                    c5.metric("15m Range", f"{s.get('r15_range_pct', 0):.1f}%")

            # Table view
            st.subheader("All Signals")
            df2 = pd.DataFrame(signals)
            cols2 = ["symbol", "type", "entry", "sl", "target", "span", "ltp", "r15_range_pct", "r15_high", "r15_low", "time"]
            df2 = df2[[c for c in cols2 if c in df2.columns]]
            styled2 = df2.style.map(_color_dir, subset=["type"])
            st.dataframe(styled2, use_container_width=True, hide_index=True)
        else:
            st.info("No breakout signals at this time. Waiting for price to breach 15-min range on high-vol stocks.")

    # Sidebar info
    st.sidebar.markdown("### High-Volatility Universe")
    st.sidebar.markdown("40 stocks with highest hourly volatility")
    st.sidebar.markdown("**Breakout Strategy:**")
    st.sidebar.markdown("- Rolling 15-min range")
    st.sidebar.markdown("- Entry at range break")
    st.sidebar.markdown("- SL: 0.5x range")
    st.sidebar.markdown("- Target: 1.0x range (RR 2:1)")
