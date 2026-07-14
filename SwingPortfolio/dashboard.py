import streamlit as st
import os, sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
# Add root for common module
COMMON_DIR = os.path.dirname(BASE_DIR)
if COMMON_DIR not in sys.path:
    sys.path.insert(0, COMMON_DIR)

st.set_page_config(page_title="SwingPortfolio", page_icon="\u2601\ufe0f", layout="wide")

PAGES = {
    "\U0001f4ca Dashboard": "reports.dashboard",
    "\U0001f4cb Stock Selection": "reports.stock_selection",
    "\U0001f4ca Backtest": "reports.backtest",
    "\U0001f6e0\ufe0f Admin": "reports.admin",
    "\u2699\ufe0f Scheduler": "reports.scheduler",
    "\u2139\ufe0f About": "reports.about",
}

if "nav_page" not in st.session_state:
    nav_from_qp = st.query_params.get("nav")
    valid = list(PAGES.values())
    st.session_state.nav_page = nav_from_qp if nav_from_qp in valid else "reports.dashboard"

st.markdown("""
<style>
    .nav-link { display: block; padding: 6px 12px; margin: 2px 0;
        text-decoration: none; color: inherit; font-size: 14px; border-radius: 4px; }
    .nav-link:hover { background: rgba(128,128,128,0.1); }
    .nav-link.active { color: #00e676; font-weight: 600; }
</style>
""", unsafe_allow_html=True)

st.sidebar.markdown(
    "<p style='margin:6px 0;text-align:center;font-size:1.1rem;font-weight:700'>\u2601\ufe0f SwingPortfolio</p>"
    "<hr style='margin:2px 0 8px'>",
    unsafe_allow_html=True,
)

for label, module in PAGES.items():
    cls = "nav-link active" if st.session_state.nav_page == module else "nav-link"
    st.sidebar.markdown(
        f"<a href='?nav={module}' class='{cls}' target='_self'>{label}</a>",
        unsafe_allow_html=True,
    )

try:
    exec(f"from {st.session_state.nav_page} import show")
    show()
except Exception as e:
    st.error(f"Error loading page: {e}")
