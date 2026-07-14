import pandas as pd
import numpy as np
import os, sys, time as _time
from datetime import datetime, timedelta
from datetime import time as dt_time
try:
    import zoneinfo
    _TZ = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    _TZ = None
from cfg.settings import (CAPITAL_TOTAL, MAX_POSITIONS_PER_STRATEGY,
    MAX_POSITIONS_TOTAL,     TRAIL_PCT, RR_RATIO, HARD_SL_ENABLED, HARD_SL_AMOUNT,
    SL_ATR_MULTIPLIER,
    GAP_FILTER_ENABLED, ENTRY_TIME, STRATEGY_WEIGHTS, CAPITAL_MODE,
    POSITION_SIZING, OPTION_STRIKE, MAX_MARGIN_PER_STOCK, MARGIN_RATE, FALLBACK_CUTOFF)
from core.data_loader import download_month
from cfg.universes import get_lot_size, get_tick_size

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(BASE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from common.market_data.cache import get_cache

_ENTRY_MAP = {"15m_945": 0, "walk": 1, "zscore": 2, "15_15": 3}


# ── Options helpers ──────────────────────────────────────────

def _option_sizing(entry_price, direction, entry_date, lot_size, tick_size=0.05,
                   user_strike="ATM", symbol=None, cache=None):
    """Resolve option sizing from NFO table (when symbol+cache provided) or formula.
    All new code should pass symbol and cache to use the NFO table directly."""
    from common.market_data.cache import get_cache as _get_cache
    _cache = cache or _get_cache()

    # NFO table lookup (preferred — no formula, no weekday math)
    if symbol is not None:
        try:
            nfo = _cache.resolve_option_contract(
                symbol, entry_price, direction, strike_mode=user_strike
            )
            if nfo:
                from datetime import datetime as _dt
                exp_dt = nfo["expiry"]
                if isinstance(exp_dt, str):
                    exp_dt = pd.Timestamp(exp_dt)
                dte = (exp_dt - pd.Timestamp(entry_date)).days if hasattr(exp_dt, 'date') else 30
                tv_pct = 0.03 if dte < 10 else 0.02
                return {
                    "strike": nfo["strike_price"],
                    "delta": 0.75 if user_strike == "ITM1" else 0.5,
                    "tv_pct": tv_pct,
                    "expiry": str(nfo["expiry"]),
                    "label": f"{user_strike} ({pd.Timestamp(nfo['expiry']).strftime('%b')})",
                    "lot_size": nfo["lot_size"],
                    "trading_symbol": nfo["trading_symbol"],
                }
        except Exception:
            pass

    # Formula fallback (only if NFO table unavailable or lookup fails)
    def _last_tuesday(year, month):
        last = pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)
        while last.weekday() != 1:
            last -= timedelta(days=1)
        return last

    dt = pd.Timestamp(entry_date)
    cur_exp = _last_tuesday(dt.year, dt.month)
    dte = (cur_exp - dt).days
    if dte < 10:
        next_m = dt.month + 1 if dt.month < 12 else 1
        next_y = dt.year if dt.month < 12 else dt.year + 1
        exp = _last_tuesday(next_y, next_m)
        strike = round(entry_price / tick_size) * tick_size
        delta = 0.5
        tv_pct = 0.03
        label = f"ATM ({exp.strftime('%b')})"
    else:
        exp = cur_exp
        if user_strike == "ITM1":
            strike = round(entry_price / tick_size) * tick_size
            if direction == "LONG":
                strike -= tick_size
            else:
                strike += tick_size
            delta = 0.75
            tv_pct = 0.02
            label = f"ITM1 ({exp.strftime('%b')})"
        else:
            strike = round(entry_price / tick_size) * tick_size
            delta = 0.5
            tv_pct = 0.02
            label = f"ATM ({exp.strftime('%b')})"
    return {"strike": round(strike, 2), "delta": delta, "tv_pct": tv_pct,
            "expiry": exp.strftime("%Y-%m-%d"), "label": label}


def _option_premium(price, strike, direction, tv_pct):
    """Estimate option premium = intrinsic + time value."""
    if direction == "LONG":
        intrinsic = max(0, price - strike)
    else:
        intrinsic = max(0, strike - price)
    time_val = price * tv_pct
    return intrinsic + time_val


