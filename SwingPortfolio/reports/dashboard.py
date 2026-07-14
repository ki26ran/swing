import streamlit as st
import pandas as pd
import numpy as np
import os, sys, json, time, io
from datetime import datetime
from contextlib import redirect_stdout

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
# Add root for common module
COMMON = os.path.dirname(BASE)
if COMMON not in sys.path:
    sys.path.insert(0, COMMON)

from common.market_data.cache import get_cache
from core.registry import get_all_strategies, get_strategy_names
from common.market_data.scan_logger import query_scan_history, query_signal_details

DATA_DIR = os.path.join(BASE, "data")
POS_FILE = os.path.join(DATA_DIR, "live_positions.json")
HISTORY_FILE = os.path.join(DATA_DIR, "positions_history.json")
SEL_FILE = os.path.join(DATA_DIR, "selections.json")

STRAT_COLORS = {
    "donchian_adx": {"bg": "#1a3a5c", "border": "#2a6fb0"},
    "keltner_rsi": {"bg": "#3d1a0a", "border": "#b05a2a"},
    "supertrend_volume": {"bg": "#3d0a3d", "border": "#b02ab0"},
}

CSS = """
<style>
    .strat-card {
        border-radius: 8px; padding: 12px 16px; margin: 8px 0;
        border-left: 4px solid; color: #e0e0e0;
    }
    .strat-card h4 { margin: 0 0 4px 0; font-size: 1rem; }
    .strat-card p { margin: 0; font-size: 0.85rem; opacity: 0.8; }
    .pos-card {
        border-radius: 6px; padding: 10px 14px; margin: 4px 0;
        border: 1px solid #333; background: #1e1e1e;
    }
    .pos-symbol { font-weight: 700; font-size: 1.05rem; }
    .tag-long { color: #00e676; font-weight: 600; }
    .tag-short { color: #ff5252; font-weight: 600; }
    .tag-open { color: #ffd740; }
    .tag-closed { color: #90a4ae; }
    .pnl-positive { color: #00e676; font-weight: 700; }
    .pnl-negative { color: #ff5252; font-weight: 700; }
    .pnl-neutral { color: #90a4ae; }
    .metric-card {
        background: #1a1a2e; border-radius: 8px; padding: 16px;
        text-align: center; border: 1px solid #333;
    }
    .summary-row {
        display: flex; gap: 12px; flex-wrap: wrap;
    }
    .dir-icon { font-size: 0.9rem; margin-right: 4px; }
</style>
"""

def _fmt_pnl(pnl):
    if pnl is None or (isinstance(pnl, float) and np.isnan(pnl)):
        pnl = 0
    cls = "pnl-positive" if pnl > 0 else ("pnl-negative" if pnl < 0 else "pnl-neutral")
    return f'<span class="{cls}">Rs.{pnl:+,.0f}</span>'

def _fmt_dir(d):
    cls = "tag-long" if d == "LONG" else "tag-short"
    icon = "▲" if d == "LONG" else "▼"
    return f'<span class="{cls}">{icon} {d}</span>'


