"""
Live Scanner — uses Shoonya API for near-real-time quotes.
1. 2-min candle scanner: flags stocks where range > 2% (original)
2. 15-min range breakout: flags breakouts on high-vol stocks with entry/SL/target
"""
import os, sys, json, time
from datetime import datetime, timedelta

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

# Top 40 high-volatility stocks for 15-min breakout strategy
# (pre-computed from hourly volatility ranking)
HIGH_VOL_STOCKS = {
    "TRENT", "KALYANKJIL", "GVT&D", "KAYNES", "KPITTECH", "AMBER",
    "POWERINDIA", "SAMMAANCAP", "PERSISTENT", "LODHA", "NUVAMA",
    "FORCEMOT", "INOXWIND", "PGEL", "COFORGE", "ADANIENSOL", "DIXON",
    "INFY", "SWIGGY", "MCX", "NATIONALUM", "ADANIGREEN", "ANGELONE",
    "LTM", "UNOMINDA", "ASHOKLEY", "PAYTM", "BDL", "NAUKRI",
    "MOTHERSON", "JUBLFOOD", "MOTILALOFS", "POLICYBZR", "COCHINSHIP",
    "SAIL", "PRESTIGE", "HINDPETRO", "TIINDIA", "BHARATFORG", "MUTHOOTFIN"
}

_broker_api = None
CYCLE_COUNT = 0          # tracks scanner cycles for 15-min window reset
CYCLES_PER_15MIN = 7     # reset 15-min window every ~7 cycles (14 min)


def get_api():
    global _broker_api
    if _broker_api is not None:
        return _broker_api
    from ganah import setup_api
    _broker_api = setup_api("SHOONYA", "FA138862")
    return _broker_api


