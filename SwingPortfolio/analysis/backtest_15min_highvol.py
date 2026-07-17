"""
15-min range breakout on high-volatility stocks only.
Filters universe to top 20% most volatile stocks first.
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

all_tickers = sorted(SYMBOLS_DATA.keys())[:212]
end = datetime.now()
start = end - timedelta(days=60)
clean = [s.replace(".NS","").replace(".BO","") for s in all_tickers]
ph = ", ".join(["?" for _ in clean])

print("Loading hourly data for volatility ranking...")
hourly = con.execute(f"""
    SELECT ticker, datetime_ist, high, low, close
    FROM hourly_bars
    WHERE ticker IN ({ph}) AND datetime_ist >= ? AND datetime_ist < ?
    ORDER BY ticker, datetime_ist
""", clean + [(start - timedelta(days=30)).strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]).df()
hourly["datetime_ist"] = pd.to_datetime(hourly["datetime_ist"])

# Compute volatility ranking per stock
vol_rank = {}
for ticker, grp in hourly.groupby("ticker"):
    grp = grp.sort_values("datetime_ist")
    if len(grp) < 10:
        continue
    returns = grp["close"].pct_change().dropna()
    if len(returns) < 10:
        continue
    vol = returns.std() * np.sqrt(252)  # annualized volatility
    avg_range = (grp["high"] - grp["low"]).mean()
    avg_price = grp["close"].mean()
    range_pct = avg_range / avg_price * 100 if avg_price > 0 else 0
    vol_rank[ticker] = {"vol": float(vol), "range_pct": float(range_pct)}

# Sort by volatility and take top 40 (20%)
sorted_by_vol = sorted(vol_rank.items(), key=lambda x: -x[1]["vol"])
top_n = 40
top_tickers = [t for t, _ in sorted_by_vol[:top_n]]

print(f"Top {top_n} volatile stocks:")
for t, v in sorted_by_vol[:top_n]:
    print(f"  {t:20s} vol={v['vol']:.2f} avg_range={v['range_pct']:.2f}%")

# Load 1-min data for these tickers only
print(f"\nLoading 1-min data for {len(top_tickers)} high-vol stocks...")
clean_top = [s.replace(".NS","").replace(".BO","") for s in top_tickers]
ph2 = ", ".join(["?" for _ in clean_top])
df = con.execute(f"""
    SELECT ticker, datetime_ist, open, high, low, close, volume
    FROM bars_1min
    WHERE ticker IN ({ph2}) AND datetime_ist >= ? AND datetime_ist < ?
    ORDER BY ticker, datetime_ist
""", clean_top + [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]).df()
con.close()

df["datetime_ist"] = pd.to_datetime(df["datetime_ist"])
df["time"] = df["datetime_ist"].dt.time
df["date"] = df["datetime_ist"].dt.date
df = df[(df["time"] >= pd.Timestamp("09:50").time()) & (df["time"] <= pd.Timestamp("15:20").time())]
print(f"Loaded {len(df)} rows, {df['ticker'].nunique()} symbols")


def test_strategy(params):
    window = params.get("window", 15)
    min_range_pct = params.get("min_range_pct", 0.0)
    rr = params.get("rr", 2.0)
    dir_filter = params.get("dir_filter", "BOTH")
    trades = []

    for ticker in top_tickers:
        tdata = df[df["ticker"] == ticker]
        for day, grp in tdata.groupby("date"):
            grp = grp.sort_values("datetime_ist")
            if len(grp) < window + 2:
                continue

            for i in range(window, len(grp) - 1):
                wd = grp.iloc[i - window:i]
                curr = grp.iloc[i]
                range_high = float(wd["high"].max())
                range_low = float(wd["low"].min())
                range_span = range_high - range_low
                c_high = float(curr["high"])
                c_low = float(curr["low"])

                if min_range_pct > 0:
                    pc = float(curr["close"])
                    if pc > 0 and (range_span / pc * 100) < min_range_pct:
                        continue

                is_long = c_high > range_high
                is_short = c_low < range_low

                if not is_long and not is_short:
                    continue
                direction = "LONG" if is_long else "SHORT"
                if dir_filter == "LONG" and direction != "LONG": continue
                if dir_filter == "SHORT" and direction != "SHORT": continue

                sl_dist = range_span * 0.5
                if direction == "LONG":
                    entry = range_high * 1.001
                    sl = entry - sl_dist
                    target = entry + sl_dist * rr
                else:
                    entry = range_low * 0.999
                    sl = entry + sl_dist
                    target = entry - sl_dist * rr

                if np.isnan(entry) or np.isnan(sl) or entry <= 0 or sl <= 0:
                    continue
                if abs(entry - sl) < 0.5:
                    continue

                qty = max(1, int(20000 / entry))
                remaining = grp.iloc[i + 1:]
                if len(remaining) < 2:
                    continue

                pnl = 0; exit_price = entry; exit_reason = "EOD"
                for j in range(len(remaining)):
                    r = remaining.iloc[j]
                    h_, l_, c_ = float(r["high"]), float(r["low"]), float(r["close"])
                    if r["datetime_ist"].time() >= pd.Timestamp("15:20").time():
                        exit_price = c_; exit_reason = "EOD"; break
                    if direction == "LONG":
                        if h_ >= target: exit_price = target; exit_reason = "TARGET"; break
                        if l_ <= sl: exit_price = sl; exit_reason = "SL"; break
                    else:
                        if l_ <= target: exit_price = target; exit_reason = "TARGET"; break
                        if h_ >= sl: exit_price = sl; exit_reason = "SL"; break
                else:
                    exit_price = float(remaining.iloc[-1]["close"]); exit_reason = "EOD"

                pnl = (exit_price - entry) * qty if direction == "LONG" else (entry - exit_price) * qty
                trades.append(pnl)

    return trades


configs = [
    ("15min, both dir", {"window": 15}),
    ("15min, LONG only", {"window": 15, "dir_filter": "LONG"}),
    ("15min, SHORT only", {"window": 15, "dir_filter": "SHORT"}),
    ("10min, LONG only", {"window": 10, "dir_filter": "LONG"}),
    ("15min + 1% range", {"window": 15, "min_range_pct": 1.0}),
    ("15min + 1.5% range", {"window": 15, "min_range_pct": 1.5}),
    ("15min + RR3", {"window": 15, "rr": 3.0}),
]

print(f"\n{'='*90}")
print(f"{'Variant':40s} {'Trades':>7s} {'P&L':>10s} {'WR':>6s} {'AvgW':>8s} {'AvgL':>8s} {'PF':>6s}")
print(f"{'-'*90}")

results = []
for name, params in configs:
    trades = test_strategy(params)
    n = len(trades)
    if n == 0: continue
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
    print(f"  {name:40s} P&L={total:>+8,.0f}  WR={wr:>5.1f}%  Trades={n:>4d}  PF={pf:.2f}")
