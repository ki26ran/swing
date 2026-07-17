"""
Find edge in 2-min candle momentum strategy.
Tests multiple parameter combinations to find what works.
"""
import os, sys, duckdb, pandas as pd
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(BASE))
from common.market_data.cache import get_cache
from common.universes import SYMBOLS_DATA

cache = get_cache()
con = duckdb.connect(cache.db_path, read_only=True)

tickers = sorted(SYMBOLS_DATA.keys())[:212]
end = datetime.now()
start = end - timedelta(days=60)
clean = [s.replace(".NS","").replace(".BO","") for s in tickers]
ph = ", ".join(["?" for _ in clean])

print("Loading data...")
df = con.execute(f"""
    SELECT ticker, datetime_ist, open, high, low, close, volume
    FROM bars_1min
    WHERE ticker IN ({ph}) AND datetime_ist >= ? AND datetime_ist < ?
    ORDER BY ticker, datetime_ist
""", clean + [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]).df()
con.close()

df["datetime_ist"] = pd.to_datetime(df["datetime_ist"])
df["time"] = df["datetime_ist"].dt.time
df = df[(df["time"] >= pd.Timestamp("09:50").time()) & (df["time"] <= pd.Timestamp("15:20").time())]
df["bucket"] = df["datetime_ist"].dt.floor("2min")
df["date"] = df["datetime_ist"].dt.date

# Load daily for prev close
con2 = duckdb.connect(cache.db_path, read_only=True)
daily = con2.execute(f"""
    SELECT ticker, date, close FROM daily_bars
    WHERE ticker IN ({ph}) AND date >= ? AND date < ?
""", clean + [(start - timedelta(days=5)).strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]).df()
con2.close()

pv = {}
for ticker, grp in daily.groupby("ticker"):
    grp = grp.sort_values("date")
    pv[ticker] = dict(zip(grp["date"].astype(str), grp["close"]))

def get_pc(ticker, dt):
    for o in range(8):
        d = (dt.date() - timedelta(days=o)).strftime("%Y-%m-%d")
        m = pv.get(ticker, {})
        if d in m:
            return float(m[d])
    return None

def test_strategy(params):
    """Run backtest with given params. Returns trade list."""
    min_range = params.get("min_range", 2.0)
    rr = params.get("rr", 2.0)
    trail_pct = params.get("trail_pct", 0.003)
    trail_after_r = params.get("trail_after_r", 1.0)
    dir_filter = params.get("dir_filter", "BOTH")  # LONG, SHORT, BOTH
    vol_filter = params.get("vol_filter", False)
    lookback_entries = params.get("lookback_entries", 0)  # skip if already entered this stock recently

    trades = []
    recent_entries = defaultdict(int)  # {ticker: count}

    for (ticker, bucket_ts), grp in df.groupby(["ticker", "bucket"]):
        grp = grp.sort_values("datetime_ist")
        if len(grp) < 2:
            continue
        b_open = float(grp.iloc[0]["open"])
        b_high = float(grp["high"].max())
        b_low = float(grp["low"].min())
        b_close = float(grp.iloc[-1]["close"])
        b_vol = float(grp["volume"].sum())
        b_range = b_high - b_low
        pc = get_pc(ticker, bucket_ts)
        if not pc or pc <= 0:
            continue
        rp = b_range / pc * 100
        if rp < min_range:
            continue

        direction = "LONG" if b_close > b_open else "SHORT"
        if dir_filter == "LONG" and direction != "LONG":
            continue
        if dir_filter == "SHORT" and direction != "SHORT":
            continue

        # Volume filter: skip if volume < average
        if vol_filter:
            day = bucket_ts.date()
            day_data = df[(df["ticker"] == ticker) & (df["date"] == day)]
            if len(day_data) > 0:
                avg_vol = day_data["volume"].mean()
                if b_vol < avg_vol:
                    continue

        # Limit entries per stock per day
        if lookback_entries > 0 and recent_entries[ticker] >= lookback_entries:
            continue

        # Entry
        if direction == "LONG":
            entry = b_high * 1.001
            sl = b_low * 0.999
        else:
            entry = b_low * 0.999
            sl = b_high * 1.001

        if np.isnan(entry) or np.isnan(sl) or entry <= 0 or sl <= 0:
            continue
        span = abs(entry - sl)
        if span < 0.5:
            continue

        target = entry + span * rr if direction == "LONG" else entry - span * rr
        qty = max(1, int(20000 / entry))

        # Simulate exit
        ticker_data = df[(df["ticker"] == ticker) & (df["datetime_ist"] > bucket_ts) & (df["date"] == bucket_ts.date())].sort_values("datetime_ist")
        if len(ticker_data) < 2:
            continue

        pnl = 0; exit_price = entry; best_price = entry
        trail_active = False; trail_sl = sl; exit_reason = "EOD"

        for i in range(len(ticker_data)):
            r = ticker_data.iloc[i]
            h_, l_, c_ = float(r["high"]), float(r["low"]), float(r["close"])
            if r["datetime_ist"].time() >= pd.Timestamp("15:20").time():
                exit_price = c_; exit_reason = "EOD"; break
            if direction == "LONG":
                if h_ >= target: exit_price = target; exit_reason = "TARGET"; break
                if l_ <= trail_sl: exit_price = trail_sl; exit_reason = "TRAIL" if trail_active else "SL"; break
                if c_ > best_price: best_price = c_
                if (best_price - entry) >= span * trail_after_r: trail_active = True
                if trail_active: trail_sl = max(trail_sl, best_price * (1 - trail_pct))
            else:
                if l_ <= target: exit_price = target; exit_reason = "TARGET"; break
                if h_ >= trail_sl: exit_price = trail_sl; exit_reason = "TRAIL" if trail_active else "SL"; break
                if c_ < best_price: best_price = c_
                if (entry - best_price) >= span * trail_after_r: trail_active = True
                if trail_active: trail_sl = min(trail_sl, best_price * (1 + trail_pct))
        else:
            exit_price = float(ticker_data.iloc[-1]["close"]); exit_reason = "EOD"

        pnl = (exit_price - entry) * qty if direction == "LONG" else (entry - exit_price) * qty
        trades.append(pnl)
        recent_entries[ticker] += 1

    return trades


