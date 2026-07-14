"""
Single source of truth: DuckDB universe table via DataCache.
All stock metadata (symbols, lot sizes, universes) lives in the shared DuckDB.
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from common.market_data.cache import get_cache

_cache = None


def _get():
    global _cache
    if _cache is None:
        _cache = get_cache()
    return _cache


def get_lot_size(symbol):
    return _get().get_lot_size(symbol)


def get_tick_size(symbol):
    return _get().get_tick_size(symbol)


UNIVERSE_DEFAULT = "Margin < 200K"


def __getattr__(name):
    c = _get()
    if name == 'SYMBOLS_DATA':
        return c.get_symbols_data()
    if name == 'UNIVERSE_MAP':
        return c.get_universe_map()
    if name == 'UNIVERSE_NAMES':
        return c.get_universe_names()
    if name == 'NIFTY200':
        return c.get_nifty200_symbols()
    if name == 'NIFTY50':
        return [s + ".NS" for s in sorted(c.get_nifty50_symbols())]
    if name == 'NIFTY100_SYMBOLS':
        return c.get_nifty100_symbols()
    if name == 'NIFTY50_SYMBOLS':
        return c.get_nifty50_symbols()
    if name == 'MARGIN_FILTERED_UNIVERSE':
        return c.get_universe_map().get("Margin < 200K", set())
    if name == 'FULL_FO_SYMBOLS':
        return c.get_universe_map().get("Full F&O", set())
    if name == 'SECTOR_MAP':
        stocks = c.get_all_stocks()
        return {s["symbol"]: s.get("sector", "") for s in stocks if s.get("sector")}
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
