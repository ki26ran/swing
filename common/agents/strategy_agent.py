"""
Strategy Agent — per-strategy isolation. One agent = one strategy.
Usage:
  python strategy_agent.py --project SwingPortfolio --strategy donchian_adx --mode scan
  python strategy_agent.py --project SwingPortfolio --strategy donchian_adx --mode live
  python strategy_agent.py --project IntraPortfolio --strategy gap_fade --mode scan
  python strategy_agent.py --project IntraPortfolio --strategy gap_fade --mode live

Each agent reads/writes its own files:
  data/selections_{strategy_id}.json
  data/live_positions_{strategy_id}.json
  data/trade_log_{strategy_id}.csv
"""
import os, sys, json, time, csv, socket
from datetime import datetime, time as dt_time
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _send_telegram(project, msg):
    try:
        import requests
        host = socket.gethostname().replace("LAPTOP-", "WIN-")[:12]
        full = f"{msg} [{host}]"
        cfg_path = os.path.join(ROOT, project, "data", "telegram_config.json")
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                tc = json.load(f)
            token = tc.get("bot_token", "")
            chat = tc.get("chat_id", "")
            if token and chat:
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                requests.post(url, json={"chat_id": chat, "text": full, "parse_mode": "Markdown"}, timeout=10)
    except Exception:
        pass


def _load_json(path, default=None):
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return default


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


def _append_csv(path, fieldnames, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_header = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames)
        if write_header:
            w.writeheader()
        w.writerow(row)


# ════════════════════════════════════════════════════════════════
# SCAN MODE
# ════════════════════════════════════════════════════════════════

