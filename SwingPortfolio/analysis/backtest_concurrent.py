"""
Track max concurrent positions for 15-min breakout on high-vol stocks.
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

# Get top 40 volatile stocks
hourly = con.execute(f"""
    SELECT ticker, datetime_ist, high, low, close FROM hourly_bars
    WHERE ticker IN ({ph}) AND datetime_ist >= ? AND datetime_ist < ?
""", clean + [(start - timedelta(days=30)).strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]).df()
hourly["datetime_ist"] = pd.to_datetime(hourly["datetime_ist"])

vol_rank = {}
for ticker, grp in hourly.groupby("ticker"):
    grp = grp.sort_values("datetime_ist")
    if len(grp) < 10: continue
    ret = grp["close"].pct_change().dropna()
    vol = float(ret.std() * np.sqrt(252))
    avg_r = float((grp["high"] - grp["low"]).mean())
    avg_p = float(grp["close"].mean())
    vol_rank[ticker] = {"vol": vol, "range_pct": avg_r / avg_p * 100 if avg_p > 0 else 0}

top_tickers = [t for t, _ in sorted(vol_rank.items(), key=lambda x: -x[1]["vol"])[:40]]

# Load 1-min data
clean_top = [s.replace(".NS","").replace(".BO","") for s in top_tickers]
ph2 = ", ".join(["?" for _ in clean_top])
df = con.execute(f"""
    SELECT ticker, datetime_ist, open, high, low, close, volume
    FROM bars_1min WHERE ticker IN ({ph2}) AND datetime_ist >= ? AND datetime_ist < ?
    ORDER BY ticker, datetime_ist
""", clean_top + [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]).df()
con.close()

df["datetime_ist"] = pd.to_datetime(df["datetime_ist"])
df["time"] = df["datetime_ist"].dt.time
df["date"] = df["datetime_ist"].dt.date
df = df[(df["time"] >= pd.Timestamp("09:50").time()) & (df["time"] <= pd.Timestamp("15:20").time())]

# Simulate strategy with position tracking
window = 15
rr = 2.0
min_range_pct = 1.0
CAP_PER_STOCK = 20000
all_entries = []  # (entry_time, exit_time, symbol)

for ticker in top_tickers:
    tdata = df[df["ticker"] == ticker]
    for day, grp in tdata.groupby("date"):
        grp = grp.sort_values("datetime_ist")
        if len(grp) < window + 2: continue

        for i in range(window, len(grp) - 1):
            wd = grp.iloc[i - window:i]
            curr = grp.iloc[i]
            rh = float(wd["high"].max())
            rl = float(wd["low"].min())
            rs = rh - rl
            ch = float(curr["high"])
            cl = float(curr["low"])

            if min_range_pct > 0:
                pc = float(curr["close"])
                if pc > 0 and (rs / pc * 100) < min_range_pct: continue

            is_long = ch > rh
            is_short = cl < rl
            if not is_long and not is_short: continue
            direction = "LONG" if is_long else "SHORT"

            sl_dist = rs * 0.5
            if direction == "LONG":
                entry = rh * 1.001
                sl = entry - sl_dist
                target = entry + sl_dist * rr
            else:
                entry = rl * 0.999
                sl = entry + sl_dist
                target = entry - sl_dist * rr

            if np.isnan(entry) or np.isnan(sl) or entry <= 0 or sl <= 0 or abs(entry - sl) < 0.5:
                continue

            remaining = grp.iloc[i + 1:]
            if len(remaining) < 2: continue

            exit_time = None
            for j in range(len(remaining)):
                r = remaining.iloc[j]
                h_, l_ = float(r["high"]), float(r["low"])
                if r["datetime_ist"].time() >= pd.Timestamp("15:20").time():
                    exit_time = r["datetime_ist"]; break
                if direction == "LONG":
                    if h_ >= target: exit_time = r["datetime_ist"]; break
                    if l_ <= sl: exit_time = r["datetime_ist"]; break
                else:
                    if l_ <= target: exit_time = r["datetime_ist"]; break
                    if h_ >= sl: exit_time = r["datetime_ist"]; break
            else:
                exit_time = remaining.iloc[-1]["datetime_ist"]

            entry_time = curr["datetime_ist"]
            all_entries.append((entry_time, exit_time, ticker, direction))

# Compute max concurrent positions
print(f"\nTotal trades: {len(all_entries)}")
print(f"Over {((end-start).days)} days")

# Bucket by minute
minute_counts = defaultdict(int)
for entry_time, exit_time, ticker, direction in all_entries:
    cur = entry_time.replace(second=0)
    end_min = exit_time.replace(second=0)
    while cur <= end_min:
        minute_counts[cur] += 1
        cur += timedelta(minutes=1)

max_concurrent = max(minute_counts.values()) if minute_counts else 0
avg_concurrent = sum(minute_counts.values()) / len(minute_counts) if minute_counts else 0

print(f"Max concurrent positions at any minute: {max_concurrent}")
print(f"Avg concurrent positions: {avg_concurrent:.1f}")
print(f"Total minutes with active trades: {len(minute_counts)}")

# Capital calculation
cap_per_pos = CAP_PER_STOCK
print(f"\n=== CAPITAL REQUIREMENT ===")
print(f"Per position: ₹{cap_per_pos:,.0f}")
print(f"Max required (concurrent × per position): ₹{max_concurrent * cap_per_pos:,.0f}")
print(f"Avg required: ₹{avg_concurrent * cap_per_pos:,.0f}")

# Distribution
from collections import Counter
conc_dist = Counter(minute_counts.values())
print(f"\n=== CONCURRENT POSITION DISTRIBUTION ===")
for n in sorted(conc_dist):
    print(f"  {n:>2d} concurrent: {conc_dist[n]:>6d} minutes ({conc_dist[n]/len(minute_counts)*100:.1f}%)")

# Max daily
daily_max = defaultdict(int)
for entry_time, exit_time, ticker, direction in all_entries:
    day = entry_time.date()
    daily_max[day] += 1

print(f"\n=== DAILY ENTRY COUNT ===")
daily_counts = sorted(daily_max.items())
print(f"Max entries in a day: {max(daily_max.values())}")
print(f"Avg entries per day: {sum(daily_max.values())/len(daily_max):.1f}")
for day, cnt in daily_counts:
    print(f"  {day}: {cnt} entries")
