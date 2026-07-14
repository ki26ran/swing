import streamlit as st
import os, sys, json, time, io
import pandas as pd
from contextlib import redirect_stdout

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
ROOT = os.path.dirname(BASE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.registry import get_strategy_names

DATA_DIR = os.path.join(BASE, "data")
SEL_FILE = os.path.join(DATA_DIR, "selections.json")

CSS = """
<style>
    .strat-header {
        border-radius: 8px; padding: 12px 16px; margin: 12px 0 8px;
        border-left: 4px solid; color: #e0e0e0;
    }
    .strat-header h3 { margin: 0; font-size: 1.05rem; }
    .strat-header p { margin: 2px 0 0; font-size: 0.8rem; opacity: 0.7; }
    .tag-long { color: #00e676; font-weight: 600; }
    .tag-short { color: #ff5252; font-weight: 600; }
</style>
"""

STRAT_COLORS = {
    "donchian_adx": {"bg": "#1a3a5c", "border": "#2a6fb0", "label": "Donchian + ADX"},
    "keltner_rsi": {"bg": "#3d1a0a", "border": "#b05a2a", "label": "Keltner + RSI + Volume"},
    "supertrend_volume": {"bg": "#3d0a3d", "border": "#b02ab0", "label": "Supertrend + Volume"},
}


def _load_all_selections():
    """Load selections from both old multi-strategy file and per-strategy files."""
    results = {}

    # Per-strategy files (new)
    strategy_ids = ["donchian_adx", "keltner_rsi", "supertrend_volume"]
    latest_date = ""
    for sid in strategy_ids:
        fpath = os.path.join(DATA_DIR, f"selections_{sid}.json")
        if os.path.exists(fpath):
            try:
                with open(fpath) as f:
                    data = json.load(f)
                results[sid] = data.get("data", [])
                if data.get("scan_date", ""):
                    latest_date = data["scan_date"]
            except Exception:
                results[sid] = []

    # Fallback: legacy multi-strategy file
    if not results and os.path.exists(SEL_FILE):
        try:
            with open(SEL_FILE) as f:
                data = json.load(f)
            results = data.get("results", {})
            latest_date = data.get("scan_date", data.get("date", ""))
        except Exception:
            pass

    return results, latest_date


def show():
    st.title("SwingPortfolio — Stock Selection v2")
    st.markdown("Latest scan results from the 8:30 AM daily scan.")
    st.markdown(CSS, unsafe_allow_html=True)

    names = get_strategy_names()

    col1, col2 = st.columns([3, 1])
    with col1:
        results, scan_date = _load_all_selections()
    with col2:
        if st.button("Run Scan Now", type="primary", help="Triggers per-strategy scans"):
            from common.agents.strategy_agent import run_scan
            strategy_ids = ["donchian_adx", "keltner_rsi", "supertrend_volume"]
            total = len(strategy_ids)
            for i, sid in enumerate(strategy_ids):
                with st.status(f"Scanning {sid} ({i+1}/{total})...", expanded=True) as status_box:
                    buf = io.StringIO()
                    try:
                        with redirect_stdout(buf):
                            run_scan("SwingPortfolio", sid)
                        output = buf.getvalue()
                        if output.strip():
                            st.code(output[-2000:])
                        status_box.update(label=f"{sid} complete", state="complete", expanded=False)
                    except Exception as e:
                        st.error(str(e))
                        status_box.update(label=f"{sid} failed", state="error")
            st.success(f"All {total} scans complete — refreshing...")
            time.sleep(0.5)
            st.rerun()

    if not results:
        st.info("No selections file found. Click 'Run Scan Now' to trigger scans, or wait for the 8:30 AM scheduled scan.")
        return

    if scan_date:
        st.metric("Scan Date", scan_date)

    total_signals = 0
    for sid, sigs in results.items():
        sname = names.get(sid, sid)
        c = STRAT_COLORS.get(sid, {"bg": "#1a1a2e", "border": "#555", "label": sname})
        n = len(sigs) if isinstance(sigs, list) else 0

        st.markdown(
            f'<div class="strat-header" style="background:{c["bg"]};border-color:{c["border"]}">'
            f'<h3>{c["label"]} — {n} signal{"s" if n != 1 else ""}</h3>'
            f'<p>{sname}</p></div>',
            unsafe_allow_html=True,
        )

        if sigs and isinstance(sigs, list):
            df = pd.DataFrame(sigs)
            col_order = ["symbol", "direction", "fill_price", "fill_time",
                         "option_symbol", "entry_price", "signal_date",
                         "close_price", "filled"]
            avail = [c for c in col_order if c in df.columns]
            extra = [c for c in df.columns if c not in col_order]
            display_cols = avail + extra
            df = df[display_cols] if all(c in df.columns for c in display_cols) else df

            def _color_row(row):
                styles = [""] * len(row)
                for i, val in enumerate(row):
                    if isinstance(val, str) and val == "LONG":
                        styles[i] = "color: #00e676; font-weight: bold"
                    elif isinstance(val, str) and val == "SHORT":
                        styles[i] = "color: #ff5252; font-weight: bold"
                    elif val is True:
                        styles[i] = "color: #00e676"
                    elif val is False:
                        styles[i] = "color: #666"
                return styles

            styled = df.style.apply(_color_row, axis=1)
            st.dataframe(styled, use_container_width=True, hide_index=True)
            total_signals += n
        else:
            st.info("No signals")

    st.caption(f"Total: {total_signals} signals across {len(results)} strategies")
