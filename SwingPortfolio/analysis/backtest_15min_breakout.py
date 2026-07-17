"""
Backtest: Rolling 15-min range breakout strategy.
- Track high/low of last 15 min
- Enter when price breaks the range
- Target = 1x range, SL = 0.5x range
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

print("Loading 1-min data...")
df = con.execute(f"""
    SELECT ticker, datetime_ist, open, high, low, close, volume
    FROM bars_1min
    WHERE ticker IN ({ph}) AND datetime_ist >= ? AND datetime_ist < ?
    ORDER BY ticker, datetime_ist
""", clean + [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]).df()
con.close()

df["datetime_ist"] = pd.to_datetime(df["datetime_ist"])
df["time"] = df["datetime_ist"].dt.time
df["date"] = df["datetime_ist"].dt.date
# Market hours after 09:50
df = df[(df["time"] >= pd.Timestamp("09:50").time()) & (df["time"] <= pd.Timestamp("15:20").time())]
print(f"Loaded {len(df)} rows, {df['ticker'].nunique()} symbols")

# Load daily for prev close reference
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
    """Rolling 15-min range breakout strategy."""
    window = params.get("window", 15)  # 15-min rolling window
    min_range_pct = params.get("min_range_pct", 0.0)  # minimum range % to consider
    rr = params.get("rr", 2.0)  # target = rr * SL distance (SL=0.5x range, so target=range)
    dir_filter = params.get("dir_filter", "BOTH")
    min_vol_ratio = params.get("min_vol_ratio", 0.0)  # volume ratio filter

    trades = []
    recent_entries = defaultdict(int)
    unique_tickers = df["ticker"].unique()

    for ticker in unique_tickers:
        tdata = df[df["ticker"] == ticker]
        for day, grp in tdata.groupby("date"):
            grp = grp.sort_values("datetime_ist")
        if len(grp) < window + 2:
            continue
        day_entries = 0

        # Rolling window
        for i in range(window, len(grp) - 1):
            window_data = grp.iloc[i - window:i]
            curr = grp.iloc[i]

            range_high = window_data["high"].max()
            range_low = window_data["low"].min()
            range_span = float(range_high - range_low)

            c_open = float(curr["open"])
            c_high = float(curr["high"])
            c_low = float(curr["low"])
            c_close = float(curr["close"])
            c_vol = float(curr["volume"])

            # Min range filter
            if min_range_pct > 0:
                pc = get_pc(ticker, curr["datetime_ist"])
                if not pc or pc <= 0 or (range_span / pc * 100) < min_range_pct:
                    continue

            # Volume filter
            if min_vol_ratio > 0:
                avg_vol = grp["volume"].mean()
                if avg_vol > 0 and c_vol < avg_vol * min_vol_ratio:
                    continue

            # Breakout detection
            is_long = c_high > range_high  # price broke above range
            is_short = c_low < range_low    # price broke below range

            # Direction filter
            if dir_filter == "LONG" and not is_long:
                continue
            if dir_filter == "SHORT" and not is_short:
                continue
            if not is_long and not is_short:
                continue

            direction = "LONG" if is_long else "SHORT"

            # Entry / SL / Target
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
            span = abs(entry - sl)
            if span < 0.5:
                continue

            qty = max(1, int(20000 / entry))

            # Simulate exit on subsequent candles
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
            day_entries += 1
            if day_entries >= 3:
                break

    return trades


# Test variants
configs = [
    ("15min range, both dir", {"window": 15}),
    ("15min range, LONG only", {"window": 15, "dir_filter": "LONG"}),
    ("15min range, SHORT only", {"window": 15, "dir_filter": "SHORT"}),
    ("10min range, LONG only", {"window": 10, "dir_filter": "LONG"}),
    ("20min range, LONG only", {"window": 20, "dir_filter": "LONG"}),
    ("15min + min range 1%", {"window": 15, "min_range_pct": 1.0}),
    ("15min + min range 1.5%", {"window": 15, "min_range_pct": 1.5}),
    ("15min + vol 1.5x", {"window": 15, "min_vol_ratio": 1.5}),
    ("15min + RR3 (1.5x range)", {"window": 15, "rr": 3.0}),
]

print(f"\n{'='*90}")
print(f"{'Variant':50s} {'Trades':>7s} {'P&L':>10s} {'WR':>6s} {'AvgW':>8s} {'AvgL':>8s} {'PF':>6s}")
print(f"{'-'*90}")

results = []
for name, params in configs:
    trades = test_strategy(params)
    n = len(trades)
    if n == 0:
        print(f"{name:50s} {0:>7d}")
        continue
    wins = [p for p in trades if p > 0]
    losses = [p for p in trades if p <= 0]
    total = sum(trades)
    wr = len(wins)/n*100
    aw = np.mean(wins) if wins else 0
    al = abs(np.mean(losses)) if losses else 0
    pf = abs(sum(wins)/sum(losses)) if losses and sum(losses) != 0 else 0
    print(f"{name:50s} {n:>7d} {total:>+10,.0f} {wr:>5.1f}% {aw:>8,.0f} {al:>8,.0f} {pf:>5.2f}")
    results.append((name, n, total, wr, pf))

print(f"\nRANKED BY P&L")
for name, n, total, wr, pf in sorted(results, key=lambda x: -x[2]):
    print(f"  {name:50s} P&L={total:>+8,.0f}  WR={wr:>5.1f}%  Trades={n:>4d}  PF={pf:.2f}")
