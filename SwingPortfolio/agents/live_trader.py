"""
SwingPortfolio - Unified Live Trader.
Runs each strategy independently, monitors positions, sends Telegram alerts.
"""
import os, sys, json, time, csv
from datetime import datetime, timedelta
import pandas as pd
try:
    import zoneinfo
    _TZ = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    _TZ = None
    print("  [WARN] zoneinfo(Asia/Kolkata) unavailable — using naive datetime (may be UTC)")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

ROOT = os.path.dirname(BASE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from common.market_data.cache import get_cache

from cfg.settings import (CAPITAL_TOTAL, MAX_POSITIONS_PER_STRATEGY, MAX_POSITIONS_TOTAL,
                           TRAIL_PCT, RR_RATIO, HARD_SL_ENABLED, HARD_SL_AMOUNT, GAP_FILTER_ENABLED,
                           STRATEGY_WEIGHTS, CAPITAL_MODE, POSITION_SIZING, OPTION_STRIKE, PRODUCT_TYPE,
                           MAX_MARGIN_PER_STOCK, MARGIN_RATE,
                           SL_ATR_MULTIPLIER, FALLBACK_CUTOFF)
from core.registry import get_all_strategies, get_strategy_names
from core.notifications import send_message, is_configured
from cfg.universes import get_lot_size
from core.atomic import save_json as _save_json, save_csv as _save_csv, load_json as _load_json

from live.cache import get_swing_cache
_swing_cache = get_swing_cache()
DATA_DIR = os.path.join(BASE, "data")
os.makedirs(DATA_DIR, exist_ok=True)

SEL_FILE = os.path.join(DATA_DIR, "selections.json")
POSITIONS_FILE = os.path.join(DATA_DIR, "live_positions.json")
HISTORY_FILE = os.path.join(DATA_DIR, "positions_history.json")
SIGNALS_FILE = os.path.join(DATA_DIR, "signals.csv")
STRAT_CFG_FILE = os.path.join(BASE, "cfg", "strategies.json")

STRAT_SHORT = {"donchian_adx": "DADX", "keltner_rsi": "KRSI", "supertrend_volume": "STVOL"}
_broker_api_cache = {}


def _load_strategy_config():
    with open(STRAT_CFG_FILE) as f:
        return {e["id"]: e for e in json.load(f)["strategies"]}


def _get_broker_api(strat_cfg):
    live = strat_cfg.get("live", False)
    if not live:
        return None
    bn = strat_cfg.get("broker_name", "SHOONYA")
    bu = strat_cfg.get("broker_username", "FA138862")
    cache_key = f"{bn}|{bu}"
    if cache_key in _broker_api_cache:
        return _broker_api_cache[cache_key]
    try:
        from ganah import setup_api
        api = setup_api(bn, bu)
        _broker_api_cache[cache_key] = api
        return api
    except Exception as e:
        print(f"  Broker login failed for {bn}/{bu}: {e}")
        return None


def _place_entry_swing(api, sym, qty, direction, strat_id, entry_price, sl, target, option_symbol=None):
    if api is None:
        return None
    try:
        side = "LONG" if direction == "LONG" else "SHORT"
        ss = STRAT_SHORT.get(strat_id, strat_id[:4].upper())
        trading_symbol = option_symbol if option_symbol else f"{sym}-EQ"
        remarks = f"{ss}_ENT_{sym}_{entry_price:.0f}"
        from ganah import place_live_order
        oid = place_live_order(
            trading_symbol, side, qty, remarks,
            exchange="NSE", product_type=PRODUCT_TYPE, price_type="MKT"
        )
        return oid
    except Exception as e:
        print(f"  Order placement failed for {sym}: {e}")
        return None


def _place_exit_swing(api, sym, qty, direction, reason, strat_id, exit_price, option_symbol=None):
    if api is None:
        return None
    try:
        side = "SHORT" if direction == "LONG" else "LONG"
        ss = STRAT_SHORT.get(strat_id, strat_id[:4].upper())
        reason_short = {"TRAIL": "TRL", "TARGET": "TGT", "MAX_HOLD": "MHLD", "SL": "SL"}.get(reason, reason[:3])
        trading_symbol = option_symbol if option_symbol else f"{sym}-EQ"
        remarks = f"{ss}_{reason_short}_{sym}_{exit_price:.0f}"
        from ganah import place_live_order
        oid = place_live_order(
            trading_symbol, side, qty, remarks,
            exchange="NSE", product_type=PRODUCT_TYPE, price_type="MKT"
        )
        return oid
    except Exception as e:
        print(f"  Exit order failed for {sym}: {e}")
        return None


def save_json(path, data):
    if path == POSITIONS_FILE and isinstance(data, list):
        _swing_cache.save_positions(data)
    elif path == HISTORY_FILE and isinstance(data, list):
        _swing_cache.save_history(data)
    _save_json(path, data)

def load_json(path, default=None):
    if path == POSITIONS_FILE:
        db = _swing_cache.load_positions()
        return db if db else _load_json(path, default) or default
    if path == HISTORY_FILE:
        db = _swing_cache.load_history()
        return db if db else _load_json(path, default) or default
    return _load_json(path, default)

def save_csv(path, fieldnames, rows):
    if path == SIGNALS_FILE:
        for r in rows:
            _swing_cache.append_signal(r)
    _save_csv(path, fieldnames, rows)


CHECK_INTERVAL = 60
CLOSE_HOUR, CLOSE_MIN = 15, 15  # Monitor stops 15 min before NSE close (15:30) as safety buffer
_RETRY_COUNT = 30  # Retry unfilled signals every 5 min for ~150 min (covers 08:50→11:20; enough 1m data from 09:35)

_strategy_names = get_strategy_names()


# Module-level: store unfilled signals for the monitor to re-check
_unfilled_signals = []  # list of (sel_dict, strategy_id, entry_mode, capital, retry_count)


def _fetch_intraday_cached(ticker, interval, start, end):
    """Fetch intraday data from DuckDB cache. No fallback — DuckDB is the single source of truth."""
    try:
        cache = get_cache()
        start_str = start.strftime('%Y-%m-%d') if not isinstance(start, str) else start
        end_str = end.strftime('%Y-%m-%d') if not isinstance(end, str) else end
        clean = ticker.replace('.NS', '').replace('.BO', '')
        cache.ensure_fresh([clean], interval)
        df = cache.get_intraday_for_backtest(clean, start_str, interval)
        if df is not None and not df.empty:
            return df
    except Exception as e:
        print(f"  [RED ALERT] DuckDB intraday fetch failed for {ticker}: {e}")
    print(f"  [RED ALERT] DuckDB has no intraday data for {ticker} on {start_str}")
    print(f"  [RED ALERT] sync_job should have populated this. Check:")
    print(f"  [RED ALERT]   1. Is sync_job running? (check heartbeat)")
    print(f"  [RED ALERT]   2. Is config.json portfolio_providers correct?")
    print(f"  [RED ALERT]   3. Is broker API working? (check sync.log)")
    return None


def _now():
    return datetime.now(_TZ) if _TZ else datetime.now()


def _now_str():
    return _now().strftime("%H:%M:%S")


def _capital_for_strategy(strategy_id):
    if CAPITAL_MODE == "equal":
        n = len(list(get_all_strategies()))
        return CAPITAL_TOTAL / n if n > 0 else CAPITAL_TOTAL
    return CAPITAL_TOTAL * STRATEGY_WEIGHTS.get(strategy_id, 1.0 / len(list(get_all_strategies())))


def _zscore_entry(df, direction, threshold, baseline_start="09:15"):
    """Compute z-score from first 15 candles starting at baseline_start, return (price, time) when |z| > threshold.
    threshold=2.0 for 1m, 1.0 for 5m. baseline_start="09:15" for 1m, "09:30" for 5m pure mode."""
    if df is None or len(df) < 20:
        return None, None
    try:
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_convert("Asia/Kolkata")
        df = df.between_time(baseline_start, "15:30")
        if len(df) < 20:
            return None, None
        mu, sigma = df.iloc[:15]["Close"].mean(), df.iloc[:15]["Close"].std()
        if sigma == 0:
            return None, None
        for i in range(15, len(df)):
            z = (df.iloc[i]["Close"] - mu) / sigma
            if direction == "LONG" and z > threshold:
                return float(df.iloc[i]["Close"]), str(df.index[i].time())[:5]
            if direction == "SHORT" and z < -threshold:
                return float(df.iloc[i]["Close"]), str(df.index[i].time())[:5]
        # Time-based fallback
        cutoff_time = pd.Timestamp(FALLBACK_CUTOFF).time()
        last_idx_time = df.index[-1].time() if hasattr(df.index[-1], "time") else None
        if last_idx_time and last_idx_time >= cutoff_time:
            last = df.iloc[-1]
            return float(last["Close"]), str(last.name.time())[:5] if hasattr(last.name, "time") else FALLBACK_CUTOFF
        return None, None
    except Exception:
        return None, None


def _get_entry_price_live(sym, direction, date_str, entry_mode):
    """Get entry price using the configured method. Returns (price, time_str) or (None, None)."""
    sym_ns = f"{sym}.NS"
    end_str = (pd.Timestamp(date_str) + timedelta(1)).strftime("%Y-%m-%d")

    if entry_mode == "15m_945":
        df = _fetch_intraday_cached(sym_ns, "15m", date_str, end_str)
        if df is not None and not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.index = pd.to_datetime(df.index)
            if df.index.tz is not None:
                df.index = df.index.tz_convert("Asia/Kolkata")
            target = pd.Timestamp(date_str).replace(hour=9, minute=45).tz_localize("Asia/Kolkata")
            if target in df.index:
                val = df.loc[target, "Open"]
                price = float(val.iloc[0]) if isinstance(val, pd.Series) else float(val)
                return price, "09:45"
            if len(df) > 0:
                return float(df.iloc[0]["Open"]), df.index[0].strftime("%H:%M")
        return None, None

    if entry_mode == "zscore_5m_pure":
        df5 = _fetch_intraday_cached(sym_ns, "5m", date_str, end_str)
        if df5 is not None and len(df5) >= 20:
            return _zscore_entry(df5, direction, 1.0, baseline_start="09:30")
        return None, None

    if entry_mode in ("zscore", "walk"):
        df = _fetch_intraday_cached(sym_ns, "1m", date_str, end_str)
        if df is None or len(df) < 20:
            # Fallback to 5m with adjusted threshold
            df5 = _fetch_intraday_cached(sym_ns, "5m", date_str, end_str)
            if df5 is not None and len(df5) >= 20:
                return _zscore_entry(df5, direction, 1.0)
            return None, None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_convert("Asia/Kolkata")
        df = df.between_time("09:15", "15:30")
        if len(df) < 20:
            df5 = _fetch_intraday_cached(sym_ns, "5m", date_str, end_str)
            if df5 is not None and len(df5) >= 20:
                return _zscore_entry(df5, direction, 1.0)
            return None, None

        if entry_mode == "zscore":
            return _zscore_entry(df, direction, 2.0)

        if entry_mode == "walk":
            base = df.iloc[:15]["Close"]
            for i in range(15, len(df)):
                c = df.iloc[i]["Close"]
                if direction == "LONG" and c > base.max():
                    return float(c), str(df.index[i].time())[:5]
                if direction == "SHORT" and c < base.min():
                    return float(c), str(df.index[i].time())[:5]
            return None, None

    return None, None


def _load_all_selections():
    """Load selections from per-strategy files. Returns merged dict matching combined format."""
    combined = {"date": _now().strftime("%Y-%m-%d"), "results": {}}
    sdir = os.path.join(DATA_DIR, "..", "cfg", "strategies.json")
    if not os.path.exists(sdir):
        sdir = os.path.join(DATA_DIR, "..", "cfg", "default_strategies.json")
    try:
        with open(sdir) as f:
            strat_cfgs = json.load(f) if isinstance(json.load(f), list) else []
    except Exception:
        strat_cfgs = []
    strat_names = {s.get("id"): s.get("name", s.get("id", "?")) for s in strat_cfgs}

    for sid in ["donchian_adx", "keltner_rsi", "supertrend_volume"]:
        sf = os.path.join(DATA_DIR, f"selections_{sid}.json")
        if os.path.exists(sf):
            try:
                with open(sf) as f:
                    sd = json.load(f)
                combined["results"][sid] = {
                    "name": strat_names.get(sid, sid),
                    "data": sd.get("data", []),
                }
            except Exception:
                pass
    return combined


def _phase_entry():
    now = _now()
    sel_data = _load_all_selections()
    results = sel_data.get("results", {})
    if not results:
        print("  No selection files found.")
        return
    date_str = now.strftime("%Y-%m-%d")
    all_positions = []
    all_rows = []

    from core.registry import get_all_strategies as _get_strats
    _unfilled_signals.clear()
    # Count current open positions across ALL strategies for global cap enforcement
    existing_all_for_global = load_json(POSITIONS_FILE, [])
    global_open_count = sum(1 for p in existing_all_for_global
                            if p.get("status") == "OPEN")
    global_open_today = sum(1 for p in existing_all_for_global
                            if p.get("status") == "OPEN" and p.get("entry_date") == date_str)

    strat_cfgs = _load_strategy_config()
    broker_apis = {}
    for sid, sc in strat_cfgs.items():
        broker_apis[sid] = _get_broker_api(sc)

    for strat in _get_strats():
        sid = strat.strategy_id
        selections = results.get(sid, {}).get("data", []) if isinstance(results.get(sid), dict) else results.get(sid, [])
        if not selections:
            print(f"  [{sid}] No selections.")
            continue
        capital = _capital_for_strategy(sid)
        max_pos = min(MAX_POSITIONS_PER_STRATEGY, MAX_POSITIONS_TOTAL)
        print(f"  [{sid}] Capital=Rs.{capital:,.0f} MaxPos={max_pos} (global open: {global_open_count}/{MAX_POSITIONS_TOTAL})")

        # Count today's existing entries for this strategy
        existing_all = load_json(POSITIONS_FILE, [])
        today_existing = {p.get("symbol","")
                          for p in existing_all
                          if p.get("strategy") == sid and p.get("entry_date") == date_str}
        # Also count carry-forward OPEN positions (from prior days) — they count against max
        carry_forward = {p.get("symbol","")
                         for p in existing_all
                         if p.get("strategy") == sid
                         and p.get("status") == "OPEN"
                         and p.get("entry_date") != date_str}
        # Total strategy positions that count against max
        total_open_for_strat = len(today_existing) + len(carry_forward)
        positions, rows = [], []
        entry_mode = getattr(strat, '_entry_time', 'zscore')
        print(f"    entry_mode={entry_mode} (today={len(today_existing)}, carry={len(carry_forward)}, max={max_pos})")
        for sel in selections[:max_pos]:
            # Refresh position state from file to prevent duplicate entries
            existing_all = load_json(POSITIONS_FILE, [])
            today_existing = {p.get("symbol","")
                              for p in existing_all
                              if p.get("strategy") == sid and p.get("entry_date") == date_str}
            carry_forward = {p.get("symbol","")
                             for p in existing_all
                             if p.get("strategy") == sid
                             and p.get("status") == "OPEN"
                             and p.get("entry_date") != date_str}
            total_open_for_strat = len(today_existing) + len(carry_forward)
            # Skip if already at max (counts both today + carry-forward)
            if total_open_for_strat >= max_pos:
                print(f"    {sel['symbol']} ({sel['direction']})...skipped (max={max_pos} reached for {sid}: {total_open_for_strat} open)"); break
            # Global cap: skip if total open positions across all strategies >= MAX_POSITIONS_TOTAL
            if global_open_count >= MAX_POSITIONS_TOTAL:
                print(f"    {sel['symbol']} ({sel['direction']})...skipped (GLOBAL cap={MAX_POSITIONS_TOTAL} reached: {global_open_count} open across all strategies)"); break
            if sel["symbol"] in today_existing or sel["symbol"] in carry_forward:
                print(f"    {sel['symbol']} ({sel['direction']})...skipped (already in {sid} positions)"); continue
            sym = sel["symbol"]
            direction = sel["direction"]
            print(f"    {sym} ({direction})...", end=" ", flush=True)
            try:
                entry_price, entry_time_str = _get_entry_price_live(sym, direction, date_str, entry_mode)
                if entry_price is None:
                    _unfilled_signals.append((sel, sid, entry_mode, capital, 0))
                    print("no entry price (will retry)"); continue

                if GAP_FILTER_ENABLED:
                    prev_close = sel.get("close_price", entry_price)
                    if prev_close and abs(entry_price - prev_close) / prev_close > 0.03:
                        print(f"gap {abs(entry_price-prev_close)/prev_close*100:.1f}%"); continue

                option_symbol = None
                if POSITION_SIZING == "lot":
                    lot_size = get_lot_size(sym)
                    if lot_size is None:
                        print(f"no lot data, skipping"); continue
                    margin = lot_size * entry_price * MARGIN_RATE
                    if margin > MAX_MARGIN_PER_STOCK:
                        print(f"margin Rs.{margin:,.0f} > Rs.{MAX_MARGIN_PER_STOCK:,}, skipping"); continue
                    qty = lot_size
                elif POSITION_SIZING == "options":
                    lot_size = get_lot_size(sym)
                    if lot_size is None:
                        print(f"no lot data, skipping"); continue
                    from core.backtest_engine import _option_premium as _opt_prem
                    from common.market_data.cache import get_cache
                    nfo_cache = get_cache()
                    nfo = nfo_cache.resolve_option_contract(sym, entry_price, direction, strike_mode=OPTION_STRIKE)
                    if nfo:
                        option_symbol = nfo["trading_symbol"]
                        lot_size = nfo["lot_size"]
                        opt_sizing = {"strike": nfo["strike_price"], "expiry": str(nfo["expiry"]),
                                      "delta": 0.75 if OPTION_STRIKE == "ITM1" else 0.5,
                                      "tv_pct": 0.02, "label": f"{OPTION_STRIKE}"}
                    else:
                        print(f"  No NFO contract found for {sym}, skipping")
                        continue
                    option_premium = _opt_prem(entry_price, opt_sizing["strike"], direction, opt_sizing["tv_pct"])
                    qty = lot_size
                    print(f"  Options: {option_symbol} premium=Rs.{option_premium:.2f}")
                else:
                    qty = max(1, int(capital / entry_price / max_pos))
                atr_est = entry_price * 0.02
                # Per-strategy SL/RR (fallback to cfg)
                sl_mult = getattr(strat, 'sl_atr_multiplier', SL_ATR_MULTIPLIER)
                rr_r = getattr(strat, 'rr_ratio', RR_RATIO)
                sl_price = entry_price - atr_est * sl_mult if direction == "LONG" else entry_price + atr_est * sl_mult
                target_price = entry_price + atr_est * sl_mult * rr_r if direction == "LONG" else entry_price - atr_est * sl_mult * rr_r
                if HARD_SL_ENABLED and HARD_SL_AMOUNT > 0:
                    ml = HARD_SL_AMOUNT / qty
                    sl_price = max(sl_price, entry_price - ml) if direction == "LONG" else min(sl_price, entry_price + ml)

                api = broker_apis.get(sid)
                broker_order_id = None
                opt_sym_for_order = option_symbol if POSITION_SIZING == "options" and option_symbol else None
                if api:
                    broker_order_id = _place_entry_swing(api, sym, qty, direction, sid,
                                                         entry_price, sl_price, target_price,
                                                         option_symbol=opt_sym_for_order)

                row_entry = {"symbol": sym, "direction": direction, "entry": round(entry_price, 2),
                    "sl": round(sl_price, 2), "target": round(target_price, 2), "qty": qty,
                    "entry_time": entry_time_str, "strategy": sid,
                    "scanned_at": now.strftime("%Y-%m-%d %H:%M:%S")}
                if POSITION_SIZING == "options":
                    row_entry["option_symbol"] = option_symbol
                    row_entry["option_premium"] = round(option_premium, 2)
                rows.append(row_entry)

                strat_cfg = strat_cfgs.get(sid, {})
                pos_entry = {"symbol": sym, "direction": direction, "entry_price": round(entry_price, 2),
                    "initial_sl": round(sl_price, 2), "target": round(target_price, 2),
                    "trail_sl": round(sl_price, 2), "best_price": round(entry_price, 2),
                    "qty": qty, "entry_time": entry_time_str, "entry_date": date_str,
                    "status": "OPEN", "last_candle_idx": 0, "last_checked": _now_str(),
                    "strategy": sid, "last_price": round(entry_price, 2),
                    "live": strat_cfg.get("live", False),
                    "broker_name": strat_cfg.get("broker_name", ""),
                    "broker_username": strat_cfg.get("broker_username", ""),
                    "broker_order_id": broker_order_id}
                if POSITION_SIZING == "options":
                    pos_entry["option_strike"] = opt_sizing["strike"]
                    pos_entry["option_delta"] = opt_sizing["delta"]
                    pos_entry["option_expiry"] = opt_sizing["expiry"]
                    pos_entry["option_label"] = opt_sizing["label"]
                    pos_entry["option_type"] = opt_type
                    pos_entry["option_symbol"] = option_symbol
                    pos_entry["option_entry_premium"] = round(option_premium, 2)
                    pos_entry["tv_pct"] = opt_sizing["tv_pct"]
                positions.append(pos_entry)
                global_open_count += 1
                # Save immediately to prevent duplicate entries from restarts
                if all_positions or positions:
                    combined = all_positions + positions
                    # Dedup by (symbol, strategy, entry_date) before saving
                    seen = set()
                    deduped = []
                    for p in combined:
                        key = (p.get("symbol",""), p.get("strategy",""), p.get("entry_date",""))
                        if key in seen:
                            continue
                        seen.add(key)
                        deduped.append(p)
                    save_json(POSITIONS_FILE, deduped)
                print(f"@ Rs.{entry_price:,.2f} qty={qty} (global: {global_open_count}/{MAX_POSITIONS_TOTAL})")
            except Exception as e:
                print(f"Error: {e}")

        all_rows.extend(rows)
        all_positions.extend(positions)

    if not all_rows:
        print("  No entries.")
        # Don't return — keep existing positions for monitor
        if not load_json(POSITIONS_FILE, []):
            return

    csv_fields = ["symbol", "direction", "entry", "sl", "target", "qty", "entry_time", "strategy", "scanned_at"]
    if POSITION_SIZING == "options":
        csv_fields += ["option_symbol", "option_premium"]
    save_csv(SIGNALS_FILE, csv_fields, all_rows)
    # Merge with existing positions — prevent same-day duplicate re-entry
    existing = load_json(POSITIONS_FILE, [])
    date_str = now.strftime("%Y-%m-%d")
    existing_keys = {(p.get("symbol",""), p.get("strategy",""))
                     for p in existing if p.get("entry_date") == date_str}
    for np in all_positions:
        key = (np["symbol"], np["strategy"])
        if key not in existing_keys:
            existing.append(np)
            existing_keys.add(key)
    save_json(POSITIONS_FILE, existing)
    print(f"  Entered {len(all_positions)} new positions ({len(existing)} total in file)")

    if is_configured():
        strat_icons = {"donchian_adx": "📈", "keltner_rsi": "📊", "supertrend_volume": "📉"}
        for r in all_rows:
            dir_icon = "🟢" if r["direction"] == "LONG" else "🔴"
            sid = r.get("strategy", "")
            strat_icon = strat_icons.get(sid, "")
            sname = _strategy_names.get(sid, sid)
            opt_line = f"\nOption: {r.get('option_symbol', '')}" if POSITION_SIZING == "options" and r.get("option_symbol") else ""
            prem_line = f"\nPremium: Rs.{r.get('option_premium', 0):.2f}" if POSITION_SIZING == "options" and r.get("option_premium") else ""
            send_message(
                f"{dir_icon}{strat_icon} *{sname} Entry — {r['symbol']}*\n{r['direction']}\nEntry: Rs.{r['entry']:,.2f}\nSL: Rs.{r['sl']:,.2f}\nQty: {r['qty']}{opt_line}{prem_line}",
                parse_mode="Markdown"
            )


def _fetch_1m(sym, date_str):
    try:
        df = _fetch_intraday_cached(f"{sym}.NS", "1m", date_str,
                                    (pd.Timestamp(date_str) + timedelta(1)).strftime("%Y-%m-%d"))
        if df is None:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_convert("Asia/Kolkata")
        return df.between_time("09:15", "15:30")
    except Exception:
        return None


def _check_sl(pos, date_str):
    sym = pos["symbol"]
    direction = pos["direction"]
    trail = pos.get("trail_sl", pos["initial_sl"])
    best = pos.get("best_price", pos["entry_price"])
    target_price = pos.get("target")
    # Per-strategy trail_pct (fallback to cfg)
    sid = pos.get("strategy", "")
    trail_pct = TRAIL_PCT
    max_hold_days = None
    if sid:
        from core.registry import get_strategy as _get_strat_by_id
        _sobj = _get_strat_by_id(sid)
        if _sobj is not None:
            trail_pct = max(0.001, min(0.5, getattr(_sobj, 'trail_pct', TRAIL_PCT)))
            max_hold_days = getattr(_sobj, 'max_hold_days', None)
    df = _fetch_1m(sym, date_str)
    if df is None or len(df) < 5:
        return False, None, None, None
    eh, em = map(int, pos.get("entry_time", "09:45").split(":"))
    entry_idx = 0
    for i in range(len(df)):
        t = df.iloc[i].name
        if t.hour > eh or (t.hour == eh and t.minute >= em):
            entry_idx = i
            break
    last_idx = pos.get("last_candle_idx", entry_idx - 1)
    for i in range(max(last_idx + 1, entry_idx), len(df)):
        row = df.iloc[i]
        h, l = float(row["High"]), float(row["Low"])
        t = row.name
        if i > entry_idx:
            if direction == "LONG":
                if h > best:
                    best = h
                    trail = max(trail, h * (1 - trail_pct))
            else:
                if l < best:
                    best = l
                    trail = min(trail, l * (1 + trail_pct))
        if direction == "LONG":
            if target_price and h >= target_price:
                return True, target_price, str(t.time())[:5], "TARGET"
            if l <= trail:
                return True, trail, str(t.time())[:5], "SL"
        else:
            if target_price and l <= target_price:
                return True, target_price, str(t.time())[:5], "TARGET"
            if h >= trail:
                return True, trail, str(t.time())[:5], "SL"
        last_idx = i
    pos["best_price"] = round(best, 2)
    pos["trail_sl"] = round(trail, 2)
    pos["last_candle_idx"] = last_idx
    pos["last_checked"] = _now_str()
    pos["last_price"] = round(float(df.iloc[-1]["Close"]), 2)
    # max_hold check
    if max_hold_days is not None and pos.get("entry_date") and date_str:
        try:
            ed = pd.to_datetime(pos["entry_date"]).date()
            cd = pd.to_datetime(date_str).date()
            if (cd - ed).days >= max_hold_days:
                ex_p = float(df.iloc[-1]["Close"])
                return True, ex_p, str(df.iloc[-1].name.time())[:5], "MAX_HOLD"
        except Exception:
            pass
    return False, None, None, None


def _send_exit_alert(pos, exit_price, exit_time, reason):
    qty = pos.get("qty", int(CAPITAL_TOTAL / len(get_all_strategies()) / pos["entry_price"]))
    is_option = pos.get("option_strike") is not None
    if is_option:
        from core.backtest_engine import _option_premium as _opt_prem
        entry_prem = pos.get("option_entry_premium", 0)
        if entry_prem == 0:
            entry_prem = _opt_prem(pos["entry_price"], pos["option_strike"], pos["direction"], pos.get("tv_pct", 0.02))
        exit_prem = _opt_prem(exit_price, pos["option_strike"], pos["direction"], pos.get("tv_pct", 0.02))
        pnl = round((exit_prem - entry_prem) * qty, 2)
        entry_display = entry_prem
        exit_display = exit_prem
    else:
        pnl = round((exit_price - pos["entry_price"]) * qty, 2) if pos["direction"] == "LONG" else round((pos["entry_price"] - exit_price) * qty, 2)
        entry_display = pos["entry_price"]
        exit_display = exit_price

    # Place broker exit order if live
    if pos.get("live"):
        bn = pos.get("broker_name", "SHOONYA")
        bu = pos.get("broker_username", "FA138862")
        api = _get_broker_api({"live": True, "broker_name": bn, "broker_username": bu})
        if api:
            opt_sym_for_exit = pos.get("option_symbol") if is_option else None
            _place_exit_swing(api, pos["symbol"], qty, pos["direction"], reason,
                              pos.get("strategy", ""), exit_price, option_symbol=opt_sym_for_exit)

    pos["pnl"] = pnl
    pos["status"] = "CLOSED"
    pos["exit_price"] = round(exit_price, 2)
    pos["exit_time"] = exit_time
    pos["exit_reason"] = reason
    pnl_emoji = "✅" if pnl > 0 else "❌"
    icon_dir = "🟢" if pos["direction"] == "LONG" else "🔴"
    strat_icons = {"donchian_adx": "📈", "keltner_rsi": "📊", "supertrend_volume": "📉"}
    strat_icon = strat_icons.get(pos.get("strategy", ""), "")
    sname = _strategy_names.get(pos.get("strategy", ""), pos.get("strategy", ""))
    entry_label = f"Entry (Stock): Rs.{pos['entry_price']:,.2f}" if not is_option else f"Entry: Rs.{pos['entry_price']:,.2f} | Premium: Rs.{entry_display:,.2f}"
    exit_label = f"Exit (Stock): Rs.{exit_price:,.2f}" if not is_option else f"Exit: Rs.{exit_price:,.2f} | Premium: Rs.{exit_display:,.2f}"
    opt_line = f"\nOption: {pos.get('option_symbol', '')}" if is_option else ""
    msg = f"{pnl_emoji}{icon_dir}{strat_icon} *{sname} Exit — {pos['symbol']}*\n{pos['direction']}\n{entry_label}\n{exit_label}{opt_line}\nTime: {exit_time}\nReason: {reason}\nP&L: Rs.{pnl:+,.0f}"
    if is_configured():
        send_message(msg, parse_mode="Markdown")
    try:
        history = load_json(HISTORY_FILE, [])
        if not isinstance(history, list):
            history = []
        hist_keys = ["symbol", "direction", "entry_price", "qty", "initial_sl", "target",
                     "entry_time", "entry_date", "exit_price", "exit_time", "exit_reason", "pnl", "strategy"]
        if is_option:
            hist_keys += ["option_strike", "option_delta", "option_expiry", "option_type", "option_symbol", "option_entry_premium", "tv_pct"]
        hist_entry = {k: pos.get(k) for k in hist_keys}
        hist_entry["pnl"] = round(pnl, 0)
        history.append(hist_entry)
        save_json(HISTORY_FILE, history)
    except Exception:
        pass
    return pos


def _phase_monitor():
    now = _now()
    date_str = now.strftime("%Y-%m-%d")
    close_time = now.replace(hour=CLOSE_HOUR, minute=CLOSE_MIN, second=0)
    if now >= close_time:
        print(f"[{_now_str()}] Past market close. Skipping monitor.")
        return
    try:
        positions = load_json(POSITIONS_FILE, [])
    except Exception:
        return
    open_positions = [p for p in positions if p.get("status") != "CLOSED"]
    if not open_positions:
        print("  No positions to monitor.")
        if not _unfilled_signals:
            return
        print(f"  {len(_unfilled_signals)} unfilled signals — will retry periodically")

    _last_retry_time = None
    _retry_interval = timedelta(minutes=5)

    while _now() < close_time:
        changed = False

        # Retry unfilled signals every 5 minutes
        if _unfilled_signals:
                now_time = _now()
                if _last_retry_time is None or now_time - _last_retry_time >= _retry_interval:
                    _last_retry_time = now_time
                    print(f"  [{_now_str()}] Retrying {len(_unfilled_signals)} unfilled signals...")
                    # Track entries per strategy for max-pos enforcement
                    # Includes BOTH today's entries AND carry-forward positions from prior days
                    today_entries = {}
                    for p in positions:
                        if p.get("status") == "OPEN":
                            sid2 = p.get("strategy", "")
                            sym2 = p.get("symbol", "")
                            if sym2:
                                today_entries.setdefault(sid2, set()).add(sym2)
                    still_unfilled = []
                # Defensive dedup: skip if (sym, sid) already processed in this retry block
                _processed_in_retry = set()
                for sel, sid, entry_mode, capital, retry_n in _unfilled_signals:
                    sym = sel["symbol"]
                    direction = sel["direction"]
                    _dedup_key = (sym, sid)
                    if retry_n >= _RETRY_COUNT:
                        continue  # drop: exceeded max retries
                    if _dedup_key in _processed_in_retry:
                        continue
                    _processed_in_retry.add(_dedup_key)
                    if sym in today_entries.get(sid, set()):
                        continue
                    max_pos = min(MAX_POSITIONS_PER_STRATEGY, MAX_POSITIONS_TOTAL)
                    if len(today_entries.get(sid, set())) >= max_pos:
                        continue
                    ep, et = _get_entry_price_live(sym, direction, date_str, entry_mode)
                    if ep is None:
                        still_unfilled.append((sel, sid, entry_mode, capital, retry_n + 1))
                        continue
                    if GAP_FILTER_ENABLED:
                        prev_close = sel.get("close_price", ep)
                        if prev_close and abs(ep - prev_close) / prev_close > 0.03:
                            continue
                    lot = get_lot_size(sym)
                    if lot is None:
                        continue
                    if POSITION_SIZING != "options":
                        margin = lot * ep * MARGIN_RATE
                        if margin > MAX_MARGIN_PER_STOCK:
                            continue
                    qty = lot
                    atr_est = ep * 0.02
                    # Per-strategy SL/RR (fallback to cfg)
                    from core.registry import get_strategy as _get_strat_by_id
                    _strat_obj = _get_strat_by_id(sid)
                    sl_mult2 = getattr(_strat_obj, 'sl_atr_multiplier', SL_ATR_MULTIPLIER)
                    rr_r2 = getattr(_strat_obj, 'rr_ratio', RR_RATIO)
                    sl_price = ep - atr_est * sl_mult2 if direction == "LONG" else ep + atr_est * sl_mult2
                    target_price = ep + atr_est * sl_mult2 * rr_r2 if direction == "LONG" else ep - atr_est * sl_mult2 * rr_r2
                    if HARD_SL_ENABLED and HARD_SL_AMOUNT > 0:
                        ml = HARD_SL_AMOUNT / qty
                        sl_price = max(sl_price, ep - ml) if direction == "LONG" else min(sl_price, ep + ml)
                    entry_time_str = et
                    new_pos = {"symbol": sym, "direction": direction, "entry_price": round(ep, 2),
                        "initial_sl": round(sl_price, 2), "target": round(target_price, 2),
                        "trail_sl": round(sl_price, 2), "best_price": round(ep, 2),
                        "qty": qty, "entry_time": entry_time_str, "entry_date": date_str,
                        "status": "OPEN", "last_candle_idx": 0, "last_checked": _now_str(),
                        "strategy": sid, "last_price": round(ep, 2)}
                    if POSITION_SIZING == "options":
                        from core.backtest_engine import _option_premium as _opt_prem
                        from common.market_data.cache import get_cache
                        nfo_cache = get_cache()
                        nfo = nfo_cache.resolve_option_contract(sym, ep, direction, strike_mode=OPTION_STRIKE)
                        if nfo:
                            retry_option_symbol = nfo["trading_symbol"]
                            opt_sizing = {"strike": nfo["strike_price"], "expiry": str(nfo["expiry"]),
                                          "delta": 0.75 if OPTION_STRIKE == "ITM1" else 0.5,
                                          "tv_pct": 0.02}
                        else:
                            print(f"    No NFO contract for {sym}, skipping late entry")
                            continue
                        retry_option_premium = _opt_prem(ep, opt_sizing["strike"], direction, opt_sizing["tv_pct"])
                        new_pos["option_strike"] = opt_sizing["strike"]
                        new_pos["option_delta"] = opt_sizing["delta"]
                        new_pos["option_expiry"] = opt_sizing["expiry"]
                        new_pos["option_label"] = f"{OPTION_STRIKE} ({pd.Timestamp(nfo['expiry']).strftime('%b')})"
                        new_pos["option_type"] = "C" if direction == "LONG" else "P"
                        new_pos["option_symbol"] = retry_option_symbol
                        new_pos["option_entry_premium"] = round(retry_option_premium, 2)
                        new_pos["tv_pct"] = opt_sizing["tv_pct"]
                    open_positions.append(new_pos)
                    positions.append(new_pos)
                    today_entries.setdefault(sid, set()).add(sym)
                    changed = True
                    opt_info = f"\nOption: {retry_option_symbol}\nPremium: Rs.{retry_option_premium:.2f}" if POSITION_SIZING == "options" else ""
                    print(f"    ENTERED {sym} {direction} @ Rs.{ep:,.2f} qty={qty}{opt_info}")
                    if is_configured():
                        sname = _strategy_names.get(sid, sid)
                        dir_icon = "🟢" if direction == "LONG" else "🔴"
                        strat_icons = {"donchian_adx": "📈", "keltner_rsi": "📊", "supertrend_volume": "📉"}
                        strat_icon = strat_icons.get(sid, "")
                        send_message(f"{dir_icon}{strat_icon} *{sname} Late Entry — {sym}*\n{direction}\nEntry: Rs.{ep:,.2f}\nSL: Rs.{sl_price:,.2f}\nQty: {qty}{opt_info}", parse_mode="Markdown")
                _unfilled_signals[:] = still_unfilled

        for i, pos in enumerate(open_positions):
            if pos.get("status") == "CLOSED":
                continue
            print(f"  [{_now_str()}] {pos['symbol']}...", end=" ", flush=True)
            hit, ex_p, ex_t, reason = _check_sl(pos, date_str)
            if hit:
                pos = _send_exit_alert(pos, ex_p, ex_t, reason)
                open_positions[i] = pos
                changed = True
                print(f"{reason} @ {ex_t} P&L={pos['pnl']:+,.0f}")
            else:
                print("active")
        if changed:
            # Dedup positions: use (symbol, strategy) as key to preserve cross-strategy positions
            seen = set()
            deduped_positions = []
            for p in reversed(positions):
                key = (p.get("symbol", ""), p.get("strategy", ""))
                if key in seen:
                    continue
                seen.add(key)
                deduped_positions.append(p)
            positions = list(reversed(deduped_positions))
            save_json(POSITIONS_FILE, positions)
        remaining = (close_time - _now()).total_seconds()
        time.sleep(max(30, remaining) if remaining <= CHECK_INTERVAL else CHECK_INTERVAL)

    # End of session — save carry-forward positions
    save_json(POSITIONS_FILE, positions)
    open_count = sum(1 for p in positions if p.get("status") == "OPEN")
    closed = [p for p in positions if p.get("status") == "CLOSED"]
    tp = sum(p.get("pnl", 0) for p in closed)
    wins = sum(1 for p in closed if p.get("pnl", 0) > 0)
    losses = sum(1 for p in closed if p.get("pnl", 0) < 0)
    print(f"  End of session: {len(closed)} closed, {open_count} carried forward, {wins}W/{losses}L, P&L: Rs.{tp:+,.0f}")
    if is_configured() and (closed or open_count):
        strat_icons = {"donchian_adx": "📈", "keltner_rsi": "📊", "supertrend_volume": "📉"}
        by_strategy = {}
        for p in closed:
            sid = p.get("strategy", "unknown")
            if sid not in by_strategy:
                by_strategy[sid] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
            s = by_strategy[sid]
            s["trades"] += 1
            pnl_val = p.get("pnl", 0)
            s["pnl"] += pnl_val
            if pnl_val > 0:
                s["wins"] += 1
            else:
                s["losses"] += 1

        msg = f"📊 *SwingPortfolio EOD Summary — {date_str}*\n\n"
        for sid, stats in sorted(by_strategy.items()):
            icon = strat_icons.get(sid, "📊")
            st_name = _strategy_names.get(sid, sid)
            wr = (stats["wins"] / max(stats["trades"], 1) * 100)
            pnl_sign = "+" if stats["pnl"] >= 0 else ""
            msg += f"{icon} {st_name}: {stats['trades']} trades | {stats['wins']}W/{stats['losses']}L ({wr:.0f}%) | P&L: {pnl_sign}Rs.{abs(stats['pnl']):,.0f}\n"

        msg += f"\n━━━━━━━━━━━━━━━━\n"
        msg += f"*Total*: {len(closed)} closed | {wins}W/{losses}L ({wins/max(len(closed),1)*100:.0f}%) | P&L: Rs.{tp:+,.0f}"
        if open_count:
            msg += f"\n{open_count} open positions carried forward"

        send_message(msg, parse_mode="Markdown")


def run():
    print(f"[{_now_str()}] SwingPortfolio Live Trader started")
    _phase_entry()
    _phase_monitor()
    print(f"[{_now_str()}] SwingPortfolio Live Trader complete")


if __name__ == "__main__":
    run()