# Test parameter combinations
configs = [
    # (name, params)
    ("Baseline (2%, RR2, both dir)", {"min_range": 2.0, "rr": 2.0}),
    ("LONG only", {"min_range": 2.0, "rr": 2.0, "dir_filter": "LONG"}),
    ("LONG + Volume", {"min_range": 2.0, "rr": 2.0, "dir_filter": "LONG", "vol_filter": True}),
    ("LONG + RR3", {"min_range": 2.0, "rr": 3.0, "dir_filter": "LONG"}),
    ("LONG + RR2.5", {"min_range": 2.0, "rr": 2.5, "dir_filter": "LONG"}),
    ("LONG + trail 0.5%", {"min_range": 2.0, "rr": 2.0, "dir_filter": "LONG", "trail_pct": 0.005}),
    ("LONG + trail late", {"min_range": 2.0, "rr": 2.0, "dir_filter": "LONG", "trail_after_r": 2.0}),
    ("LONG + 1 entry/stock/day", {"min_range": 2.0, "rr": 2.0, "dir_filter": "LONG", "lookback_entries": 1}),
    ("Wider range 2.5% LONG", {"min_range": 2.5, "rr": 2.0, "dir_filter": "LONG"}),
    ("All: Vol+RR3+1entry", {"min_range": 2.0, "rr": 3.0, "dir_filter": "LONG", "vol_filter": True, "lookback_entries": 1}),
]

print(f"\n{'='*90}")
print(f"{'Variant':40s} {'Trades':>7s} {'P&L':>10s} {'WR':>6s} {'AvgW':>8s} {'AvgL':>8s} {'PF':>6s}")
print(f"{'-'*90}")

results = []
for name, params in configs:
    trades = test_strategy(params)
    n = len(trades)
    if n == 0:
        print(f"{name:40s} {0:>7d}")
        continue
    wins = [p for p in trades if p > 0]
    losses = [p for p in trades if p <= 0]
    total = sum(trades)
    wr = len(wins)/n*100
    aw = np.mean(wins) if wins else 0
    al = abs(np.mean(losses)) if losses else 0
    pf = abs(sum(wins)/sum(losses)) if losses and sum(losses) != 0 else 0
    print(f"{name:40s} {n:>7d} {total:>+10,.0f} {wr:>5.1f}% {aw:>8,.0f} {al:>8,.0f} {pf:>5.2f}")
    results.append((name, n, total, wr, pf))

print(f"\n{'='*90}")
print("RANKED BY P&L")
for name, n, total, wr, pf in sorted(results, key=lambda x: -x[2]):
    print(f"  {name:40s} P&L={total:>+8,.0f}  WR={wr:>5.1f}%  Trades={n:>3d}  PF={pf:.2f}")
