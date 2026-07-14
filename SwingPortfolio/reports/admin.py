import streamlit as st
import pandas as pd
import os, sys, json, shutil

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
# Add root for common module
COMMON = os.path.dirname(BASE)
if COMMON not in sys.path:
    sys.path.insert(0, COMMON)

from cfg.settings import (CAPITAL_TOTAL, MAX_POSITIONS_PER_STRATEGY, MAX_POSITIONS_TOTAL,
                           TRAIL_PCT, RR_RATIO, HARD_SL_AMOUNT, HARD_SL_ENABLED,
                           GAP_FILTER_ENABLED, REGIME_FILTER_ENABLED,
                           CAPITAL_MODE, STRATEGY_WEIGHTS, POSITION_SIZING, OPTION_STRIKE,
                           MAX_MARGIN_PER_STOCK, MARGIN_RATE)
from core.notifications import get_config, update_config, is_configured
from core.registry import get_all_strategies, load_config
from cfg.universes import UNIVERSE_NAMES

SETTINGS_FILE = os.path.join(BASE, "cfg", "settings.py")
CONFIG_FILE = os.path.join(BASE, "cfg", "strategies.json")
DEFAULT_SETTINGS_FILE = os.path.join(BASE, "cfg", "default_settings.json")
DEFAULT_STRATEGIES_FILE = os.path.join(BASE, "cfg", "default_strategies.json")


def _save_setting(key, value):
    path = SETTINGS_FILE
    with open(path, "r") as f:
        content = f.read()
    old = f"{key} = "
    idx = content.find(old)
    if idx == -1:
        return
    end = content.index("\n", idx)
    if isinstance(value, str):
        new_line = f'{key} = "{value}"'
    elif isinstance(value, bool):
        new_line = f"{key} = {str(value)}"
    else:
        new_line = f"{key} = {value}"
    content = content[:idx] + new_line + content[end:]
    with open(path, "w") as f:
        f.write(content)


CSS = """
<style>
    .strat-card { border-radius: 8px; padding: 10px 14px; margin: 8px 0; border-left: 4px solid; background: #1a1a2e; }
</style>
"""


