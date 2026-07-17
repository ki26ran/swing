"""
Backtest a momentum strategy based on 2-min candle range scanner.
For each flagged >2% candle, enter in the direction of the move.
Track subsequent candles to see if continuation or reversal.
"""
import os, sys, json, duckdb, pandas as pd
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
    WHERE ticker IN ({ph})
      AND datetime_ist >= ? AND datetime_ist < ?
    ORDER BY ticker, datetime_ist
""", clean + [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]).df()
con.close()

df["datetime_ist"] = pd.to_datetime(df["datetime_ist"])
df["time"] = df["datetime_ist"].dt.time
# Filter market hours after 09:50
df = df[(df["time"] >= pd.Timestamp("09:50").time()) & (df["time"] <= pd.Timestamp("15:20").time())]
df["bucket"] = df["datetime_ist"].dt.floor("2min")
df["date"] = df["datetime_ist"].dt.date

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

# Strategy parameters
RR = 2.0          # Risk:Reward ratio
TRAIL_AFTER_R = 1.0  # Trail after 1R profit
TRAIL_PCT = 0.003    # 0.3% trailing

trades = []

# Process each flagged candle
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
    if rp < 2.0:
        continue

    # Entry in the direction of the move
    direction = "LONG" if b_close > b_open else "SHORT"
    bucket_end = grp["datetime_ist"].max()

    if direction == "LONG":
        entry = b_high * 1.001
        sl = b_low * 0.999
    else:
        entry = b_low * 0.999
        sl = b_high * 1.001

    if np.isnan(entry) or np.isnan(sl) or entry <= 0 or sl <= 0:
        continue
    span = abs(entry - sl)
    if span < 0.5 or np.isnan(span):
        continue
    target = entry + span * RR if direction == "LONG" else entry - span * RR
    qty = max(1, int(20000 / entry))

    # Walk forward through subsequent 1-min candles to simulate exit
    ticker_data = df[(df["ticker"] == ticker) & (df["datetime_ist"] > bucket_end) & (df["date"] == bucket_ts.date())].sort_values("datetime_ist")
    if len(ticker_data) < 2:
        continue  # no subsequent data

    pnl = 0
    exit_reason = "EOD"
    exit_price = entry
    best_price = entry
    trail_active = False
    trail_sl = sl

    for i in range(len(ticker_data)):
        r = ticker_data.iloc[i]
        h_, l_, c_ = float(r["high"]), float(r["low"]), float(r["close"])
        idx_time = r["datetime_ist"]

        if idx_time.time() >= pd.Timestamp("15:20").time():
            exit_price = c_
            exit_reason = "EOD"
            break

        if direction == "LONG":
            if h_ >= target:
                exit_price = target; exit_reason = "TARGET"; break
            if l_ <= trail_sl:
                exit_price = trail_sl; exit_reason = "TRAIL" if trail_active else "SL"; break
            if c_ > best_price:
                best_price = c_
                if (best_price - entry) >= span * TRAIL_AFTER_R:
                    trail_active = True
            if trail_active:
                trail_sl = max(trail_sl, best_price * (1 - TRAIL_PCT))
        else:
            if l_ <= target:
                exit_price = target; exit_reason = "TARGET"; break
            if h_ >= trail_sl:
                exit_price = trail_sl; exit_reason = "TRAIL" if trail_active else "SL"; break
            if c_ < best_price:
                best_price = c_
                if (entry - best_price) >= span * TRAIL_AFTER_R:
                    trail_active = True
            if trail_active:
                trail_sl = min(trail_sl, best_price * (1 + TRAIL_PCT))
    else:
        exit_price = float(ticker_data.iloc[-1]["close"])
        exit_reason = "EOD"

    if direction == "LONG":
        pnl = (exit_price - entry) * qty
    else:
        pnl = (entry - exit_price) * qty

    trades.append({
        "symbol": ticker, "date": bucket_ts.strftime("%m-%d"), "time": bucket_ts.strftime("%H:%M"),
        "direction": direction, "range_pct": round(rp, 1),
        "entry": round(entry, 2), "exit": round(exit_price, 2), "sl": round(sl, 2),
        "pnl": round(pnl, 2), "qty": qty, "reason": exit_reason,
    })

# Results
df_trades = pd.DataFrame(trades)
print(f"\n{'='*70}")
print(f"MOMENTUM STRATEGY BACKTEST ({len(trades)} trades)")
print(f"{'='*70}")

if not trades:
    print("No trades generated.")
    exit()

pnls = [t["pnl"] for t in trades]
wins = [p for p in pnls if p > 0]
losses = [p for p in pnls if p <= 0]
n = len(pnls)

print(f"Total P&L: {sum(pnls):+,.2f}")
print(f"Win Rate: {len(wins)/n*100:.1f}% ({len(wins)}W/{len(losses)}L)")
print(f"Profit Factor: {abs(sum(wins)/sum(losses)):.2f}" if losses and sum(losses)!=0 else "Profit Factor: N/A")
print(f"Avg Win: {np.mean(wins):.2f}" if wins else "Avg Win: N/A")
print(f"Avg Loss: {abs(np.mean(losses)):.2f}" if losses else "Avg Loss: N/A")

print(f"\n{'='*70}")
print(f"TOP 10 TRADES")
print(f"{'='*70}")
for t in sorted(trades, key=lambda x: -abs(x["pnl"]))[:15]:
    e = "🟢" if t["pnl"] > 0 else "🔴"
    print(f"{e} {t['symbol']:15s} {t['date']:6s} {t['time']:6s} {t['direction']:6s} range={t['range_pct']:>4.1f}%  entry={t['entry']:>8.2f} exit={t['exit']:>8.2f} pnl={t['pnl']:>+8.2f} ({t['reason']})")

# By direction
print(f"\nBY DIRECTION:")
for dir_ in ["LONG", "SHORT"]:
    d_trades = [t for t in trades if t["direction"] == dir_]
    if d_trades:
        dp = [t["pnl"] for t in d_trades]
        dw = [p for p in dp if p > 0]
        print(f"  {dir_:6s}: {len(d_trades):>3d} trades, P&L={sum(dp):>+8,.0f}, WR={len(dw)/len(d_trades)*100:.0f}%")

# By exit reason
print(f"\nBY EXIT REASON:")
for reason in ["TARGET", "TRAIL", "SL", "EOD"]:
    r_trades = [t for t in trades if t["reason"] == reason]
    if r_trades:
        rp = [t["pnl"] for t in r_trades]
        print(f"  {reason:8s}: {len(r_trades):>3d} trades, P&L={sum(rp):>+8,.0f}")

print(f"\n{'='*70}")
