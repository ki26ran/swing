"""
Live Scanner — uses Shoonya API for near-real-time quotes.
Scans Nifty 200 universe every 2 minutes.
Flags stocks where 2-min candle range > 2% of previous close.
"""
import os, sys, json, time
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(BASE))

from common.universes import SYMBOLS_DATA
from cfg.universes import MARGIN_FILTERED_UNIVERSE

RESULTS_FILE = os.path.join(BASE, "data", "live_scanner_results.json")
STATE_FILE = os.path.join(BASE, "data", "live_scanner_state.json")

UNIVERSE = sorted(MARGIN_FILTERED_UNIVERSE) if MARGIN_FILTERED_UNIVERSE else sorted(SYMBOLS_DATA.keys())[:212]
SCAN_INTERVAL = 120
MIN_RANGE_PCT = 2.0

_broker_api = None


def get_api():
    global _broker_api
    if _broker_api is not None:
        return _broker_api
    from ganah import setup_api
    _broker_api = setup_api("SHOONYA", "FA138862")
    return _broker_api


def scan():
    """Fetch live quotes, track 2-min range, flag stocks > 2%."""
    api = get_api()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Load state (rolling high/low per symbol)
    state = {"date": "", "symbols": {}}
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                loaded = json.load(f)
                if loaded.get("date") == today:
                    state = loaded
    except Exception:
        pass

    # Reset state if new day
    if state.get("date") != today:
        state = {"date": today, "symbols": {}}

    results = []
    errors = 0

    for sym in UNIVERSE:
        clean = sym.replace(".NS", "").replace(".BO", "")
        try:
            q = api.get_quotes("NSE", f"{clean}-EQ")
            if not isinstance(q, dict) or q.get("stat") != "Ok":
                continue

            lp = float(q.get("lp", 0))
            if lp <= 0:
                continue

            # Get previous close from quote (uc = upper circuit = prev close in Shoonya)
            prev_close = float(q.get("uc", 0))
            if prev_close <= 0:
                prev_close = lp  # fallback

            # Update rolling high/low
            sym_state = state["symbols"].get(clean, {"h": lp, "l": lp, "t": now.isoformat()})

            if lp > sym_state["h"]:
                sym_state["h"] = lp
            if lp < sym_state["l"]:
                sym_state["l"] = lp

            sym_state["t"] = now.isoformat()
            state["symbols"][clean] = sym_state

            # Compute range % from rolling high/low
            rolling_range = sym_state["h"] - sym_state["l"]
            range_pct = rolling_range / prev_close * 100

            # Also compute instant move from previous close
            move_pct = (lp - prev_close) / prev_close * 100

            if range_pct >= MIN_RANGE_PCT:
                direction = "LONG" if lp > sym_state["l"] + rolling_range * 0.5 else "SHORT"
                results.append({
                    "symbol": clean,
                    "ltp": round(lp, 2),
                    "prev_close": round(prev_close, 2),
                    "high": round(sym_state["h"], 2),
                    "low": round(sym_state["l"], 2),
                    "range": round(rolling_range, 2),
                    "range_pct": round(range_pct, 2),
                    "move_pct": round(move_pct, 2),
                    "direction": direction,
                    "volume": q.get("v", "?"),
                    "updated": now.strftime("%H:%M:%S"),
                })

        except Exception as e:
            errors += 1
            if errors > 10:
                break
            continue

    # Sort by range_pct descending
    results.sort(key=lambda r: -r["range_pct"])

    output = {
        "last_updated": now.strftime("%Y-%m-%d %H:%M:%S"),
        "total_flagged": len(results),
        "scanned": len(UNIVERSE),
        "min_range_pct": MIN_RANGE_PCT,
        "results": results,
    }

    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(output, f, indent=2)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

    print(f"[{now.strftime('%H:%M:%S')}] Scanner: {len(results)}/{len(UNIVERSE)} flagged (>{MIN_RANGE_PCT}%)")
    return results


if __name__ == "__main__":
    print(f"Live Scanner started — scanning {len(UNIVERSE)} stocks every {SCAN_INTERVAL}s via Shoonya API")
    while True:
        try:
            scan()
        except Exception as e:
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()
        time.sleep(SCAN_INTERVAL)
