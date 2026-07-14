"""
Resilient data loader backed by the shared DuckDB cache (common/market_data).
All daily data flows through DataCache — cache-first with live yfinance fallback.
No more per-project pickle caches or direct yfinance calls.
"""
import os, sys
import pandas as pd
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

ROOT = os.path.dirname(BASE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from common.market_data.cache import get_cache
from cfg.universes import SYMBOLS_DATA

DATA_BUFFER_DAYS = 200

FO_SYMBOLS = sorted(s + ".NS" for s in SYMBOLS_DATA.keys())


def download_month(year, month):
    """Download daily data for one month, backed by the shared DuckDB cache.
    Returns {SYMBOL.NS: DataFrame} with datetime index and OHLCV columns."""
    st = datetime(year, month, 1) - timedelta(days=DATA_BUFFER_DAYS)
    et = datetime(year, month + 1, 1) if month < 12 else datetime(year + 1, 1, 1)
    st_str = st.strftime("%Y-%m-%d")
    et_str = et.strftime("%Y-%m-%d")

    try:
        cache = get_cache(portfolio="SwingPortfolio")
        result = cache.get_daily_for_backtest(FO_SYMBOLS, st_str, et_str)
    except Exception as e:
        print(f"  [ERROR] DataCache failed: {e} — returning empty")
        return {}

    succeeded = len(result)
    failed = len(FO_SYMBOLS) - succeeded
    if failed:
        print(f"  [WARN] {failed} symbols failed to load (will be skipped)")
    print(f"  [DATA] {succeeded} symbols loaded ({failed} failed)")

    return result


def verify_data_source():
    """Verify that the configured data source (Shoonya/Yahoo) is working.
    Returns (ok: bool, message: str, provider_name: str)."""
    try:
        cache = get_cache(portfolio="SwingPortfolio")
        provider_name = type(cache.provider).__name__
        df = cache.get_daily("RELIANCE", "2026-06-01", "2026-06-21")
        if df is not None and not df.empty:
            return True, f"OK — {len(df)} daily rows for RELIANCE via {provider_name}", provider_name
        return False, f"No data returned from {provider_name}", provider_name
    except Exception as e:
        return False, f"Error: {e}", "unknown"
