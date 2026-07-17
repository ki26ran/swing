"""
Backtest the live scanner using historical 1-min data from DuckDB.
Simulates running every 2 minutes over past N days.
"""
import os, sys, json, duckdb, pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.dirname(BASE))

from common.market_data.cache import get_cache
from common.universes import SYMBOLS_DATA

cache = get_cache()
con = duckdb.connect(cache.db_path, read_only=True)

# Get universe
tickers = sorted(SYMBOLS_DATA.keys())[:212]
print(f"Universe: {len(tickers)} stocks")

# Date range
end = datetime.now()
start = end - timedelta(days=30)
print(f"Range: {start.date()} to {end.date()} ({end-start} days)")

# Load 1-min data for the period
print("Loading 1-min bars...")
clean = [s.replace(".NS","").replace(".BO","") for s in tickers]
ph = ", ".join(["?" for _ in clean])
df = con.execute(f"""
    SELECT ticker, datetime_ist, open, high, low, close
    FROM bars_1min
    WHERE ticker IN ({ph})
      AND datetime_ist >= ? AND datetime_ist < ?
    ORDER BY ticker, datetime_ist
""", clean + [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]).df()
con.close()

if df.empty:
    print("No 1-min data available.")
    exit()

df["datetime_ist"] = pd.to_datetime(df["datetime_ist"])
print(f"Loaded {len(df)} rows, {df['ticker'].nunique()} symbols")

# Filter to market hours only (09:15 to 15:30)
df["time"] = df["datetime_ist"].dt.time
market_start = pd.Timestamp("09:50").time()
market_end = pd.Timestamp("15:30").time()
df = df[(df["time"] >= market_start) & (df["time"] <= market_end)]
print(f"After market hours filter (09:50-15:30): {len(df)} rows")

# Group into 2-min buckets
df["bucket"] = df["datetime_ist"].dt.floor("2min")

# Load daily for prev_close
con2 = duckdb.connect(cache.db_path, read_only=True)
daily = con2.execute(f"""
    SELECT ticker, date, close FROM daily_bars
    WHERE ticker IN ({ph}) AND date >= ? AND date < ?
    ORDER BY ticker, date
""", clean + [(start - timedelta(days=5)).strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]).df()
con2.close()

# Build prev_close map: {ticker: {date: close}}
prev_close_map = {}
for ticker, grp in daily.groupby("ticker"):
    grp = grp.sort_values("date")
    prev_close_map[ticker] = dict(zip(grp["date"].astype(str), grp["close"]))


def get_prev_close(ticker, dt):
    """Get the most recent daily close before this datetime."""
    date_str = dt.strftime("%Y-%m-%d")
    pm = prev_close_map.get(ticker, {})
    # Try exact date, then go back up to 7 days
    for offset in range(8):
        d = (dt.date() - timedelta(days=offset)).strftime("%Y-%m-%d")
        if d in pm:
            return float(pm[d])
    return None


# Simulate scanner every 2 minutes across all days
MIN_RANGE_PCT = 2.0

# Group data by (ticker, bucket)
grouped = df.groupby(["ticker", "bucket"])

results_by_day = defaultdict(list)
total_buckets = 0
flagged_buckets = 0

for (ticker, bucket_ts), grp in grouped:
    grp = grp.sort_values("datetime_ist")
    if len(grp) < 2:
        continue
    total_buckets += 1

    b_open = float(grp.iloc[0]["open"])
    b_high = float(grp["high"].max())
    b_low = float(grp["low"].min())
    b_close = float(grp.iloc[-1]["close"])
    b_range = b_high - b_low

    prev_close = get_prev_close(ticker, bucket_ts)
    if not prev_close or prev_close <= 0:
        continue

    range_pct = b_range / prev_close * 100
    if range_pct >= MIN_RANGE_PCT:
        flagged_buckets += 1
        day_key = bucket_ts.strftime("%Y-%m-%d")
        direction = "LONG" if b_close > b_open else "SHORT"
        results_by_day[day_key].append({
            "symbol": ticker,
            "time": bucket_ts.strftime("%H:%M"),
            "range_pct": round(range_pct, 2),
            "range": round(b_range, 2),
            "price": round(b_close, 2),
            "direction": direction,
            "prev_close": round(prev_close, 2),
        })

# Summary
print(f"\n{'='*70}")
print(f"BACKTEST RESULTS (30 days)")
print(f"{'='*70}")
print(f"Total 2-min buckets analyzed: {total_buckets}")
print(f"Flagged (>2% range): {flagged_buckets} ({(flagged_buckets/total_buckets*100):.2f}%)")
print(f"Avg per day: {flagged_buckets/30:.1f}")

# By day
print(f"\n{'Day':15s} {'Flagged':>8s} {'Unique Symbols':>15s}")
print("-"*40)
for day in sorted(results_by_day.keys())[-15:]:  # last 15 days
    hits = results_by_day[day]
    syms = len(set(h["symbol"] for h in hits))
    print(f"{day:15s} {len(hits):>8d} {syms:>15d}")

# Top volatile stocks
print(f"\n=== TOP 20 MOST COMMONLY FLAGGED STOCKS ===")
sym_count = defaultdict(int)
for day_hits in results_by_day.values():
    for h in day_hits:
        sym_count[h["symbol"]] += 1
for sym, cnt in sorted(sym_count.items(), key=lambda x: -x[1])[:20]:
    print(f"  {sym:20s} -> flagged {cnt} times")

# Hourly distribution (market hours only)
print(f"\n=== HOURLY DISTRIBUTION (09:50-15:30) ===")
hourly = defaultdict(int)
for day_hits in results_by_day.values():
    for h in day_hits:
        hr = h["time"].split(":")[0]
        hourly[int(hr)] += 1
for hr in sorted(hourly):
    print(f"  {hr:02d}:00 -> {hourly[hr]} flags")
