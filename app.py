import streamlit as st
import os, sys

_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)
_SWING = os.path.join(_BASE, "SwingPortfolio")
if _SWING not in sys.path:
    sys.path.insert(0, _SWING)

from common.market_data.provider import _load_config as _get_config

st.set_page_config(page_title="Swing Trading System", layout="wide")

PAGES = {
    "📊 Dashboard": "SwingPortfolio.reports.dashboard",
    "📈 Stock Selection": "SwingPortfolio.reports.stock_selection",
    "⏪ Backtest": "SwingPortfolio.reports.backtest",
    "📡 Live Monitor": "SwingPortfolio.reports.live_monitor",
    "⚙️ Scheduler": "SwingPortfolio.reports.scheduler",
    "⚙️ Admin": "SwingPortfolio.reports.admin",
    "📖 Guide": "SwingPortfolio.reports.about",
}
PAGE_ORDER = list(PAGES.keys())

if "page" not in st.session_state:
    nav_from_qp = st.query_params.get("nav")
    st.session_state.page = nav_from_qp if nav_from_qp in PAGES.values() else "SwingPortfolio.reports.dashboard"

_cfg = _get_config()
_host = _cfg.get("host", "localhost")
_provider = _cfg.get("portfolio_providers", {}).get("__default__", "yahoo")

st.sidebar.markdown(
    "<p style='margin:6px 0;text-align:center;font-size:1.1rem;font-weight:700'>📈 Swing Trading</p>"
    f"<p style='margin:0;text-align:center;font-size:11px;color:#888'>{_host} • {_provider.upper()}</p>"
    "<hr style='margin:2px 0 8px'>",
    unsafe_allow_html=True,
)

for label in PAGE_ORDER:
    module = PAGES[label]
    cls = "nav-link active" if st.session_state.page == module else "nav-link"
    st.sidebar.markdown(
        f"<a href='?nav={module}' class='{cls}' target='_self'>{label}</a>",
        unsafe_allow_html=True,
    )

try:
    exec(f"from {st.session_state.page} import show")
    show()
except Exception as e:
    st.error(f"Error: {e}")