def scan():
    global CYCLE_COUNT
    api = get_api()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")

    # Skip before 09:50
    if now.hour < 9 or (now.hour == 9 and now.minute < 50):
        return []

    # Load state
    state = {"date": "", "symbols": {}}
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                loaded = json.load(f)
                if loaded.get("date") == today:
                    state = loaded
    except Exception:
        pass
    if state.get("date") != today:
        state = {"date": today, "symbols": {}}
        CYCLE_COUNT = 0

    # Reset 15-min window every ~7 cycles
    CYCLE_COUNT += 1
    if CYCLE_COUNT > CYCLES_PER_15MIN:
        # Slide the 15-min window: keep only the latest quote as starting point
        for sym, s in state["symbols"].items():
            s["r15_h"] = s.get("h", s.get("p", 0))
            s["r15_l"] = s.get("l", s.get("p", 0))
        CYCLE_COUNT = 0

    scanner_results = []
    breakout_signals = []
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
            prev_close = float(q.get("uc", 0))
            if prev_close <= 0:
                prev_close = lp

            # ── 2-min rolling state ──
            sym_state = state["symbols"].get(clean, {"h": lp, "l": lp, "p": lp, "t": now.isoformat()})
            if "r15_h" not in sym_state:
                sym_state["r15_h"] = lp
                sym_state["r15_l"] = lp

            if lp > sym_state["h"]: sym_state["h"] = lp
            if lp < sym_state["l"]: sym_state["l"] = lp
            if lp > sym_state["r15_h"]: sym_state["r15_h"] = lp
            if lp < sym_state["r15_l"]: sym_state["r15_l"] = lp

            sym_state["p"] = lp
            sym_state["t"] = now.isoformat()
            state["symbols"][clean] = sym_state

            # 2-min scanner (original)
            rolling_range = sym_state["h"] - sym_state["l"]
            range_pct = rolling_range / prev_close * 100
            move_pct = (lp - prev_close) / prev_close * 100

            if range_pct >= MIN_RANGE_PCT:
                direction = "LONG" if lp > sym_state["l"] + rolling_range * 0.5 else "SHORT"
                scanner_results.append({
                    "symbol": clean, "ltp": round(lp, 2),
                    "prev_close": round(prev_close, 2),
                    "high": round(sym_state["h"], 2), "low": round(sym_state["l"], 2),
                    "range": round(rolling_range, 2), "range_pct": round(range_pct, 2),
                    "move_pct": round(move_pct, 2), "direction": direction,
                    "volume": q.get("v", "?"), "updated": now.strftime("%H:%M:%S"),
                })

            # ── 15-min range breakout (high-vol stocks only) ──
            if clean in HIGH_VOL_STOCKS:
                r15_range = sym_state["r15_h"] - sym_state["r15_l"]
                r15_range_pct = r15_range / prev_close * 100 if prev_close > 0 else 0
                r15_mid = (sym_state["r15_h"] + sym_state["r15_l"]) / 2

                if r15_range_pct >= 0.5:  # minimum range to consider
                    # LONG breakout: price breaks above 15-min high
                    if lp > sym_state["r15_h"] * 1.001:
                        entry = round(sym_state["r15_h"] * 1.001, 2)
                        sl_dist = r15_range * 0.5
                        sl = round(entry - sl_dist, 2)
                        target = round(entry + sl_dist * 2.0, 2)
                        span = round(abs(entry - sl), 2)
                        breakout_signals.append({
                            "symbol": clean, "type": "LONG BREAKOUT",
                            "entry": entry, "sl": sl, "target": target,
                            "span": span, "ltp": round(lp, 2),
                            "r15_high": round(sym_state["r15_h"], 2),
                            "r15_low": round(sym_state["r15_l"], 2),
                            "r15_range": round(r15_range, 2),
                            "r15_range_pct": round(r15_range_pct, 2),
                            "time": now.strftime("%H:%M:%S"),
                        })
                    # SHORT breakout: price breaks below 15-min low
                    elif lp < sym_state["r15_l"] * 0.999:
                        entry = round(sym_state["r15_l"] * 0.999, 2)
                        sl_dist = r15_range * 0.5
                        sl = round(entry + sl_dist, 2)
                        target = round(entry - sl_dist * 2.0, 2)
                        span = round(abs(entry - sl), 2)
                        breakout_signals.append({
                            "symbol": clean, "type": "SHORT BREAKOUT",
                            "entry": entry, "sl": sl, "target": target,
                            "span": span, "ltp": round(lp, 2),
                            "r15_high": round(sym_state["r15_h"], 2),
                            "r15_low": round(sym_state["r15_l"], 2),
                            "r15_range": round(r15_range, 2),
                            "r15_range_pct": round(r15_range_pct, 2),
                            "time": now.strftime("%H:%M:%S"),
                        })

        except Exception as e:
            errors += 1
            if errors > 10:
                break
            continue

    scanner_results.sort(key=lambda r: -r["range_pct"])
    breakout_signals.sort(key=lambda r: -r.get("r15_range_pct", 0))

    output = {
        "last_updated": now.strftime("%Y-%m-%d %H:%M:%S"),
        "scanned": len(UNIVERSE),
        "scanner": {"total_flagged": len(scanner_results), "min_range_pct": MIN_RANGE_PCT, "results": scanner_results},
        "breakout_15m": {"total_flagged": len(breakout_signals), "results": breakout_signals},
    }

    os.makedirs(os.path.dirname(RESULTS_FILE), exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(output, f, indent=2)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

    print(f"[{now.strftime('%H:%M:%S')}] Scanner: {len(scanner_results)} flags | Breakouts: {len(breakout_signals)} signals")
    return output


if __name__ == "__main__":
    print(f"Live Scanner started — {len(UNIVERSE)} stocks every {SCAN_INTERVAL}s")
    while True:
        try:
            scan()
        except Exception as e:
            print(f"  Error: {e}")
            import traceback
            traceback.print_exc()
        time.sleep(SCAN_INTERVAL)