def _option_pnl(entry_price, exit_price, direction, sizing):
    """PnL = (exit - entry) * delta * lot_size for call.
    For put: (entry - exit) * delta * lot_size.
    sizing is the position dict with option_delta, qty keys."""
    delta = sizing.get("option_delta", 0.5)
    lot = sizing.get("qty", 1)
    if direction == "LONG":
        return (exit_price - entry_price) * delta * lot
    else:
        return (entry_price - exit_price) * delta * lot


def _fetch_intraday_cached(symbol, interval, date_str, end_str=None):
    """Fetch intraday data from DuckDB cache. For backtests, reads cache only — no sync (data should already exist)."""
    try:
        cache = get_cache()
        return cache.get_intraday_for_backtest(symbol, date_str, interval)
    except Exception as e:
        print(f"  [WARN] _fetch_intraday_cached({symbol}, {interval}) failed: {e}")
        return None


def _zscore_entry_from_df(df, direction, threshold, fallback_cutoff=None, baseline_start="09:15"):
    """Compute z-score on first 15 candles from baseline_start, return entry (price, time) when threshold crossed.
    threshold: 2.0 for 1m, 1.0 for 5m. baseline_start: "09:15" for 1m, "09:30" for 5m.
    Returns (None, None) if not triggered."""
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
        if sigma <= 0:
            return None, None
        for i in range(15, len(df)):
            z = (df.iloc[i]["Close"] - mu) / sigma
            if (direction == "LONG" and z > threshold) or (direction == "SHORT" and z < -threshold):
                return float(df.iloc[i]["Close"]), str(df.index[i].time())[:5]
        # Time-based fallback at cutoff
        if fallback_cutoff:
            ctime = pd.Timestamp(fallback_cutoff).time()
            idx_time = df.index[-1].time() if hasattr(df.index[-1], "time") else None
            if idx_time and idx_time >= ctime:
                return float(df.iloc[-1]["Close"]), str(df.index[-1].time())[:5] if hasattr(df.index[-1], "time") else fallback_cutoff
        return None, None
    except Exception:
        return None, None


def _get_entry_price(sym, entry_date, direction, entry_mode):
    entry_ts = pd.Timestamp(entry_date)
    date_str = entry_ts.strftime("%Y-%m-%d")
    end_str = (entry_ts + timedelta(1)).strftime("%Y-%m-%d")
    lookup_sym = sym if sym.endswith(".NS") else f"{sym}.NS"
    age_days = (datetime.now() - entry_ts.to_pydatetime()).days

    if entry_mode == "15m_945":
        if age_days <= 30:
            df = _fetch_intraday_cached(lookup_sym, "15m", date_str, end_str)
            if df is not None:
                try:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    df.index = pd.to_datetime(df.index)
                    if df.index.tz is not None:
                        df.index = df.index.tz_convert("Asia/Kolkata")
                    target = entry_ts.replace(hour=9, minute=45).tz_localize("Asia/Kolkata")
                    if target in df.index:
                        val = df.loc[target, "Open"]
                        return float(val.iloc[0]) if isinstance(val, pd.Series) else float(val), "09:45"
                except Exception:
                    pass

    elif entry_mode in ("zscore", "zscore_5m"):
        # Try 1m first with z > 2.0 (only if date is within Yahoo's 30-day window)
        if age_days <= 30:
            df1 = _fetch_intraday_cached(lookup_sym, "1m", date_str, end_str)
            ep, et = _zscore_entry_from_df(df1, direction, 2.0, FALLBACK_CUTOFF)
            if ep is not None:
                return ep, et

        # Fallback to 5m with z > 1.0 (Yahoo 5m retention ~60 days)
        if age_days <= 60:
            df5 = _fetch_intraday_cached(lookup_sym, "5m", date_str, end_str)
            ep, et = _zscore_entry_from_df(df5, direction, 1.0, FALLBACK_CUTOFF)
            if ep is not None:
                return ep, et

    elif entry_mode == "zscore_5m_pure":
        # Pure 5m z-score with z>1.0, baseline from 09:30, no 1m involved
        if age_days <= 60:
            df5 = _fetch_intraday_cached(lookup_sym, "5m", date_str, end_str)
            ep, et = _zscore_entry_from_df(df5, direction, 1.0, FALLBACK_CUTOFF, baseline_start="09:30")
            if ep is not None:
                return ep, et

    elif entry_mode == "walk":
        if age_days <= 30:
            df = _fetch_intraday_cached(lookup_sym, "1m", date_str, end_str)
            if df is not None and len(df) > 20:
                try:
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    df.index = pd.to_datetime(df.index)
                    if df.index.tz is not None:
                        df.index = df.index.tz_convert("Asia/Kolkata")
                    df = df.between_time("09:15", "15:30")
                    if len(df) >= 20:
                        base = df.iloc[:15]["Close"]
                        for i in range(15, len(df)):
                            c = df.iloc[i]["Close"]
                            if c > base.max() or c < base.min():
                                return float(c), str(df.index[i].time())[:5]
                except Exception:
                    pass
    return None, None