def show():
    st.title("SwingPortfolio Dashboard")
    st.markdown(CSS, unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Run Stock Selection", type="primary", help="Run all strategy scans now"):
            from common.agents.strategy_agent import run_scan
            strategy_ids = ["donchian_adx", "keltner_rsi", "supertrend_volume"]
            for i, sid in enumerate(strategy_ids):
                with st.status(f"Scanning {sid} ({i+1}/{len(strategy_ids)})...", expanded=True) as sb:
                    buf = io.StringIO()
                    try:
                        with redirect_stdout(buf):
                            run_scan("SwingPortfolio", sid)
                        output = buf.getvalue()
                        if output.strip():
                            st.code(output[-1500:])
                        sb.update(label=f"{sid} done", state="complete", expanded=False)
                    except Exception as e:
                        st.error(str(e))
                        sb.update(label=f"{sid} failed", state="error")
            st.success("All scans complete")
            time.sleep(0.5)
            st.rerun()
    with col2:
        st.caption(f"Loaded: {datetime.now().strftime('%H:%M:%S')}")
    with col3:
        if st.button("Refresh Prices", help="Reload live prices"):
            st.rerun()

    st.divider()

    strategies = list(get_all_strategies())
    names = get_strategy_names()

    st.subheader(f"Active Strategies ({len(strategies)})")
    if strategies:
        cols = st.columns(len(strategies))
        for i, s in enumerate(strategies):
            c = STRAT_COLORS.get(s.strategy_id, {"bg": "#1a1a2e", "border": "#555"})
            with cols[i]:
                st.markdown(
                    f'<div class="strat-card" style="background:{c["bg"]};border-color:{c["border"]}">'
                    f'<h4>{s.name}</h4><p>{s.description}</p></div>',
                    unsafe_allow_html=True,
                )
    else:
        st.info("All strategies are disabled. Enable them in Admin > Strategies.")

    st.divider()

    # Load positions from unified file (written by live_trader.py)
    pos_list = []
    if os.path.exists(POS_FILE):
        try:
            with open(POS_FILE) as f:
                legacy = json.load(f)
                if isinstance(legacy, list):
                    pos_list.extend(legacy)
                elif isinstance(legacy, dict):
                    pos_list.extend(legacy.values())
        except Exception:
            pass

    today_str = datetime.now().strftime("%Y-%m-%d")
    open_pos = [p for p in pos_list if p.get("status", "").lower() == "open"]
    closed_today = [p for p in pos_list if p.get("status", "").lower() == "closed"
                    and p.get("entry_date", "").startswith(today_str)]

    # --- Strategy Summary Table ---
    strategy_stats = {}
    for p in pos_list:
        sid = p.get("strategy", "unknown")
        if sid not in strategy_stats:
            strategy_stats[sid] = {"open": 0, "closed": 0, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "wins": 0, "losses": 0, "win_pnls": [], "loss_pnls": []}
        if p.get("status", "").lower() == "open":
            strategy_stats[sid]["open"] += 1
            sym = p.get("symbol", "")
            ep = float(p.get("entry_price", 0))
            qty = int(p.get("qty", 1))
            direction = p.get("direction", "LONG")
            ltp = ep
            try:
                cache_px = get_cache()
                price = cache_px.get_last_price(sym)
                if price is not None:
                    ltp = price
            except Exception:
                pass
            upnl = round((ltp - ep) * qty, 2) if direction == "LONG" else round((ep - ltp) * qty, 2)
            upnl = 0.0 if (isinstance(upnl, float) and np.isnan(upnl)) or upnl is None else upnl
            strategy_stats[sid]["unrealized_pnl"] += upnl
        elif p.get("entry_date", "").startswith(today_str):
            strategy_stats[sid]["closed"] += 1
            pnl = float(p.get("pnl", 0)) if p.get("pnl") is not None else 0
            strategy_stats[sid]["realized_pnl"] += pnl
            if pnl > 0:
                strategy_stats[sid]["wins"] += 1
                strategy_stats[sid]["win_pnls"].append(pnl)
            else:
                strategy_stats[sid]["losses"] += 1
                strategy_stats[sid]["loss_pnls"].append(pnl)

    if strategy_stats:
        st.divider()
        st.subheader("Strategy Summary")
        summary_rows = []
        grand_open = 0; grand_closed = 0; grand_realized = 0.0; grand_unrealized = 0.0
        grand_wins = 0; grand_losses = 0; grand_win_pnls = []; grand_loss_pnls = []
        for sid, sname in sorted(names.items()):
            if sid not in strategy_stats:
                continue
            s = strategy_stats[sid]
            grand_open += s["open"]; grand_closed += s["closed"]
            grand_realized += s["realized_pnl"]; grand_unrealized += s["unrealized_pnl"]
            grand_wins += s["wins"]; grand_losses += s["losses"]
            grand_win_pnls.extend(s["win_pnls"]); grand_loss_pnls.extend(s["loss_pnls"])
            total_s = s["realized_pnl"] + s["unrealized_pnl"]
            wr = s["wins"] / (s["wins"] + s["losses"]) * 100 if (s["wins"] + s["losses"]) > 0 else None
            avg_win = sum(s["win_pnls"]) / len(s["win_pnls"]) if s["win_pnls"] else None
            avg_loss = sum(s["loss_pnls"]) / len(s["loss_pnls"]) if s["loss_pnls"] else None
            summary_rows.append({
                "Strategy": sname, "Open": s["open"], "Closed": s["closed"],
                "Realized ₹": s["realized_pnl"], "Unrealized ₹": s["unrealized_pnl"],
                "Total ₹": total_s, "Wins": s["wins"], "Losses": s["losses"],
                "Win Rate": f"{wr:.0f}%" if wr is not None else "—",
                "Avg Win ₹": f"{avg_win:+,.2f}" if avg_win is not None else "—",
                "Avg Loss ₹": f"{avg_loss:+,.2f}" if avg_loss is not None else "—",
            })
        grand_total = grand_realized + grand_unrealized
        grand_wr = grand_wins / (grand_wins + grand_losses) * 100 if (grand_wins + grand_losses) > 0 else None
        grand_avg_win = sum(grand_win_pnls) / len(grand_win_pnls) if grand_win_pnls else None
        grand_avg_loss = sum(grand_loss_pnls) / len(grand_loss_pnls) if grand_loss_pnls else None
        summary_rows.append({
            "Strategy": "**TOTAL**", "Open": grand_open, "Closed": grand_closed,
            "Realized ₹": grand_realized, "Unrealized ₹": grand_unrealized,
            "Total ₹": grand_total, "Wins": grand_wins, "Losses": grand_losses,
            "Win Rate": f"{grand_wr:.0f}%" if grand_wr is not None else "—",
            "Avg Win ₹": f"{grand_avg_win:+,.2f}" if grand_avg_win is not None else "—",
            "Avg Loss ₹": f"{grand_avg_loss:+,.2f}" if grand_avg_loss is not None else "—",
        })
        sdf = pd.DataFrame(summary_rows)
        styled_s = sdf.style.format({
            "Realized ₹": "{:+,.2f}", "Unrealized ₹": "{:+,.2f}", "Total ₹": "{:+,.2f}",
        }).map(
            lambda v: "color: #00e676; font-weight: bold" if isinstance(v, (int, float)) and v > 0
            else "color: #ff5252; font-weight: bold" if isinstance(v, (int, float)) and v < 0 else "",
            subset=["Realized ₹", "Unrealized ₹", "Total ₹"],
        ).map(
            lambda v: "background-color: #1a1a3e; font-weight: bold" if isinstance(v, str) and "TOTAL" in v else "",
        )
        st.dataframe(styled_s, use_container_width=True, hide_index=True)

    st.divider()

    # --- Open Positions by Strategy ---
    st.subheader(f"Open Positions ({len(open_pos)})")

    if len(open_pos) > 0:
        by_strat = {}
        for p in open_pos:
            sid = p.get("strategy", "unknown")
            by_strat.setdefault(sid, []).append(p)

        total_upnl = 0.0
        for sid in sorted(by_strat.keys()):
            positions = by_strat[sid]
            sname = names.get(sid, sid)
            c = STRAT_COLORS.get(sid, {"bg": "#1a1a2e", "border": "#555"})

            spnl = 0
            pos_html = ""
            for p in positions:
                sym = p.get("symbol", "")
                direction = p.get("direction", "LONG")
                ep = float(p.get("entry_price", 0))
                qty = int(p.get("qty", 1))
                sl = float(p.get("trail_sl", p.get("initial_sl", 0)))
                target = p.get("target", 0)

                ltp = ep
                try:
                    cache = get_cache()
                    price = cache.get_last_price(sym)
                    if price is not None:
                        ltp = price
                except Exception:
                    pass

                upnl = round((ltp - ep) * qty, 0) if direction == "LONG" else round((ep - ltp) * qty, 0)
                upnl = 0 if np.isnan(upnl) or upnl is None else upnl
                spnl += upnl
                total_upnl += upnl

                entry_date = p.get("entry_date", "")
                entry_time = p.get("entry_time", "")
                # entry_time is full datetime; extract just the time part
                entry_time_only = entry_time.split()[-1] if " " in entry_time else ""
                pos_html += (
                    f'<div class="pos-card">'
                    f'<div style="display:flex;justify-content:space-between;align-items:center">'
                    f'<span class="pos-symbol">{sym}</span>'
                    f'<span>{_fmt_dir(direction)}</span>'
                    f'</div>'
                    f'<div style="display:flex;justify-content:space-between;font-size:0.85rem;margin-top:4px;color:#aaa">'
                    f'<span>Entry: ₹{ep:,.2f}</span>'
                    f'<span>LTP: ₹{ltp:,.2f}</span>'
                    f'<span>SL: ₹{sl:,.2f}</span>'
                    f'<span>Qty: {qty}</span>'
                    f'<span>P&L: {_fmt_pnl(upnl)}</span>'
                    f'</div>'
                    f'<div style="font-size:0.8rem;margin-top:2px;color:#777">'
                    f'{entry_date} {entry_time_only}'
                    f'</div></div>'
                )

            st.markdown(
                f'<div class="strat-card" style="background:{c["bg"]};border-color:{c["border"]}">'
                f'<h4>{sname} ({len(positions)} open)</h4>'
                f'{pos_html}'
                f'<div style="text-align:right;margin-top:8px;font-weight:700;font-size:1rem">'
                f'Subtotal: {_fmt_pnl(spnl)}</div></div>',
                unsafe_allow_html=True,
            )

        st.markdown(
            f'<div style="text-align:right;font-size:1.2rem;font-weight:700;padding:12px;'
            f'background:#1a1a2e;border-radius:8px;border:1px solid #333;margin-top:8px">'
            f'Total Unrealized P&L: {_fmt_pnl(total_upnl)}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.info("No open positions")

    st.divider()

    # --- Today's Closed Positions by Strategy ---
    st.subheader(f"Today's Closed Positions ({len(closed_today)})")
    if closed_today:
        by_strat = {}
        for p in closed_today:
            sid = p.get("strategy", "unknown")
            by_strat.setdefault(sid, []).append(p)

        total_rpnl = 0
        for sid in sorted(by_strat.keys()):
            positions = by_strat[sid]
            sname = names.get(sid, sid)
            c = STRAT_COLORS.get(sid, {"bg": "#1a1a2e", "border": "#555"})

            spnl = sum(float(p.get("pnl", 0)) for p in positions if not np.isnan(float(p.get("pnl", 0))))
            total_rpnl += spnl

            pos_rows = []
            for p in positions:
                sym = p.get("symbol", "")
                d = p.get("direction", "")
                ep = float(p.get("entry_price", 0))
                ex = float(p.get("exit_price", 0))
                rsn = p.get("exit_reason", "")
                pnl = float(p.get("pnl", 0))
                pos_rows.append(
                    f'<div class="pos-card" style="display:flex;justify-content:space-between;align-items:center">'
                    f'<span class="pos-symbol">{sym}</span>'
                    f'{_fmt_dir(d)} '
                    f'<span style="color:#aaa">Entry: ₹{ep:,.2f}</span>'
                    f'<span style="color:#aaa">Exit: ₹{ex:,.2f}</span>'
                    f'<span style="color:#aaa;font-size:0.8rem">{rsn}</span>'
                    f'{_fmt_pnl(pnl)}'
                    f'</div>'
                )

            st.markdown(
                f'<div class="strat-card" style="background:{c["bg"]};border-color:{c["border"]}">'
                f'<h4>{sname} ({len(positions)} closed)</h4>'
                f'{"".join(pos_rows)}'
                f'<div style="text-align:right;margin-top:8px;font-weight:700;font-size:1rem">'
                f'Subtotal: {_fmt_pnl(spnl)}</div></div>',
                unsafe_allow_html=True,
            )

        st.markdown(
            f'<div style="text-align:right;font-size:1.2rem;font-weight:700;padding:12px;'
            f'background:#1a1a2e;border-radius:8px;border:1px solid #333;margin-top:8px">'
            f'Today\'s Realized P&L: {_fmt_pnl(total_rpnl)}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.info("No closed positions today")

    st.divider()

    # --- Historical Positions ---
    st.subheader("Historical Positions")
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE) as f:
                hist = json.load(f)
            if hist:
                hdf = pd.DataFrame(hist)
                by_strat = hdf.groupby("strategy") if "strategy" in hdf.columns else None

                if by_strat:
                    for sid, grp in sorted(by_strat):
                        sname = names.get(sid, sid)
                        c = STRAT_COLORS.get(sid, {"bg": "#1a1a2e", "border": "#555"})
                        total = grp["pnl"].sum() if "pnl" in grp.columns else 0
                        n = len(grp)
                        wins = (grp["pnl"] > 0).sum() if "pnl" in grp.columns else 0
                        losses = n - wins

                        st.markdown(
                            f'<div class="strat-card" style="background:{c["bg"]};border-color:{c["border"]}">'
                            f'<h4>{sname}</h4>'
                            f'<div style="display:flex;gap:24px;font-size:0.9rem">'
                            f'<span>Trades: {n}</span>'
                            f'<span>Wins: {wins}</span>'
                            f'<span>Losses: {losses}</span>'
                            f'<span>Total: {_fmt_pnl(total)}</span>'
                            f'</div></div>',
                            unsafe_allow_html=True,
                        )
                else:
                    cols = [c for c in ["strategy", "symbol", "direction", "entry_price", "exit_price", "pnl"]
                            if c in hdf.columns]
                    st.dataframe(hdf[cols] if cols else hdf, use_container_width=True, hide_index=True)
            else:
                st.info("No historical positions yet")
        except Exception:
            pass
    else:
        st.info("No historical positions yet")

    st.divider()

    # --- Latest Selections ---
    st.subheader("Latest Selections")
    # Load from per-strategy files
    sel_results = {}
    sel_date = "N/A"
    sel_scan_date = "N/A"
    for sid in ["donchian_adx", "keltner_rsi", "supertrend_volume"]:
        sf = os.path.join(DATA_DIR, f"selections_{sid}.json")
        if os.path.exists(sf):
            try:
                with open(sf) as f:
                    sd = json.load(f)
                if sd.get("scan_date"):
                    sel_scan_date = sd["scan_date"]
                sel_results[sid] = sd.get("data", [])
            except Exception:
                pass
    # Fallback to legacy combined file
    if not sel_results and os.path.exists(SEL_FILE):
        with open(SEL_FILE) as f:
            data = json.load(f)
        sel_date = data.get("date", "N/A")
        sel_scan_date = data.get("scan_date", sel_scan_date)
        sel_results = data.get("results", {})
    else:
        sel_date = datetime.now().strftime("%Y-%m-%d")
    if sel_results:
        st.caption(f"Scan ran: {sel_date} — using {sel_scan_date} close")
        for sid, sigs in sel_results.items():
            sname = names.get(sid, sid)
            c = STRAT_COLORS.get(sid, {"bg": "#1a1a2e", "border": "#555"})
            with st.expander(f"{sname} — {len(sigs)} signals"):
                if sigs:
                    df = pd.DataFrame(sigs)
                    st.dataframe(df.style.map(
                        lambda v: "color: #00e676; font-weight: bold"
                        if isinstance(v, str) and v == "LONG"
                        else ("color: #ff5252; font-weight: bold"
                              if isinstance(v, str) and v == "SHORT" else ""),
                        subset=["direction"] if "direction" in df.columns else []
                    ), use_container_width=True, hide_index=True)
                else:
                    st.info("No signals")
    else:
        st.info("No selections yet")

    st.divider()
    st.subheader("Scan History")
    try:
        df = query_scan_history(DATA_DIR, days=30)
        if not df.empty:
            st.dataframe(df.style.map(
                lambda v: "background-color: #1a3a1a" if isinstance(v, (int, float)) and v > 0 else "",
                subset=df.columns.tolist()
            ), use_container_width=True)

            dates = df.index.tolist()
            if dates:
                sel_date = st.selectbox("Drill-down date", dates, key="scan_history_date")
                if sel_date:
                    details = query_signal_details(DATA_DIR, str(sel_date))
                    if not details.empty:
                        st.dataframe(details, use_container_width=True, hide_index=True)
        else:
            st.info("No scan history yet. Stock Selection needs to run first.")
    except Exception as e:
        st.caption(f"Scan history unavailable: {e}")
