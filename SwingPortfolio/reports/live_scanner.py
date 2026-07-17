"""
Live Scanner Dashboard — displays 2-min candle range scanner results.
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
    return "color: green" if val == "LONG" else "color: red"


def show():
    st.title("📡 Live Scanner")
    st.markdown("Real-time 2-min candle range scanner — Nifty 200 universe")

    # Auto-refresh
    auto_refresh = st.checkbox("Auto-refresh (30s)", value=True)
    if auto_refresh:
        st.rerun()

    # Load results
    if not os.path.exists(RESULTS_FILE):
        st.info("Scanner hasn't run yet. Results appear after the first scan cycle.")
        return

    with open(RESULTS_FILE) as f:
        data = json.load(f)

    last_upd = data.get("last_updated", "unknown")
    flagged = data.get("total_flagged", 0)
    scanned = data.get("scanned", 0)
    min_pct = data.get("min_range_pct", 2.0)
    results = data.get("results", [])

    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Scanned", scanned)
    col2.metric("Flagged (>{}%)".format(min_pct), flagged)
    col3.metric("Updated", last_upd.split()[-1] if " " in last_upd else last_upd)
    longs = len([r for r in results if r.get("direction") == "LONG"])
    shorts = len(results) - longs
    col4.metric("LONG / SHORT", f"{longs} / {shorts}")

    if not results:
        st.success(f"No stocks flagged with 2-min range > {min_pct}%")
        return

    # Build DataFrame
    df = pd.DataFrame(results)
    display_cols = ["symbol", "ltp", "prev_close", "high", "low", "range", "range_pct", "move_pct", "direction", "volume", "updated"]
    df = df[display_cols]

    # Styling
    styled = df.style.map(_color_range, subset=["range_pct"])
    st.dataframe(styled, use_container_width=True, hide_index=True)

    # Top movers detail
    st.subheader(f"🔥 Top Movers (>{min_pct}%)")
    top = df.head(10)

    for _, row in top.iterrows():
        emoji = "🟢" if row["direction"] == "LONG" else "🔴"
        with st.expander(f"{emoji} {row['symbol']} — {row['range_pct']:.1f}% range"):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("LTP", f"₹{row['ltp']:.2f}", f"{row['move_pct']:+.1f}%")
            c2.metric("Range", f"₹{row['range']:.2f} ({row['range_pct']:.1f}%)")
            c3.metric("High/Low", f"₹{row['high']:.2f} / ₹{row['low']:.2f}")
            c4.metric("Volume", row.get("volume", "?"))