def run_backtest(strategy, months, universe=None, entry_mode="zscore", capital=CAPITAL_TOTAL, max_pos=MAX_POSITIONS_PER_STRATEGY,
                 position_sizing=None, option_strike=None):
    """Run backtest for a strategy. entry_mode can be overridden; falls back to strategy's _entry_time."""
    sizing_mode = position_sizing if position_sizing is not None else POSITION_SIZING
    strike_mode = option_strike if option_strike is not None else OPTION_STRIKE
    # Use per-strategy entry_time if not explicitly overridden
    if entry_mode == "zscore":
        strat_entry = getattr(strategy, '_entry_time', None)
        if strat_entry:
            entry_mode = strat_entry
    # Per-strategy trail_pct (use local copy, never mutate global)
    strat_trail = getattr(strategy, 'trail_pct', None)
    trail_pct_local = strat_trail if strat_trail is not None else TRAIL_PCT
    all_trades = []
    day_log = []
    pos = []
    for label, y, m in months:
        data = download_month(y, m)
        ms_dt, me_dt = pd.Timestamp(y, m, 1), pd.Timestamp(y+1, 1, 1) if m == 12 else pd.Timestamp(y, m+1, 1)
        ind = {}
        for sym, df in data.items():
            if universe is not None and sym.replace(".NS", "") not in universe:
                continue
            if len(df) < 25:
                continue
            d = strategy.prepare_data(df)
            if len(d) < 25:
                continue
            ind[sym] = d
        all_ts = sorted(set(ts for d in ind.values() for ts in d.index if ms_dt <= ts < me_dt))
        # Exclude today's incomplete candle only if market is still open
        now = datetime.now(_TZ) if _TZ else datetime.now()
        if now.weekday() < 5 and now.time() < dt_time(15, 30):
            all_ts = [ts for ts in all_ts if ts.date() < now.date()]
        else:
            all_ts = [ts for ts in all_ts]  # market closed: include all dates
        closed, day_log = [], []

        # Build next-trading-day lookup (fixes same-day entry bias)
        ts_to_next = {}
        for i in range(len(all_ts) - 1):
            ts_to_next[all_ts[i]] = all_ts[i + 1]

        for ts in all_ts:
            ds = []
            raw_signals = []
            for sym, df in ind.items():
                if any(p["sym"] == sym for p in pos):
                    continue
                if ts not in df.index:
                    continue
                ix = list(df.index).index(ts)
                if ix < 5:
                    continue
                row = df.iloc[ix]
                prev = df.iloc[ix-1]
                sig = strategy.check_signal(row, prev)
                if sig:
                    raw_signals.append({"sym": sym.replace(".NS", ""), "dir": sig["direction"]})
                    atr_v = row.get("atr", None)
                    if atr_v is None or pd.isna(atr_v) or atr_v == 0:
                        atr_v = row["high"] - row["low"]
                        if atr_v == 0:
                            atr_v = row["close"] * 0.02
                    ref = row["close"]
                    # Per-strategy SL/RR (with fallback to cfg settings)
                    sl_mult = getattr(strategy, 'sl_atr_multiplier', SL_ATR_MULTIPLIER)
                    rr_ratio = getattr(strategy, 'rr_ratio', RR_RATIO)
                    sl = ref - atr_v * sl_mult if sig["direction"] == "LONG" else ref + atr_v * sl_mult
                    target = ref + atr_v * sl_mult * rr_ratio if sig["direction"] == "LONG" else ref - atr_v * sl_mult * rr_ratio
                    # Entry: 15_15 mode enters same day at close; others enter next day via zscore
                    if entry_mode == "15_15":
                        entry_ts = ts
                        ep = row["close"]
                        entry_time_str = "15:15"
                    else:
                        entry_ts = ts_to_next.get(ts, ts)
                        ep, entry_time_str = _get_entry_price(sym, entry_ts, sig["direction"], entry_mode)
                        if ep is None:
                            if entry_ts in df.index:
                                ep = df.loc[entry_ts]["open"]
                                entry_time_str = f"{entry_ts.strftime('%d-%b')} open"
                            else:
                                ep = row["open"]
                                entry_time_str = "open"
                    if abs(ep - ref) / ref > 0.20:
                        continue
                    if GAP_FILTER_ENABLED and ix > 0:
                        ref_close = df.iloc[ix-1]["close"]
                        if ref_close and abs(row["open"] - ref_close) / ref_close > 0.03:
                            continue
                    qty = max(1, int(capital / ep / max(1, max_pos)))
                    cap_deployed = round(ep * qty, 2)
                    opt_extra = {}
                    if sizing_mode == "lot":
                        sym_clean = sym.replace(".NS", "")
                        lot_size = get_lot_size(sym_clean)
                        if lot_size is None:
                            continue
                        margin = lot_size * ep * MARGIN_RATE
                        if margin > MAX_MARGIN_PER_STOCK:
                            continue
                        qty = lot_size
                        cap_deployed = round(ep * qty * MARGIN_RATE, 2)
                    elif sizing_mode == "options":
                        sym_clean = sym.replace(".NS", "")
                        lot_size = get_lot_size(sym_clean)
                        if lot_size is None:
                            continue
                        tick_size = get_tick_size(sym_clean) or 0.05
                        opt_sizing = _option_sizing(ep, sig["direction"], ts, lot_size, tick_size, user_strike=strike_mode, symbol=sym)
                        opt_sizing["lot_size"] = lot_size
                        qty = lot_size
                        entry_prem = _option_premium(ep, opt_sizing["strike"], sig["direction"], opt_sizing["tv_pct"])
                        cap_deployed = round(entry_prem * qty, 2)
                        opt_extra = {
                            "option_strike": opt_sizing["strike"],
                            "option_delta": opt_sizing["delta"],
                            "option_expiry": opt_sizing["expiry"],
                            "option_label": opt_sizing["label"],
                            "entry_premium": round(entry_prem, 2),
                        }
                    tr = sl
                    if HARD_SL_ENABLED and HARD_SL_AMOUNT > 0:
                        ml = HARD_SL_AMOUNT / qty
                        tr = max(sl, ep - ml) if sig["direction"] == "LONG" else min(sl, ep + ml)
                    adx_v = row.get("adx", 50)
                    ds.append((adx_v, {"sym": sym, "dir": "L" if sig["direction"] == "LONG" else "S",
                        "ep": ep, "sl": sl, "init_sl": sl, "tr": tr, "target": target, "qty": qty,
                        "dt": entry_ts, "bp": ep, "adx": adx_v, "entry_time_str": entry_time_str,
                        "position_sizing": sizing_mode, "capital_deployed": cap_deployed,
                        "option_strike": opt_extra.get("option_strike"),
                        "option_delta": opt_extra.get("option_delta"),
                        "option_expiry": opt_extra.get("option_expiry"),
                        "option_label": opt_extra.get("option_label"),
                        "entry_premium": opt_extra.get("entry_premium")}))

            selected = []
            if len(pos) < max_pos and ds:
                ds.sort(key=lambda x: -x[0])
                selected = [p["sym"].replace(".NS", "") for _, p in ds[:max_pos - len(pos)]]
                for _, p in ds[:max_pos - len(pos)]:
                    pos.append(p)

            day_log.append({"date": ts.strftime("%d-%b %a"), "signals": raw_signals, "selected": selected})

            for p in list(pos):
                if ts not in ind.get(p["sym"], pd.DataFrame()).index:
                    continue
                r = ind[p["sym"]].loc[ts]
                h, l, c = r["high"], r["low"], r["close"]
                il = p["dir"] == "L"
                if trail_pct_local > 0 and ts > p["dt"]:
                    if il and h > p["bp"]:
                        p["bp"] = h
                        p["tr"] = max(p["tr"], h * (1 - trail_pct_local))
                    elif not il and l < p["bp"]:
                        p["bp"] = l
                        p["tr"] = min(p["tr"], l * (1 + trail_pct_local))
                ex_p, reason = None, None
                if il:
                    if h >= p["target"]:
                        ex_p, reason = p["target"], "TARGET"
                    elif l <= p["tr"]:
                        ex_p, reason = p["tr"], "SL"
                else:
                    if l <= p["target"]:
                        ex_p, reason = p["target"], "TARGET"
                    elif h >= p["tr"]:
                        ex_p, reason = p["tr"], "SL"
                # max_hold check (per-strategy)
                max_hold = getattr(strategy, 'max_hold_days', None)
                held_days = (ts - p["dt"]).days
                if ex_p is None and max_hold is not None and held_days >= max_hold:
                    ex_p, reason = c, "MAX_HOLD"
                if ex_p is not None:
                    sz_mode = p.get("position_sizing", sizing_mode)
                    if sz_mode == "options" and "option_delta" in p and p["option_delta"] is not None:
                        pnl = _option_pnl(p["ep"], ex_p, "LONG" if il else "SHORT", p)
                        exit_prem = _option_premium(ex_p, p.get("option_strike", p["ep"]),
                                                    "LONG" if il else "SHORT",
                                                    p.get("tv_pct", 0.02))
                    else:
                        pnl = (ex_p - p["ep"]) * p["qty"] if il else (p["ep"] - ex_p) * p["qty"]
                        exit_prem = None
                    cap_dep = p.get("capital_deployed", p["ep"] * p["qty"])
                    roi_pct = round((pnl / cap_dep) * 100, 2) if cap_dep > 0 else 0.0
                    pct = ((ex_p - l) / (h - l) if not il else (h - ex_p) / (h - l)) if h > l else 0.5
                    mins = int(9 * 60 + 15 + pct * 375)
                    init_sl = p.get("init_sl", p["sl"])
                    closed.append({"sym": p["sym"].replace(".NS", ""), "dir": "LONG" if il else "SHORT",
                        "ep": p["ep"], "ex": ex_p, "qty": p["qty"], "pnl": round(pnl, 2),
                        "capital_deployed": cap_dep, "roi_pct": roi_pct,
                        "entry_dt": p["dt"], "exit_dt": ts,
                        "exit_time": f"{mins//60:02d}:{mins%60:02d}", "exit_reason": reason,
                        "month": pd.Timestamp(p["dt"]).strftime("%b"),
                        "init_sl": round(init_sl, 2), "risk_rs": round(abs(p["ep"] - init_sl), 2),
                        "risk_total": int(abs(p["ep"] - init_sl) * p["qty"]),
                        "adx_entry": round(p.get("adx", 0), 1),
                        "entry_time_str": p.get("entry_time_str", "09:15"),
                        "strategy": strategy.strategy_id,
                        "option_strike": p.get("option_strike"),
                        "option_delta": p.get("option_delta"),
                        "option_expiry": p.get("option_expiry"),
                        "option_label": p.get("option_label"),
                        "entry_premium": p.get("entry_premium")})
                    pos.remove(p)

        all_trades.extend(closed)
    # Close remaining positions at end of backtest (no month-boundary CLOSE)
    for p in list(pos):
        il = p["dir"] == "L"
        init_sl = p.get("init_sl", p["sl"])
        cap_dep = p.get("capital_deployed", p["ep"] * p["qty"])
        all_trades.append({"sym": p["sym"].replace(".NS", ""), "dir": "LONG" if il else "SHORT",
            "ep": p["ep"], "ex": p["ep"], "qty": p["qty"], "pnl": 0,
            "capital_deployed": cap_dep, "roi_pct": 0.0,
            "entry_dt": p["dt"], "exit_dt": p["dt"],
            "exit_time": "15:30", "exit_reason": "END",
            "month": pd.Timestamp(p["dt"]).strftime("%b"),
            "init_sl": round(init_sl, 2), "risk_rs": round(abs(p["ep"] - init_sl), 2),
            "risk_total": int(abs(p["ep"] - init_sl) * p["qty"]),
            "adx_entry": round(p.get("adx", 0), 1),
            "entry_time_str": p.get("entry_time_str", "09:15"),
            "strategy": strategy.strategy_id})
    return all_trades, day_log


