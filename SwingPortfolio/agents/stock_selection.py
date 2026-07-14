"""
SwingPortfolio - Unified Stock Selection.
Runs at 8:30 AM. Scans all enabled strategies with full error isolation.
If one strategy fails, others continue. Partial results are saved.
"""
import os, sys, json, time
from datetime import datetime, timedelta
import pandas as pd
try:
    import zoneinfo
    _TZ = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    _TZ = None

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from core.data_loader import download_month
from core.registry import get_all_strategies, get_strategy_names, get_strategy_universe
from core.notifications import send_message, is_configured
from core.atomic import save_json
from cfg.universes import UNIVERSE_MAP

# Add common module to path (sibling under root)
_common_root = os.path.dirname(BASE)
if _common_root not in sys.path:
    sys.path.insert(0, _common_root)
from common.market_data.scan_logger import save_scan, rebuild_db

DATA_DIR = os.path.join(BASE, "data")
os.makedirs(DATA_DIR, exist_ok=True)
SEL_FILE = os.path.join(DATA_DIR, "selections.json")


def run():
    now = datetime.now(_TZ) if _TZ else datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    print(f"[{now.strftime('%H:%M:%S')}] SwingPortfolio Stock Selection started")

    # Download data once with resilient loader
    data = download_month(now.year, now.month)

    # Find most recent trading day with COMPLETE data (close not NaN)
    max_date = None
    for d in data.values():
        valid = d['Close'].dropna()
        if len(valid) > 0:
            dt = valid.index[-1].date()
            if max_date is None or dt > max_date:
                max_date = dt
    all_dates = sorted(set(
        ts.date() for d in data.values() for ts in d.index
        if ts < pd.Timestamp(now)
    ))
    # Never scan using today's partial candle (look-ahead bias)
    # If max_date is today and market hasn't closed, fall back to previous day
    if max_date and max_date == now.date():
        # Only use today if after EOD (15:30+)
        current_time = now.time()
        from datetime import time as dt_time
        if current_time < dt_time(15, 30):
            # Today's candle is still forming — use previous trading day
            prev_dates = sorted(set(
                ts.date() for d in data.values() for ts in d.index
                if ts < pd.Timestamp(now) and ts.date() < now.date()
            ))
            if prev_dates:
                max_date = prev_dates[-1]
                scan_date = datetime.combine(max_date, datetime.min.time())
                print(f"  (skipping today's partial candle, using {max_date})")
    if max_date:
        scan_date = datetime.combine(max_date, datetime.min.time())
    else:
        scan_date = datetime.combine(all_dates[-1], datetime.min.time()) if all_dates else None
    print(f"  Scanning {scan_date.date()} close ({len(all_dates)} trading days, {len(data)} stocks)")

    strategies = get_all_strategies()
    results = {}
    strategy_errors = []

    for strat in strategies:
        sid = strat.strategy_id
        # Get this strategy's universe
        uni_name = getattr(strat, '_universe_name', 'Margin < 200K')
        universe_set = UNIVERSE_MAP.get(uni_name)
        print(f"  [{sid}] {strat.name} ({uni_name})...", end=" ", flush=True)
        try:
            t0 = time.time()
            # Filter data by universe
            if universe_set is not None:
                filtered_data = {k: v for k, v in data.items()
                                 if k.replace('.NS', '') in universe_set}
            else:
                filtered_data = data
            signals = strat.scan_universe(filtered_data, scan_date.date())
            elapsed = time.time() - t0
            print(f"{len(signals)} signals ({elapsed:.0f}s)")
            # Log to scan history
            try:
                save_scan(DATA_DIR, str(scan_date.date()), sid, {
                    "scanned": len(data),
                    "signals": len(signals),
                    "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "data": [{"symbol": s["symbol"], "direction": s.get("direction", ""),
                               "entry": s.get("entry_price", s.get("entry", 0)),
                               "sl": s.get("sl", s.get("initial_sl", s.get("sl", 0)))} for s in signals]
                })
            except Exception:
                pass
            results[sid] = [{
                "symbol": s["symbol"],
                "direction": s["direction"],
                "close_price": s.get("close_price", 0),
                "entry_price": s.get("entry_price", 0),
                "scan_date": now.strftime("%Y-%m-%d %H:%M"),
            } for s in signals]
        except Exception as e:
            elapsed = time.time() - t0
            print(f"ERROR ({elapsed:.0f}s): {e}")
            strategy_errors.append(f"{sid}: {e}")
            results[sid] = []

    # Save whatever we got (even partial)
    output = {
        "date": now.strftime("%Y-%m-%d %H:%M"),
        "scan_date": str(scan_date.date()),
        "results": results,
        "errors": strategy_errors,
        "stocks_scanned": len(data),
    }
    save_json(SEL_FILE, output)
    print(f"  Saved -> {SEL_FILE} ({sum(len(v) for v in results.values())} total signals)")

    # Telegram (best effort)
    if is_configured():
        icons = {"donchian_adx": "📈", "keltner_rsi": "📊", "supertrend_volume": "📉"}
        for sid, sigs in results.items():
            sname = get_strategy_names().get(sid, sid)
            icon = icons.get(sid, "📋")
            try:
                if sigs:
                    lines = [f"{'🟢' if s['direction']=='LONG' else '🔴'} {s['symbol']} {s['direction']}" for s in sigs[:10]]
                    send_message(
                        f"{icon} *{sname} — {date_str}*\n{len(sigs)} signals:\n" + "\n".join(lines),
                        parse_mode="Markdown"
                    )
                else:
                    send_message(f"{icon} *{sname} — {date_str}*\nNo signals.", parse_mode="Markdown")
            except Exception:
                pass

    if strategy_errors:
        print(f"  {len(strategy_errors)} strategy error(s):")
        for e in strategy_errors:
            print(f"    {e}")

    tz_now = datetime.now(_TZ) if _TZ else datetime.now()
    print(f"[{tz_now.strftime('%H:%M:%S')}] SwingPortfolio Stock Selection complete")

    try:
        rebuild_db(DATA_DIR)
    except Exception:
        pass

if __name__ == "__main__":
    run()