def run_scan(project, strategy_id):
    sys.path.insert(0, os.path.join(ROOT, project))
    from core.registry import get_strategy, get_strategy_universe
    from core.notifications import send_message, is_configured
    from cfg.universes import UNIVERSE_MAP

    hb_name = f"{project.split('Portfolio')[0].lower()}_{strategy_id.split('_')[0]}" if project == "SwingPortfolio" else f"intra_{strategy_id}"
    try:
        import heartbeat as _hb
        _hb.write(hb_name, "running")
    except Exception:
        pass

    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    strat = get_strategy(strategy_id)
    sname = strat.strategy_name if hasattr(strat, 'strategy_name') else strategy_id
    print(f"[{now.strftime('%H:%M:%S')}] {project}/{strategy_id} scan started — {sname}")

    # Load data (daily for swing, intraday for intra)
    if project == "SwingPortfolio":
        from core.data_loader import download_month
        data = download_month(now.year, now.month)
        # Find last complete trading day
        max_date = None
        for d in data.values():
            valid = d['Close'].dropna() if 'Close' in d.columns else d['close'].dropna()
            if len(valid) > 0:
                dt = valid.index[-1].date()
                if max_date is None or dt > max_date:
                    max_date = dt
        if max_date and max_date == now.date() and now.time() < dt_time(15, 30):
            prev = sorted(set(ts.date() for d in data.values() for ts in d.index if ts < pd.Timestamp(now) and ts.date() < now.date()))
            max_date = prev[-1] if prev else max_date
        scan_date = max_date if max_date else now.date()
        print(f"  Scan date: {scan_date} ({len(data)} stocks)")

        # Filter by strategy universe
        uni_name = getattr(strat, '_universe_name', 'Margin < 200K')
        universe_set = UNIVERSE_MAP.get(uni_name)
        if universe_set:
            data = {k: v for k, v in data.items() if k.replace('.NS', '') in universe_set}

        signals = strat.scan_universe(data, scan_date)

    elif project == "IntraPortfolio":
        from core.data_loader import download_intraday_batch, get_prev_closes, download_1m_batch
        config = getattr(strat, 'config', {})
        use_manual = config.get("params", {}).get("use_manual_stocks", False)
        manual_list = config.get("params", {}).get("manual_stocks", "")
        if use_manual and manual_list:
            universe_list = [s.strip().replace("-EQ", "") for s in manual_list.split(",") if s.strip()]
        else:
            universe = get_strategy_universe(strategy_id)
            universe_list = list(universe)
        ds = config.get("data_source", "yahoo")
        bn = config.get("broker_name", "SHOONYA")
        bu = config.get("broker_username", "FA138862")
        intra_interval = "1m" if strategy_id == "opening_range_breakout" else "5m"

        # For ORB, check cached candidates first to avoid full universe download
        cached_syms = None
        if strategy_id == "opening_range_breakout":
            try:
                import json as _json
                cf = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                  "IntraPortfolio", "data", "orb_candidates.json")
                if os.path.exists(cf):
                    with open(cf) as _f:
                        _c = _json.load(_f)
                    if _c.get("date") == date_str and _c.get("candidates"):
                        cached_syms = _c["candidates"]
                        print(f"  [FAST] Using {len(cached_syms)} cached candidates for {strategy_id}")
            except Exception:
                pass

        if cached_syms:
            data_dict = download_intraday_batch(cached_syms, date_str, interval=intra_interval,
                                                data_source=ds, broker_name=bn, broker_username=bu)
        else:
            data_dict = download_intraday_batch(universe_list, date_str, interval=intra_interval,
                                                data_source=ds, broker_name=bn, broker_username=bu)

        # Download 1-min data for ORB and compute/persist ranges
        data_1m = None
        orb_ranges = None
        if strategy_id == "opening_range_breakout":
            try:
                from live.cache import get_intra_cache
                orb_ranges = get_intra_cache().load_orb_ranges(date_str)
            except Exception:
                orb_ranges = None
            if not orb_ranges:
                try:
                    data_1m = download_1m_batch(universe_list, date_str, data_source=ds,
                                                 broker_name=bn, broker_username=bu)
                    if data_1m:
                        from IntraPortfolio.strategies.opening_range_breakout import compute_range_from_1m
                        r_start = config.get("params", {}).get("range_start_time", "09:15")
                        r_end = config.get("params", {}).get("range_end_time", "09:20")
                        orb_ranges = compute_range_from_1m(data_1m, date_str, r_start, r_end)
                        print(f"    Computed {len(orb_ranges)} ranges from 1-min data")
                        if orb_ranges:
                            try:
                                get_intra_cache().save_orb_ranges(date_str, orb_ranges, r_start, r_end)
                            except Exception:
                                pass
                    if not orb_ranges and data_dict:
                        print("    Falling back to 5-min data for range calculation")
                        orb_ranges = compute_range_from_1m(data_dict, date_str, r_start, r_end)
                        print(f"    Computed {len(orb_ranges)} ranges from 5-min data")
                except Exception:
                    pass

        # Build per-symbol data dict for strategy
        clean_data = {}
        for sym in universe_list:
            ticker = sym + ".NS" if not sym.endswith(".NS") else sym
            sdf = data_dict.get(ticker)
            if sdf is not None and not sdf.empty:
                clean_data[sym] = sdf

        scan_date = date_str
        kwargs = {}
        try:
            import inspect
            if 'prev_closes' in inspect.signature(strat.scan_universe).parameters:
                kwargs['prev_closes'] = get_prev_closes(universe_list, date_str)
            if data_1m is not None and 'data_1m' in inspect.signature(strat.scan_universe).parameters:
                kwargs['data_1m'] = data_1m
            if orb_ranges is not None and 'orb_ranges' in inspect.signature(strat.scan_universe).parameters:
                kwargs['orb_ranges'] = orb_ranges
        except Exception:
            pass
        signals = strat.scan_universe(clean_data, date_str, **kwargs)
        data = clean_data

    else:
        raise ValueError(f"Unknown project: {project}")

    # Format results
    signal_list = []
    if hasattr(signals[0], 'to_dict') if signals else False:
        signal_list = [s.to_dict() for s in signals]
    else:
        signal_list = list(signals)

    output = {
        "date": now.strftime("%Y-%m-%d %H:%M"),
        "scan_date": str(scan_date),
        "strategy": strategy_id,
        "strategy_name": sname,
        "project": project,
        "signals": len(signal_list),
        "scanned": len(data) if isinstance(data, dict) else len(signal_list),
        "data": signal_list,
    }

    sel_file = os.path.join(ROOT, project, "data", f"selections_{strategy_id}.json")
    _save_json(sel_file, output)
    print(f"  {len(signal_list)} signals -> {os.path.basename(sel_file)}")

    # Telegram
    if is_configured():
        if signal_list:
            lines = [f"{'🟢' if s.get('direction','?')=='LONG' else '🔴'} {s.get('symbol','?')} {s.get('direction','?')}" for s in signal_list[:10]]
            send_message(f"📋 *{sname} — {date_str}*\n{len(signal_list)} signals:\n" + "\n".join(lines))
        else:
            send_message(f"📋 *{sname} — {date_str}*\nNo signals.")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] {project}/{strategy_id} scan complete")
    try:
        _hb.write(hb_name, "ok")
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════
# LIVE TRADE MODE
# ════════════════════════════════════════════════════════════════

