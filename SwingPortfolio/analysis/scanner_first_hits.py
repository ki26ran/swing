"""
Show first occurrence only for each flagged stock.
"""
import os, sys, duckdb, pandas as pd
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
start = datetime(end.year, end.month, end.day)  # today only
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

df["datetime_ist"] = pd.to_datetime(df["datetime_ist"])
df["time"] = df["datetime_ist"].dt.time
df = df[(df["time"] >= pd.Timestamp("09:50").time()) & (df["time"] <= pd.Timestamp("15:30").time())]
df["bucket"] = df["datetime_ist"].dt.floor("2min")

# Load previous closes
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

first_hits = {}  # {ticker: first_record}

for (ticker, bucket_ts), grp in df.groupby(["ticker", "bucket"]):
    grp = grp.sort_values("datetime_ist")
    if len(grp) < 2:
        continue
    b_open = float(grp.iloc[0]["open"])
    b_high = float(grp["high"].max())
    b_low = float(grp["low"].min())
    b_close = float(grp.iloc[-1]["close"])
    b_range = b_high - b_low
    pc = get_pc(ticker, bucket_ts)
    if not pc or pc <= 0:
        continue
    rp = b_range / pc * 100
    if rp >= 2.0 and ticker not in first_hits:
        dir_ = "LONG" if b_close > b_open else "SHORT"
        first_hits[ticker] = {
            "date": bucket_ts.strftime("%m-%d"),
            "time": bucket_ts.strftime("%H:%M"),
            "range_pct": round(rp, 2),
            "range": round(b_range, 2),
            "price": round(b_close, 2),
            "direction": dir_,
            "high": round(b_high, 2),
            "low": round(b_low, 2),
        }

print(f"{'Symbol':15s} {'Date':8s} {'Time':6s} {'Dir':6s} {'Range%':7s} {'Range':7s} {'Price':8s} {'High':8s} {'Low':8s}")
print("="*75)
for sym in sorted(first_hits, key=lambda s: first_hits[s]["date"] + first_hits[s]["time"]):
    h = first_hits[sym]
    e = "🟢" if h["direction"] == "LONG" else "🔴"
    print(f"{e} {sym:13s} {h['date']:8s} {h['time']:6s} {h['direction']:6s} {h['range_pct']:>6.1f}% {h['range']:>6.2f} {h['price']:>7.2f} {h['high']:>7.2f} {h['low']:>7.2f}")

print(f"\nTotal unique stocks flagged (first occurrence): {len(first_hits)}")
