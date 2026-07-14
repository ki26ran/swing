import streamlit as st
import pandas as pd
import os, sys
from datetime import datetime, date, timedelta
import plotly.graph_objects as go
from plotly.subplots import make_subplots

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from core.registry import get_all_strategies, get_strategy_names, get_strategy_universe, get_strategy
from core.backtest_engine import run_backtest, simulate_today, _months_from_range
from cfg.universes import UNIVERSE_MAP, UNIVERSE_NAMES, UNIVERSE_DEFAULT
from cfg.settings import CAPITAL_TOTAL, MAX_POSITIONS_PER_STRATEGY, ENTRY_TIME, POSITION_SIZING, OPTION_STRIKE

CSS = """
<style>
    .strat-card { border-radius: 8px; padding: 10px 14px; margin: 8px 0; border-left: 4px solid; }
    .tag-long { color: #00e676; font-weight: 600; }
    .tag-short { color: #ff5252; font-weight: 600; }
    .pnl-pos { color: #00e676; font-weight: 700; }
    .pnl-neg { color: #ff5252; font-weight: 700; }
</style>
"""


def show():
    st.title("SwingPortfolio \u2014 Backtest")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown("Run backtests for any strategy and compare.")

    if "sp_bt_results" not in st.session_state:
        st.session_state.sp_bt_results = {}

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    with col1:
        from_date = st.date_input("From", value=date(2026, 1, 1))
    with col2:
        to_date = st.date_input("To", value=date(2026, 5, 31))
    with col3:
        uni_name = st.selectbox("Universe", UNIVERSE_NAMES, index=UNIVERSE_NAMES.index(UNIVERSE_DEFAULT))
    with col4:
        entry_mode = st.selectbox("Entry", ["15m_945", "walk", "zscore", "15_15"], index=3)
    with col5:
        cap = st.number_input("Capital/Strategy", min_value=50000, max_value=5000000, value=CAPITAL_TOTAL, step=50000)
    with col6:
        sizing_opts = ["lot", "capital", "options"]
        sz_idx = sizing_opts.index(POSITION_SIZING) if POSITION_SIZING in sizing_opts else 0
        bt_sizing = st.selectbox("Position Sizing", sizing_opts, index=sz_idx)

    col_s1, col_s2 = st.columns([1, 5])
    with col_s1:
        if bt_sizing == "options":
            strike_opts = ["ATM", "ITM1"]
            sk_idx = strike_opts.index(OPTION_STRIKE) if OPTION_STRIKE in strike_opts else 0
            bt_strike = st.selectbox("Option Strike", strike_opts, index=sk_idx,
                                     help="Base selection. System may auto-switch: expiry <10d -> ATM, >=10d -> ITM1")
        else:
            bt_strike = OPTION_STRIKE

    # Strategy selection
    all_names = get_strategy_names()
    name_to_id = {v: k for k, v in all_names.items()}
    display_names = list(name_to_id.keys())
    selected_names = st.multiselect(
        "Strategies",
        options=display_names,
        default=display_names,
        help="Select one or more strategies. All selected by default."
    )
    selected_ids = [name_to_id[n] for n in selected_names]

    run_btn = st.button(f"Run Backtest ({len(selected_ids)} strategy{'ies' if len(selected_ids) != 1 else ''})", type="primary")

    if run_btn:
        if not selected_ids:
            st.warning("Select at least one strategy")
            return
        
        # Today-only → auto-trigger simulate instead of daily-bar backtest
        if from_date == date.today() and to_date == date.today():
            st.info("Today-only selected — running intraday simulation instead of daily-bar backtest")
            with st.spinner("Simulating today's trades on 1-min bars..."):
                sim_trades = simulate_today()
            if sim_trades:
                df = pd.DataFrame(sim_trades)
                df["win"] = df["pnl"] > 0
                tot = df["pnl"].sum()
                n_wins = df["win"].sum()
                n_losses = len(df) - n_wins
                wr = n_wins / len(df) * 100 if len(df) else 0
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Trades", len(df))
                c2.metric("P&L", f"Rs.{tot:+,.0f}")
                c3.metric("Wins", f"{n_wins}W/{n_losses}L")
                c4.metric("Win Rate", f"{wr:.0f}%")
                cols = ["sym", "dir", "entry_time_str", "exit_time", "ep", "ex", "qty", "pnl", "exit_reason"]
                avail = [c for c in cols if c in df.columns]
                if avail:
                    st.dataframe(df[avail].style.map(
                        lambda v: "color:#00e676" if isinstance(v, (int,float)) and v > 0 else ("color:#ff5252" if isinstance(v, (int,float)) and v < 0 else ""),
                        subset=["pnl"]
                    ).format({"ep":"{:.2f}","ex":"{:.2f}","pnl":"{:+,.0f}"}), use_container_width=True, hide_index=True)
            else:
                st.info("No trades simulated. Check that selections exist for today.")
            st.stop()
        
        months = _months_from_range(from_date, to_date)
        if not months:
            st.error("Invalid date range")
            return
        selected_uni_name = uni_name
        results = {}
        st.info("First run downloads data from Yahoo (~3-5 min for new months). Subsequent runs use cached data and are faster.")
        status_box = st.status("Running backtests...", expanded=True)
        for sid in selected_ids:
            status_box.write(f"Loading strategy: {sid}")
            strat = get_strategy(sid)
            if strat is None:
                st.warning(f"Strategy '{sid}' could not be loaded — skipping")
                results[sid] = ([], [], None)
                continue
            try:
                uni_name_strat = getattr(strat, '_universe_name', None)
                if uni_name_strat and uni_name_strat in UNIVERSE_MAP:
                    use_uni_name = uni_name_strat
                else:
                    use_uni_name = selected_uni_name
                universe = UNIVERSE_MAP.get(use_uni_name)
                status_box.write(f"Downloading data + running {strat.name}...")
                trades, day_log = run_backtest(strat, months, universe=universe, entry_mode=entry_mode, capital=cap, max_pos=MAX_POSITIONS_PER_STRATEGY,
                                                 position_sizing=bt_sizing, option_strike=bt_strike)
                fd = pd.Timestamp(from_date)
                td = pd.Timestamp(to_date) + timedelta(days=1)
                trades = [t for t in trades if fd <= pd.Timestamp(t["entry_dt"]).floor("D") < td]
                results[strat.strategy_id] = (trades, day_log, strat)
                status_box.write(f"{strat.name}: {len(trades)} trades")
            except Exception as e:
                sname = getattr(strat, 'name', sid)
                status_box.write(f"{sname} failed: {e}")
                st.warning(f"{sname} failed: {e}")
                results[sid] = ([], [], strat)
        status_box.update(label="Backtest complete!", state="complete", expanded=False)
        st.session_state.sp_bt_results = results
        all_empty = all(len(t) == 0 for t, _, _ in results.values())
        if all_empty:
            from_today = from_date >= date.today() or to_date >= date.today()
            if from_today:
                st.warning("Today's bar excluded from daily backtest. Use the **Simulate Today** button below for today's results.")
            else:
                st.info("No trades found in the selected date range.")
        else:
            st.success("All backtests complete!")

    st.divider()
    st.subheader("Today's Live Simulation")
    st.caption("Uses yesterday's selections + 1-min intraday data to replicate live trading. This matches how the live trader actually operates, unlike the daily-bar backtest above.")
    if st.button("Simulate Today's Trading", type="primary"):
        with st.spinner("Downloading 1-min data and simulating..."):
            sim_trades = simulate_today()
        if sim_trades:
            df = pd.DataFrame(sim_trades)
            df["win"] = df["pnl"] > 0
            tot = df["pnl"].sum()
            n_wins = df["win"].sum()
            n_losses = len(df) - n_wins
            wr = n_wins / len(df) * 100 if len(df) else 0

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Trades", len(df))
            c2.metric("P&L", f"Rs.{tot:+,.0f}")
            c3.metric("Wins", f"{n_wins}W/{n_losses}L")
            c4.metric("Win Rate", f"{wr:.0f}%")

            strat_colors = {"donchian_adx": "#2a6fb0", "keltner_rsi": "#b05a2a", "supertrend_volume": "#b02ab0"}
            for sid in sorted(set(t["strategy"] for t in sim_trades)):
                sd = [t for t in sim_trades if t["strategy"] == sid]
                sdf = pd.DataFrame(sd)
                spnl = sdf["pnl"].sum()
                sc = strat_colors.get(sid, "#555")
                st.markdown(
                    f'<div class="strat-card" style="background:#1a1a2e;border-color:{sc}">'
                    f'<strong>{sid.upper()}</strong> — {len(sd)} trades, '
                    f'P&L: <span class="{"pnl-pos" if spnl>=0 else "pnl-neg"}">Rs.{spnl:+,.0f}</span></div>',
                    unsafe_allow_html=True,
                )
                with st.expander("View trades"):
                    cols = ["sym", "dir", "entry_time_str", "exit_time", "ep", "ex", "qty", "pnl", "exit_reason"]
                    avail = [c for c in cols if c in sdf.columns]
                    display = sdf[avail].copy()
                    fmt = {"ep": "{:.2f}", "pnl": "{:+,.0f}"}
                    if "ex" in display.columns:
                        fmt["ex"] = "{:.2f}"
                    styled = display.style.map(
                        lambda v: "color: #00e676; font-weight: bold" if isinstance(v, str) and v == "LONG"
                        else ("color: #ff5252; font-weight: bold" if isinstance(v, str) and v == "SHORT" else ""),
                        subset=["dir"]
                    ).map(
                        lambda v: "color: #00e676; font-weight: bold" if isinstance(v, (int, float)) and v > 0
                        else ("color: #ff5252; font-weight: bold" if isinstance(v, (int, float)) and v < 0 else ""),
                        subset=["pnl"]
                    ).format(fmt)
                    st.dataframe(styled, use_container_width=True, hide_index=True)
        else:
            st.info("No trades simulated. Check that selections.json exists and has data.")
    st.divider()

    results = st.session_state.sp_bt_results
    if not results:
        st.info("Select parameters and strategies, then click **Run Backtest**")
        return

    st.subheader("Comparison")

    rows = []
    all_trades = {}
    all_daylogs = {}
    for sid, (trades, day_log, strat) in results.items():
        all_trades[sid] = trades
        all_daylogs[sid] = day_log
        df = pd.DataFrame(trades) if trades else pd.DataFrame()
        n = len(df)
        if n == 0:
            rows.append({"Strategy": strat.name, "Trades": 0, "P&L": 0, "WR": 0, "PF": 0, "Sharpe": 0, "MDD": 0, "AvgWin": 0, "AvgLoss": 0})
            continue
        df["win"] = df["pnl"] > 0
        df["cum"] = df["pnl"].cumsum()
        df["peak"] = df["cum"].cummax()
        df["dd"] = df["peak"] - df["cum"]
        w = df[df["win"]]
        l = df[~df["win"]]
        nw = len(w)
        nl = len(l)
        tot = df["pnl"].sum()
        wr = nw / n * 100 if n else 0
        pf = abs(w["pnl"].sum() / l["pnl"].sum()) if nl and l["pnl"].sum() != 0 else float('inf')
        mdd = df["dd"].max()
        aw = w["pnl"].mean() if nw else 0
        al = abs(l["pnl"].mean()) if nl else 0
        dp = df.groupby(df["exit_dt"].dt.date)["pnl"].sum() if "exit_dt" in df.columns else df.groupby(lambda _: 0)["pnl"].sum()
        sh = round(dp.mean() / dp.std() * (252 ** 0.5), 2) if len(dp) > 1 and dp.std() > 0 else 0

        rows.append({
            "Strategy": strat.name, "Trades": n, "P&L": f"Rs.{tot:+,.0f}", "WR": f"{wr:.0f}%",
            "PF": f"{pf:.2f}", "Sharpe": sh, "MDD": f"Rs.{mdd:+,.0f}",
            "AvgWin": f"Rs.{aw:+,.0f}", "AvgLoss": f"Rs.{al:+,.0f}"
        })

    comp_df = pd.DataFrame(rows)
    for c in comp_df.select_dtypes(include="string").columns:
        comp_df[c] = comp_df[c].astype(object)
    st.dataframe(comp_df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Portfolio (all strategies combined)")
    all_combined = []
    for sid, (trades, _, _) in results.items():
        all_combined.extend(trades)
    if all_combined:
        pdf = pd.DataFrame(all_combined)
        pdf = pdf.sort_values("exit_dt").reset_index(drop=True)
        n = len(pdf)
        pdf["win"] = pdf["pnl"] > 0
        pdf["cum"] = pdf["pnl"].cumsum()
        pdf["peak"] = pdf["cum"].cummax()
        pdf["dd"] = pdf["peak"] - pdf["cum"]
        w = pdf[pdf["win"]]
        l = pdf[~pdf["win"]]
        nw = len(w)
        nl = len(l)
        tot = pdf["pnl"].sum()
        wr = nw / n * 100 if n else 0
        pf = abs(w["pnl"].sum() / l["pnl"].sum()) if nl and l["pnl"].sum() != 0 else float('inf')
        mdd = pdf["dd"].max()
        aw = w["pnl"].mean() if nw else 0
        al = abs(l["pnl"].mean()) if nl else 0
        expectancy = (aw * nw - al * nl) / n if n else 0
        payoff = aw / al if nl and al > 0 else 0
        pdf["hd"] = (pdf["exit_dt"] - pdf["entry_dt"]).dt.days
        avg_hold = pdf["hd"].mean()
        pdf["_w"] = pdf["pnl"] > 0
        stk = 0
        mws = 0
        mls = 0
        for v in pdf["_w"]:
            if v:
                stk = stk + 1 if stk >= 0 else 1
                mws = max(mws, stk)
            else:
                stk = stk - 1 if stk <= 0 else -1
                mls = max(mls, -stk)
        rec = tot / mdd if mdd > 0 else 0
        dp = pdf.groupby(pdf["exit_dt"].dt.date)["pnl"].sum()
        sh = round(dp.mean() / dp.std() * (252 ** 0.5), 2) if len(dp) > 1 and dp.std() > 0 else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Trades", n)
        c2.metric("Total P&L", f"Rs.{tot:+,.0f}")
        c3.metric("Win Rate", f"{wr:.0f}%")
        c4.metric("Profit Factor", f"{pf:.2f}")
        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Expectancy", f"Rs.{expectancy:+,.0f}")
        c6.metric("Max DD", f"Rs.{mdd:+,.0f}")
        c7.metric("Sharpe", f"{sh:.2f}")
        c8.metric("Payoff", f"{payoff:.2f}")
        c9, c10, c11, c12 = st.columns(4)
        c9.metric("Avg Win", f"Rs.{aw:+,.0f}")
        c10.metric("Avg Loss", f"Rs.{al:+,.0f}")
        c11.metric("Avg Hold", f"{avg_hold:.1f}d")
        c12.metric("Recovery", f"{rec:.2f}")

        st.subheader("Portfolio Equity Curve")
        strat_palette = {"donchian_adx": "#00bcd4", "keltner_rsi": "#ff9800", "supertrend_volume": "#e040fb"}
        fig = make_subplots(rows=2, cols=1, row_heights=[0.7, 0.3], shared_xaxes=True, vertical_spacing=0.05)
        fig.add_trace(go.Scatter(x=pdf["exit_dt"], y=pdf["cum"], mode="lines", name="Total",
                                  line=dict(color="#00e676", width=2)), row=1, col=1)
        fig.add_trace(go.Scatter(x=pdf["exit_dt"], y=pdf["peak"], mode="lines", name="Peak",
                                  line=dict(color="#555", width=1, dash="dash")), row=1, col=1)
        for sid, color in strat_palette.items():
            sdf = pdf[pdf["strategy"] == sid].copy() if "strategy" in pdf.columns else None
            if sdf is not None and len(sdf) > 1:
                sdf["s_cum"] = sdf["pnl"].cumsum()
                sname = results.get(sid, (None, None, None))[2]
                sname = getattr(sname, "name", sid) if sname else sid
                fig.add_trace(go.Scatter(x=sdf["exit_dt"], y=sdf["s_cum"], mode="lines",
                               name=sname, line=dict(color=color, width=1.5)), row=1, col=1)
        fig.add_trace(go.Bar(x=pdf["exit_dt"], y=-pdf["dd"], name="Drawdown",
                               marker_color="#ff9100", opacity=0.4), row=2, col=1)
        fig.update_layout(height=500, template="plotly_dark", showlegend=True)
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Combined Trade Log")
        cols = ["strategy", "sym", "dir", "entry_dt", "exit_dt", "entry_time_str", "exit_time", "ep", "ex", "qty", "capital_deployed", "roi_pct", "pnl", "exit_reason"]
        if bt_sizing == "options":
            cols += ["option_label", "option_delta", "entry_premium"]
        avail = [c for c in cols if c in pdf.columns]
        display = pdf[avail].copy()
        display["pnl"] = display["pnl"].apply(lambda x: f"Rs.{x:+,.0f}").astype(object)

        def _fmt_dt(row, col, time_col):
            val = row[col]
            if isinstance(val, str):
                return val
            if pd.notna(val):
                t = row.get(time_col, "")
                return f"{val.strftime('%d-%b')} {t}" if t else val.strftime('%d-%b')
            return ""

        display["entry_dt"] = display.apply(lambda r: _fmt_dt(r, "entry_dt", "entry_time_str"), axis=1)
        display["exit_dt"] = display.apply(lambda r: _fmt_dt(r, "exit_dt", "exit_time"), axis=1)
        display = display.drop(columns=[c for c in ["entry_time_str", "exit_time"] if c in display.columns], errors='ignore')
        display = display.reset_index(drop=True)
        st.dataframe(display, use_container_width=True, hide_index=True)

        csv_data = pdf.to_csv(index=False).encode("utf-8")
        st.download_button("Download CSV", csv_data, "swingportfolio_trades.csv", "text/csv")

    st.divider()
    st.subheader("Per-Strategy Trade Log")
    tab_labels = [strat.name for _, _, strat in results.values()]
    tabs = st.tabs(tab_labels)
    for i, (sid, (trades, day_log, strat)) in enumerate(results.items()):
        with tabs[i]:
            df = pd.DataFrame(trades) if trades else pd.DataFrame()
            if df.empty:
                st.info("No trades")
                continue
            cols = ["sym", "dir", "entry_dt", "exit_dt", "entry_time_str", "exit_time", "ep", "ex", "qty", "capital_deployed", "roi_pct", "pnl", "exit_reason"]
            if bt_sizing == "options":
                cols += ["option_label", "option_delta", "entry_premium"]
            avail = [c for c in cols if c in df.columns]
            display = df[avail].copy()
            display["pnl"] = display["pnl"].apply(lambda x: f"Rs.{x:+,.0f}").astype(object)
            # Format dates with times (handle both Timestamp and string)
            def _fmt_dt(row, col, time_col):
                val = row[col]
                if isinstance(val, str):
                    return val
                if pd.notna(val):
                    t = row.get(time_col, "")
                    return f"{val.strftime('%d-%b')} {t}" if t else val.strftime('%d-%b')
                return ""
            display["entry_dt"] = display.apply(lambda r: _fmt_dt(r, "entry_dt", "entry_time_str"), axis=1)
            display["exit_dt"] = display.apply(lambda r: _fmt_dt(r, "exit_dt", "exit_time"), axis=1)
            display = display.drop(columns=[c for c in ["entry_time_str", "exit_time"] if c in display.columns], errors='ignore')
            display = display.reset_index(drop=True)
            st.dataframe(display, use_container_width=True, hide_index=True)