def run_live(project, strategy_id):
    sys.path.insert(0, os.path.join(ROOT, project))

    from core.registry import get_strategy
    from cfg.universes import get_lot_size as _get_lot_size, get_tick_size as _get_tick_size
    from cfg.settings import (TRAIL_PCT, RR_RATIO, HARD_SL_ENABLED, HARD_SL_AMOUNT,
                               SL_ATR_MULTIPLIER, MAX_POSITIONS_PER_STRATEGY,
                               MAX_MARGIN_PER_STOCK, MARGIN_RATE, POSITION_SIZING,
                               OPTION_STRIKE, PRODUCT_TYPE)

    strat = get_strategy(strategy_id)
    sname = strat.strategy_name if hasattr(strat, 'strategy_name') else strategy_id

    # Per-strategy overrides
    trail_pct = getattr(strat, 'trail_pct', TRAIL_PCT)
    rr_ratio = getattr(strat, 'rr_ratio', RR_RATIO)
    sl_mult = getattr(strat, 'sl_atr_multiplier', SL_ATR_MULTIPLIER)
    max_pos = getattr(strat, 'max_positions', MAX_POSITIONS_PER_STRATEGY)
    max_hold = getattr(strat, 'max_hold_days', None)

    data_dir = os.path.join(ROOT, project, "data")
    sel_file = os.path.join(data_dir, f"selections_{strategy_id}.json")
    pos_file = os.path.join(data_dir, f"live_positions_{strategy_id}.json")
    trade_log = os.path.join(data_dir, f"trade_log_{strategy_id}.csv")

    from common.market_data.cache import get_cache
    cache = get_cache()

    # ── Broker setup ──────────────────────────────────────────
    broker_api = None
    strat_cfg_path = os.path.join(ROOT, project, "cfg", "strategies.json")
    if os.path.exists(strat_cfg_path):
        try:
            with open(strat_cfg_path) as f:
                all_cfgs = json.load(f).get("strategies", [])
            for sc in all_cfgs:
                if sc.get("id") == strategy_id and sc.get("live"):
                    bn = sc.get("broker_name", "SHOONYA")
                    bu = sc.get("broker_username", "FA138862")
                    try:
                        from ganah import setup_api
                        broker_api = setup_api(bn, bu)
                        print(f"  Broker: {bn}/{bu} connected")
                    except Exception as e:
                        print(f"  Broker login failed ({bn}/{bu}): {e}")
                    break
        except Exception as e:
            print(f"  Could not read strategies.json: {e}")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] {project}/{strategy_id} live trader started — {sname}")
    start_time = datetime.now()

    positions = _load_json(pos_file, default={})
    TRADE_FIELDS = ["symbol", "direction", "entry_price", "exit_price", "qty", "pnl",
                    "entry_time", "exit_time", "exit_reason", "strategy", "date"]

    # ── Reconcile with broker positions on startup ────────────
    if broker_api is not None:
        try:
            broker_positions = broker_api.get_positions()
            if broker_positions and isinstance(broker_positions, list):
                for bp in broker_positions:
                    tsym = bp.get("tsym", "")
                    if not tsym.endswith("-EQ"):
                        continue
                    sym = tsym.replace("-EQ", "")
                    netqty = int(float(bp.get("netqty", 0)))
                    if netqty == 0:
                        continue
                    direction = "LONG" if netqty > 0 else "SHORT"
                    entry_price = float(bp.get("upldprc", 0) or bp.get("avgprc", 0))
                    # Check if we already track this position
                    already = any(p.get("symbol") == sym and p.get("status") == "open" for p in positions.values())
                    if not already:
                        key = f"{sym}_{strategy_id}_broker_{datetime.now().strftime('%Y%m%d%H%M%S')}"
                        positions[key] = {
                            "symbol": sym,
                            "direction": direction,
                            "entry_price": entry_price,
                            "qty": abs(netqty),
                            "strategy": strategy_id,
                            "entry_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "entry_date": datetime.now().strftime("%Y-%m-%d"),
                            "status": "open",
                            "broker_reconciled": True,
                        }
                        print(f"  Recovered broker position: {direction} {sym} x{abs(netqty)} @ {entry_price:.2f}")
        except Exception as e:
            print(f"  Could not reconcile broker positions: {e}")

    # ── Helper: check broker order fill status ────────────────
    def _check_order_fill(order_id, max_polls=10, interval=1):
        if broker_api is None or not order_id:
            return None
        from ganah import order_status
        for _ in range(max_polls):
            try:
                filled, avg_price, exec_time, rejection = order_status(order_id)
                if filled == 1:
                    return {"filled": True, "avg_price": avg_price, "time": exec_time}
                if filled == 2:
                    return {"filled": False, "rejected": True, "reason": rejection}
                if filled == 0:
                    time.sleep(interval)
            except Exception:
                time.sleep(interval)
        return {"filled": None, "rejected": False, "reason": "timeout"}

    # ── Helper: place broker order ────────────────────────────
    def _place_order(action, sym, qty, direction, strat_id, ref_price, option_symbol=None):
        if broker_api is None:
            return None
        try:
            from ganah import place_live_order
            side = "LONG" if action == "BUY" else "SHORT"
            exchange = "NFO" if option_symbol else "NSE"
            trading_symbol = option_symbol if option_symbol else f"{sym}-EQ"
            ss = {"donchian_adx": "DADX", "keltner_rsi": "KRSI", "supertrend_volume": "STVOL"}.get(strat_id, strat_id[:4].upper())
            reason_tag = "ENT" if action == "BUY" else "EXT"
            remarks = f"{ss}_{reason_tag}_{sym}_{ref_price:.0f}"
            oid = place_live_order(trading_symbol, side, qty, remarks,
                                   exchange=exchange, product_type=PRODUCT_TYPE, price_type="MKT")
            if oid:
                print(f"    {action} order placed: {trading_symbol} x{qty} -> {oid}")
            return oid
        except Exception as e:
            print(f"    {action} order failed for {sym}: {e}")
            return None

    while True:
        now = datetime.now()

        # Market hours check
        t = now.hour * 60 + now.minute
        if now.weekday() >= 5 or t < 555 or t > 930:
            time.sleep(60)
            continue

        # Load today's selections
        selections = _load_json(sel_file)
        signals = (selections or {}).get("data", [])
        # "date" = scan execution timestamp (always today 08:30 if scan ran)
        # "scan_date" = last trading day data reference (may be Friday on Monday, or pre-holiday)
        scan_run_date = (selections or {}).get("date", "")[:10]

        # Skip if scan didn't run today (e.g. holiday — file stale)
        if scan_run_date != now.strftime("%Y-%m-%d"):
            time.sleep(30)
            continue

        # Check for new entries
        open_count = sum(1 for p in positions.values() if p.get("status") == "open")
        for sig in signals:
            if open_count >= max_pos:
                break
            sym = sig.get("symbol", "")
            direction = sig.get("direction", "LONG")

            # Skip if already in a position for this symbol
            already_in = any(p.get("symbol") == sym and p.get("status") == "open" for p in positions.values())
            if already_in:
                continue

            # Get lot size
            lot = _get_lot_size(sym)
            if lot is None:
                continue

            # Get entry price: prefer live market price over scan reference price
            try:
                live_price = cache.get_last_price(sym)
            except Exception:
                live_price = None
            ep = live_price if (live_price is not None and live_price > 0) else sig.get("entry_price", 0)
            if ep <= 0:
                continue

            # Resolve option contract from NFO table (generic, no formulas)
            opt_symbol = None
            if POSITION_SIZING == "options":
                try:
                    nfo = cache.resolve_option_contract(
                        sym, ep, direction, strike_mode=OPTION_STRIKE
                    )
                    if nfo:
                        opt_symbol = nfo["trading_symbol"]
                        lot = nfo["lot_size"]
                except Exception:
                        pass

            # Margin check
            margin = lot * ep * MARGIN_RATE
            if margin > MAX_MARGIN_PER_STOCK:
                continue

            # Create position
            sl_price = ep - ep * 0.02 * sl_mult if direction == "LONG" else ep + ep * 0.02 * sl_mult
            target = ep + ep * 0.02 * sl_mult * rr_ratio if direction == "LONG" else ep - ep * 0.02 * sl_mult * rr_ratio
            if HARD_SL_ENABLED and HARD_SL_AMOUNT > 0:
                max_loss_per_unit = HARD_SL_AMOUNT / lot
                if direction == "LONG":
                    sl_price = max(sl_price, ep - max_loss_per_unit)
                else:
                    sl_price = min(sl_price, ep + max_loss_per_unit)

            pos_key = f"{sym}_{strategy_id}_{now.strftime('%Y%m%d%H%M%S')}"
            positions[pos_key] = {
                "symbol": sym,
                "direction": direction,
                "entry_price": round(ep, 2),
                "qty": lot,
                "initial_sl": round(sl_price, 2),
                "trail_sl": round(sl_price, 2),
                "target": round(target, 2),
                "best_price": round(ep, 2),
                "strategy": strategy_id,
                "entry_time": now.strftime("%Y-%m-%d %H:%M:%S"),
                "entry_date": now.strftime("%Y-%m-%d"),
                "option_symbol": opt_symbol,
                "status": "open",
            }
            open_count += 1

            # Place broker entry order
            action = "BUY" if direction == "LONG" else "SELL"
            oid = _place_order(action, sym, lot, direction, strategy_id, ep, opt_symbol)
            positions[pos_key]["broker_order_id"] = oid

            # Verify fill status (only if live broker connected)
            order_ok = True
            if oid and broker_api is not None:
                fill_result = _check_order_fill(oid)
                if fill_result and fill_result.get("rejected"):
                    print(f"    ORDER REJECTED for {sym}: {fill_result.get('reason','')}")
                    del positions[pos_key]
                    open_count -= 1
                    _send_telegram(project, f"🔴 *{sname}*: ORDER REJECTED {direction} {sym} @ {ep:.2f} ({fill_result.get('reason','')})")
                    continue
                if fill_result and fill_result.get("filled"):
                    print(f"    Order filled: {sym} avg={fill_result['avg_price']:.2f}")
                # If timeout/pending — proceed anyway, will verify on next cycle

            tg_msg = f"🟢 *{sname}*: {direction} {sym} @ {ep:.2f}"
            if opt_symbol:
                tg_msg += f"\n  Opt: `{opt_symbol}`"
            print(f"  [{now.strftime('%H:%M:%S')}] OPEN {direction} {sym} @ {ep:.2f} SL {sl_price:.2f}{f'  {opt_symbol}' if opt_symbol else ''}")
            _send_telegram(project, tg_msg)

            # Update selections file with actual fill price/time + option symbol
            sel = _load_json(sel_file)
            if sel and "data" in sel:
                for s in sel["data"]:
                    if s.get("symbol") == sym:
                        s["fill_price"] = round(ep, 2)
                        s["fill_time"] = now.strftime("%H:%M")
                        if opt_symbol:
                            s["option_symbol"] = opt_symbol
                        s["filled"] = True
                        break
                _save_json(sel_file, sel)

        # Monitor open positions
        closed = []
        for key, p in list(positions.items()):
            if p.get("status") != "open":
                continue

            sym = p["symbol"]
            direction = p["direction"]
            ep = p["entry_price"]
            qty = p["qty"]
            trail = p.get("trail_sl", 0)
            best = p.get("best_price", ep)

            # Get current price from cache
            try:
                ltp = cache.get_last_price(sym)
                if ltp is None:
                    continue
            except Exception:
                continue

            il = (direction == "LONG")

            # Update trail
            if il and ltp > best:
                best = ltp
                trail = max(trail, best * (1 - trail_pct))
            elif not il and ltp < best:
                best = ltp
                trail = min(trail, best * (1 + trail_pct))

            p["best_price"] = round(best, 2)
            p["trail_sl"] = round(trail, 2)
            p["ltp"] = round(ltp, 2)

            target = p.get("target", 0)
            ex_p, reason = None, None

            if il:
                if target and ltp >= target:
                    ex_p, reason = target, "TARGET"
                elif ltp <= trail:
                    ex_p, reason = trail, "SL"
            else:
                if target and ltp <= target:
                    ex_p, reason = target, "TARGET"
                elif ltp >= trail:
                    ex_p, reason = trail, "SL"

            # Max hold check
            if ex_p is None and max_hold:
                entry_dt = datetime.strptime(p.get("entry_date", ""), "%Y-%m-%d")
                if (now - entry_dt).days >= max_hold:
                    ex_p = ltp
                    reason = "MAX_HOLD"

            if ex_p is not None:
                pnl = round((ex_p - ep) * qty, 2) if il else round((ep - ex_p) * qty, 2)
                p["exit_price"] = ex_p
                p["exit_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                p["exit_reason"] = reason
                p["pnl"] = pnl
                p["status"] = "closed"

                # Place broker exit order
                exit_action = "SELL" if direction == "LONG" else "BUY"
                exit_oid = _place_order(exit_action, sym, qty, direction, strategy_id, ex_p, p.get("option_symbol"))
                p["broker_exit_id"] = exit_oid

                # Verify exit fill — keep position open if order rejected
                if exit_oid and broker_api is not None:
                    fill_result = _check_order_fill(exit_oid)
                    if fill_result and fill_result.get("rejected"):
                        reason = f"EXIT_REJECTED"
                        print(f"    EXIT REJECTED for {sym}: {fill_result.get('reason','')}")
                        p["status"] = "open"
                        _send_telegram(project, f"🔴 *{sname}*: EXIT REJECTED {sym} ({fill_result.get('reason','')})")
                        continue
                    if fill_result and fill_result.get("filled"):
                        print(f"    Exit filled: {sym} avg={fill_result['avg_price']:.2f}")

                _append_csv(trade_log, TRADE_FIELDS, {
                    "symbol": sym, "direction": direction, "entry_price": ep,
                    "exit_price": ex_p, "qty": qty, "pnl": pnl,
                    "entry_time": p.get("entry_time", ""), "exit_time": p["exit_time"],
                    "exit_reason": reason, "strategy": strategy_id,
                    "date": now.strftime("%Y-%m-%d"),
                })

                emoji = "✅" if pnl > 0 else "🔴"
                opt_info = f" `{p['option_symbol']}`" if p.get("option_symbol") else ""
                print(f"  [{now.strftime('%H:%M:%S')}] {reason} {sym} P&L Rs {pnl:+,.0f}{opt_info}")
                _send_telegram(project, f"{emoji} *{sname}*: {reason} {sym} P&L Rs {pnl:+,.0f}{opt_info}")

        # Save positions
        _save_json(pos_file, positions)

        # EOD close
        if t >= 915:  # 15:15
            for key, p in list(positions.items()):
                if p.get("status") != "open":
                    continue
                try:
                    ltp = cache.get_last_price(p["symbol"])
                    if ltp:
                        il = (p["direction"] == "LONG")
                        pnl = round((ltp - p["entry_price"]) * p["qty"], 2) if il else round((p["entry_price"] - ltp) * p["qty"], 2)
                        p["exit_price"] = ltp
                        p["exit_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
                        p["exit_reason"] = "EOD"
                        p["pnl"] = pnl
                        p["status"] = "closed"
                        _append_csv(trade_log, TRADE_FIELDS, {
                            "symbol": p["symbol"], "direction": p["direction"], "entry_price": p["entry_price"],
                            "exit_price": ltp, "qty": p["qty"], "pnl": pnl,
                            "entry_time": p.get("entry_time", ""), "exit_time": p["exit_time"],
                            "exit_reason": "EOD", "strategy": strategy_id,
                            "date": now.strftime("%Y-%m-%d"),
                        })
                except Exception:
                    pass
            _save_json(pos_file, positions)
            print(f"[{now.strftime('%H:%M:%S')}] EOD close complete")
            break

        time.sleep(30)

    runtime = (datetime.now() - start_time).total_seconds()
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {project}/{strategy_id} live trader ended ({runtime:.0f}s)")


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Per-Strategy Agent")
    parser.add_argument("--project", required=True, choices=["SwingPortfolio", "IntraPortfolio"])
    parser.add_argument("--strategy", required=True, help="Strategy ID")
    parser.add_argument("--mode", required=True, choices=["scan", "live"], help="scan or live")
    args = parser.parse_args()

    if args.mode == "scan":
        lock_name = f"{args.project}-{args.strategy}-scan"
        try:
            from common.process_lock import acquire_lock, cleanup_previous
            if not acquire_lock(lock_name):
                sys.exit(0)
            cleanup_previous()
        except Exception:
            pass
        run_scan(args.project, args.strategy)
    elif args.mode == "live":
        run_live(args.project, args.strategy)