def show():
    st.title("SwingPortfolio \u2014 Admin")
    st.markdown(CSS, unsafe_allow_html=True)

    tab1, tab2, tab3, tab4 = st.tabs(["Risk Settings", "Strategies", "Google Sheet", "Telegram"])

    with tab1:
        col1, col2 = st.columns(2)
        with col1:
            sizing_opts = ["lot", "capital", "options"]
            ps_idx = sizing_opts.index(POSITION_SIZING) if POSITION_SIZING in sizing_opts else 0
            ps = st.selectbox("Position Sizing", sizing_opts, index=ps_idx)
            if ps == "options":
                strike_opts = ["ATM", "ITM1"]
                os_idx = strike_opts.index(OPTION_STRIKE) if OPTION_STRIKE in strike_opts else 0
                bt_strike = st.selectbox("Option Strike (base)", strike_opts, index=os_idx,
                                         help="Expiry logic may override: <10d to expiry -> ATM, >=10d -> ITM1")
            else:
                bt_strike = OPTION_STRIKE
            ct = st.number_input("Total Capital", min_value=50000, max_value=5000000, value=CAPITAL_TOTAL, step=50000)
            mm = st.number_input("Max Margin per Stock (lot mode)", min_value=50000, max_value=1000000, value=MAX_MARGIN_PER_STOCK, step=10000)
            mr = st.slider("Margin Rate (% of contract)", 0.05, 0.30, MARGIN_RATE, 0.01, format="%.2f")
            cm = st.selectbox("Capital Mode", ["equal", "weighted"], index=0 if CAPITAL_MODE == "equal" else 1)
            mp = st.number_input("Max Positions per Strategy", min_value=1, max_value=20, value=MAX_POSITIONS_PER_STRATEGY)
            mt = st.number_input("Max Total Positions", min_value=1, max_value=50, value=MAX_POSITIONS_TOTAL)
        with col2:
            rr = st.slider("RR Ratio", 1.0, 5.0, RR_RATIO, 0.1)
            trail = st.slider("Trail %", 0.001, 0.05, TRAIL_PCT, 0.001, format="%.3f")
            hs = st.number_input("Hard SL Amount", min_value=5000, max_value=100000, value=HARD_SL_AMOUNT, step=5000)
            h_en = st.checkbox("Hard SL Enabled", value=HARD_SL_ENABLED)
            g_en = st.checkbox("Gap Filter", value=GAP_FILTER_ENABLED)
            r_en = st.checkbox("Regime Filter", value=REGIME_FILTER_ENABLED)

        if st.button("Save Risk Settings", type="primary"):
            _save_setting("POSITION_SIZING", ps)
            if ps == "options":
                _save_setting("OPTION_STRIKE", bt_strike)
            _save_setting("CAPITAL_TOTAL", ct)
            _save_setting("MAX_MARGIN_PER_STOCK", mm)
            _save_setting("MARGIN_RATE", mr)
            _save_setting("CAPITAL_MODE", cm)
            _save_setting("MAX_POSITIONS_PER_STRATEGY", mp)
            _save_setting("MAX_POSITIONS_TOTAL", mt)
            _save_setting("RR_RATIO", rr)
            _save_setting("TRAIL_PCT", trail)
            _save_setting("HARD_SL_AMOUNT", hs)
            _save_setting("HARD_SL_ENABLED", h_en)
            _save_setting("GAP_FILTER_ENABLED", g_en)
            _save_setting("REGIME_FILTER_ENABLED", r_en)
            st.success("Settings saved. Restart app to apply.")

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            if st.button("Save as Default Settings", type="secondary", use_container_width=True):
                default = {
                    "POSITION_SIZING": ps, "OPTION_STRIKE": bt_strike if ps == "options" else OPTION_STRIKE,
                    "CAPITAL_TOTAL": ct,
                    "MAX_MARGIN_PER_STOCK": mm, "MARGIN_RATE": mr,
                    "CAPITAL_MODE": cm, "MAX_POSITIONS_PER_STRATEGY": mp,
                    "MAX_POSITIONS_TOTAL": mt, "RR_RATIO": rr,
                    "TRAIL_PCT": trail, "HARD_SL_AMOUNT": hs,
                    "HARD_SL_ENABLED": h_en, "GAP_FILTER_ENABLED": g_en,
                    "REGIME_FILTER_ENABLED": r_en,
                }
                with open(DEFAULT_SETTINGS_FILE, "w") as f:
                    json.dump(default, f, indent=2)
                st.success("Current settings saved as defaults!")
        with col_d2:
            if st.button("Restore Default Settings", type="secondary", use_container_width=True):
                if os.path.exists(DEFAULT_SETTINGS_FILE):
                    with open(DEFAULT_SETTINGS_FILE) as f:
                        d = json.load(f)
                    for k, v in d.items():
                        _save_setting(k, v)
                    st.success("Default settings restored! Restart app to apply.")
                    st.rerun()
                else:
                    st.error("No default settings saved yet. Click 'Save as Default Settings' first.")
        st.divider()
        st.markdown("**Margin Filtered Universe**")
        from cfg.universes import UNIVERSE_MAP
        from common.universes import refresh_margin_filtered
        mu = UNIVERSE_MAP.get("Margin < 200K", set())
        st.metric("Current size", f"{len(mu)} stocks")
        if st.button("Refresh from yfinance", type="secondary"):
            with st.spinner("Fetching live prices..."):
                n = refresh_margin_filtered()
                st.success(f"Updated: {n} stocks")
                st.rerun()

    with tab2:
        cfg = load_config()
        strat_colors = {"donchian_adx": "#2a6fb0", "keltner_rsi": "#b05a2a", "supertrend_volume": "#b02ab0", "default": "#555"}
        for i, sc in enumerate(cfg["strategies"]):
            sc_id = sc["id"]
            color = strat_colors.get(sc_id, strat_colors["default"])
            with st.container(border=True):
                st.markdown(
                    f'<div class="strat-card" style="border-color:{color}">'
                    f'<strong>{sc["name"]}</strong> ({sc["id"]})<br>'
                    f'<span style="opacity:0.7;font-size:0.85rem">{sc["description"]}</span></div>',
                    unsafe_allow_html=True,
                )
                col_a, col_b, col_c = st.columns([1, 1, 1])
                with col_a:
                    enabled = st.checkbox("Enabled", value=sc.get("enabled", True), key=f"en_{sc['id']}")
                    cfg["strategies"][i]["enabled"] = enabled
                with col_b:
                    uni = st.selectbox("Universe", UNIVERSE_NAMES,
                                       index=UNIVERSE_NAMES.index(sc.get("universe", UNIVERSE_NAMES[0])),
                                       key=f"uni_{sc['id']}")
                    cfg["strategies"][i]["universe"] = uni
                with col_c:
                    entry_opt = ["15m_945", "walk", "zscore"]
                    et = st.selectbox("Entry Time", entry_opt,
                                      index=entry_opt.index(sc.get("entry_time", "zscore")),
                                      key=f"et_{sc['id']}")
                    cfg["strategies"][i]["entry_time"] = et

                col_d, col_e, col_f = st.columns(3)
                with col_d:
                    live = st.checkbox("Live Trading", value=sc.get("live", False), key=f"live_{sc['id']}")
                    cfg["strategies"][i]["live"] = live
                with col_e:
                    bn = st.text_input("Broker Name", value=sc.get("broker_name", "xyz"), key=f"bn_{sc['id']}")
                    cfg["strategies"][i]["broker_name"] = bn
                with col_f:
                    bu = st.text_input("Broker Username", value=sc.get("broker_username", "pqr"), key=f"bu_{sc['id']}")
                    cfg["strategies"][i]["broker_username"] = bu

                with st.expander("Parameters"):
                    for k, v in sc["params"].items():
                        if isinstance(v, bool):
                            cfg["strategies"][i]["params"][k] = st.checkbox(k, value=v, key=f"p_{sc['id']}_{k}")
                        elif isinstance(v, float):
                            cfg["strategies"][i]["params"][k] = st.number_input(k, value=float(v), format="%.2f", key=f"p_{sc['id']}_{k}")
                        elif isinstance(v, int):
                            cfg["strategies"][i]["params"][k] = st.number_input(k, value=int(v), step=1, key=f"p_{sc['id']}_{k}")

            if i == 0:
                st.markdown("---")
                st.markdown("**Add New Strategy**\nTo add a new strategy, create a class in `strategies/` and add its entry to `cfg/strategies.json`.")

        if st.button("Save Strategy Config", type="primary"):
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f, indent=2)
            st.success("Strategy config saved!")

        col_d1, col_d2 = st.columns(2)
        with col_d1:
            if st.button("Save as Default Strategies", type="secondary", use_container_width=True):
                with open(DEFAULT_STRATEGIES_FILE, "w") as f:
                    json.dump(cfg, f, indent=2)
                st.success("Current strategies saved as defaults!")
        with col_d2:
            if st.button("Restore Default Strategies", type="secondary", use_container_width=True):
                if os.path.exists(DEFAULT_STRATEGIES_FILE):
                    shutil.copy(DEFAULT_STRATEGIES_FILE, CONFIG_FILE)
                    st.success("Default strategies restored!")
                    st.rerun()
                else:
                    st.error("No default strategies saved yet. Click 'Save as Default Strategies' first.")

    with tab3:
        st.subheader("Google Sheet Integration")
        st.info("Sheet Watcher has been removed. Google Sheets integration is no longer active.")

    with tab4:
        tg = get_config()
        bot_token = st.text_input("Bot Token", value=tg.get("bot_token", ""), type="password")
        chat_id = st.text_input("Chat ID", value=tg.get("chat_id", ""))
        if st.button("Save Telegram", type="primary"):
            if len(bot_token) < 20 or "Error" in bot_token or "import" in bot_token:
                st.error("Invalid bot token — looks like an old error message. Please paste your actual token.")
            else:
                update_config(bot_token, chat_id)
                st.success("Saved!")
        if is_configured():
            st.success("Telegram configured and working.")