def simulate_today():
    """
    Replicate live trading for today using selections.json + 1-min intraday data.
    Scans yesterday's close for signals (via selections.json),
    then simulates zscore entry + SL/target/trail on 1-min bars.
    Returns list of trade dicts. Open positions have exit_reason "OPEN"
    and include unrealised P&L based on last 1-min close.
    """
    import os, json, time
    from cfg.universes import get_lot_size
    from cfg.settings import (TRAIL_PCT, RR_RATIO, HARD_SL_ENABLED, HARD_SL_AMOUNT,
                               SL_ATR_MULTIPLIER, MAX_POSITIONS_PER_STRATEGY,
                               MAX_POSITIONS_TOTAL, MAX_MARGIN_PER_STOCK, MARGIN_RATE)

    DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    
    # Load signals from per-strategy files (new architecture) + legacy combined file
    all_sigs = {}
    strategy_ids = ["donchian_adx", "keltner_rsi", "supertrend_volume"]
    for sid in strategy_ids:
        sf = os.path.join(DATA_DIR, f"selections_{sid}.json")
        if os.path.exists(sf):
            try:
                with open(sf) as f:
                    sd = json.load(f)
                sigs = sd.get("data", [])
                if sigs:
                    all_sigs[sid] = sigs
            except Exception:
                pass

    # Fallback: legacy selections.json
    if not all_sigs:
        sel_file = os.path.join(DATA_DIR, "selections.json")
        if os.path.exists(sel_file):
            with open(sel_file) as f:
                sel_data = json.load(f)
            results = sel_data.get("results", {})
            for sid, sigs in results.items():
                if sigs:
                    all_sigs[sid] = sigs

    if not all_sigs:
        return []

    results = all_sigs
    date_str = datetime.now().strftime("%Y-%m-%d")
    end_str = (datetime.now() + timedelta(1)).strftime("%Y-%m-%d")
    all_trades = []

    def _fetch_1m(sym):
        cache = get_cache()
        df = cache.get_intraday_for_backtest(sym, date_str, "1m")
        if df is not None and not df.empty:
            return df.between_time("09:15", "15:30")
        return None

    def _fetch_5m(sym):
        cache = get_cache()
        df = cache.get_intraday_for_backtest(sym, date_str, "5m")
        if df is not None and not df.empty:
            return df.between_time("09:15", "15:30")
        return None

    for sid, sigs in results.items():
        if not sigs:
            continue
        max_pos = min(MAX_POSITIONS_PER_STRATEGY, MAX_POSITIONS_TOTAL)
        filled = 0

        for sel in sigs:
            if filled >= max_pos:
                break

            sym = sel["symbol"]
            direction = sel["direction"]
            lot = get_lot_size(sym)
            if lot is None:
                continue

            # Try zscore entry — 1m first, then 5m fallback
            ep, ets = None, None
            entry_df = None
            for attempt in range(2):
                df = _fetch_1m(sym)
                if df is not None and len(df) >= 20:
                    entry_df = df
                    thresh = 2.0
                    ep, ets = _zscore_entry_from_df(df, direction, thresh, FALLBACK_CUTOFF)
                    if ep is not None:
                        break
                # Fallback: try 5m with adjusted threshold
                df5 = _fetch_5m(sym)
                if df5 is not None and len(df5) >= 12:  # 5m needs fewer candles: 12 ~= 1hr
                    entry_df = df5
                    ep, ets = _zscore_entry_from_df(df5, direction, 1.0, FALLBACK_CUTOFF)
            if ep is not None:
                    break

            if ep is None or entry_df is None:
                continue

            # Gap filter
            prev_close = sel.get("close_price", ep)
            if abs(ep - prev_close) / prev_close > 0.03:
                continue

            # Margin filter
            margin = lot * ep * MARGIN_RATE
            if margin > MAX_MARGIN_PER_STOCK:
                continue

            qty = lot
            atr_est = ep * 0.02
            sl_price = ep - atr_est * SL_ATR_MULTIPLIER if direction == "LONG" else ep + atr_est * SL_ATR_MULTIPLIER
            target_price = ep + atr_est * SL_ATR_MULTIPLIER * RR_RATIO if direction == "LONG" else ep - atr_est * SL_ATR_MULTIPLIER * RR_RATIO
            if HARD_SL_ENABLED and HARD_SL_AMOUNT > 0:
                ml = HARD_SL_AMOUNT / qty
                sl_price = max(sl_price, ep - ml) if direction == "LONG" else min(sl_price, ep + ml)

            # Simulate exit on all 1-min bars after entry
            trail = sl_price
            best = ep
            eh, em = map(int, ets.split(":"))
            entry_idx = next((i for i in range(len(entry_df))
                            if (t := entry_df.iloc[i].name).hour > eh or (t.hour == eh and t.minute >= em)), 0)

            ex_p, ex_t, reason = None, None, None
            for i in range(entry_idx, len(entry_df)):
                row = entry_df.iloc[i]
                h, l = float(row["High"]), float(row["Low"])
                t = row.name

                if i > entry_idx:
                    if direction == "LONG":
                        if h > best:
                            best = h
                            trail = max(trail, h * (1 - TRAIL_PCT))
                    else:
                        if l < best:
                            best = l
                            trail = min(trail, l * (1 + TRAIL_PCT))

                if direction == "LONG":
                    if target_price and h >= target_price:
                        ex_p, ex_t, reason = target_price, str(t.time())[:5], "TARGET"
                        break
                    if l <= trail:
                        ex_p, ex_t, reason = trail, str(t.time())[:5], "SL"
                        break
                else:
                    if target_price and l <= target_price:
                        ex_p, ex_t, reason = target_price, str(t.time())[:5], "TARGET"
                        break
                    if h >= trail:
                        ex_p, ex_t, reason = trail, str(t.time())[:5], "SL"
                        break

            current_price = float(entry_df.iloc[-1]["Close"])

            if ex_p is not None:
                # Hit SL/target during the day
                pnl = (ex_p - ep) * qty if direction == "LONG" else (ep - ex_p) * qty
                all_trades.append({
                    "sym": sym, "dir": "LONG" if direction == "LONG" else "SHORT",
                    "ep": round(ep, 2), "ex": round(ex_p, 2), "qty": qty,
                    "pnl": round(pnl, 2), "entry_dt": pd.Timestamp(date_str),
                    "exit_dt": pd.Timestamp(date_str),
                    "entry_time_str": ets, "exit_time": ex_t,
                    "exit_reason": reason, "status": "CLOSED",
                        "strategy": sid, "month": datetime.now().strftime("%b"),
                    "best_price": round(best, 2), "trail_sl": round(trail, 2),
                    "current_price": round(current_price, 2),
                })
            else:
                # Still open at EOD — carry forward
                pnl = (current_price - ep) * qty if direction == "LONG" else (ep - current_price) * qty
                all_trades.append({
                    "sym": sym, "dir": "LONG" if direction == "LONG" else "SHORT",
                    "ep": round(ep, 2), "qty": qty,
                    "pnl": round(pnl, 2), "entry_dt": pd.Timestamp(date_str),
                    "entry_time_str": ets, "exit_reason": "OPEN",
                    "status": "OPEN", "strategy": sid, "month": datetime.now().strftime("%b"),
                    "best_price": round(best, 2), "trail_sl": round(trail, 2),
                    "current_price": round(current_price, 2),
                })
            filled += 1

    return all_trades


def _months_from_range(fr, to):
    ms = []
    y, m = fr.year, fr.month
    while (y, m) <= (to.year, to.month):
        ms.append((datetime(y, m, 1).strftime("%b"), y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return ms
