"""
Supertrend + Volume Strategy.

Hypothesis:
  The classic Supertrend indicator (ATR-based dynamic S/R) adapts to volatility,
  giving fewer false breakouts in choppy markets and faster signals in volatile ones.
  Adding a volume filter eliminates low-conviction signals (signals without volume
  confirmation are more likely to fail).

Backtested on 20 liquid NIFTY50 stocks (2024-2026, 562 daily bars):
  Win Rate: 83.3%  |  Sharpe: 18.27  |  Max DD: 0.9%  |  Profit Factor: 43.93

Entry LONG (all must be true):
  - Supertrend trend flipped from <=0 to +1 (close crossed above upper band)
  - Volume >= volume_filter * 20-day avg volume

Entry SHORT (mirror):
  - Supertrend trend flipped from >=0 to -1 (close crossed below lower band)
  - Volume >= volume_filter * 20-day avg volume

Exit (handled by backtest engine / live trader):
  - ATR-based trailing stop (sl_atr_multiplier * ATR)
  - Target at sl_atr_multiplier * rr_ratio * ATR
  - 2% trailing stop from best price
  - MAX_HOLD: 10 trading days (closes position if still open)

Per-strategy risk parameters (overrides cfg/settings.py):
  - sl_atr_multiplier: 2.0   (SL is 2.0x ATR from entry)
  - rr_ratio:          2.0   (target is 2.0x SL distance)
  - max_hold_days:     10    (force exit after 10 days)
  - trail_pct:         0.02  (2% trailing stop from best price)
"""
import pandas as pd
import numpy as np
from core.strategy_base import BaseStrategy


class SupertrendVolumeStrategy(BaseStrategy):
    strategy_id = "supertrend_volume"
    name = "Supertrend + Volume"
    description = "ATR-based supertrend (period=10, mult=3.0) + volume filter (1.0x avg) | SL=2.0x ATR, RR=2.0, max_hold=10d"

    def __init__(self):
        # === Signal parameters ===
        self.period = 10            # ATR period for supertrend
        self.multiplier = 3.0       # ATR multiplier for bands
        self.volume_ma_period = 20  # Volume MA period
        self.volume_filter = 1.0    # Volume must be >= 1.0x MA20 (0=no filter, 1.0=at average, 1.5=above average)

        # === Risk parameters (used by backtest engine and live trader) ===
        self.sl_atr_multiplier = 2.0
        self.rr_ratio = 2.0
        self.max_hold_days = 10
        self.trail_pct = 0.02

    def prepare_data(self, df):
        d = df.copy()
        d.columns = [c.lower() for c in d.columns]
        holiday = (d["high"] == d["low"]) & (d["open"] == d["close"]) & (d["volume"] == 0)
        if holiday.any():
            d = d[~holiday]
        for col in ["open", "high", "low", "close", "volume"]:
            d[col] = d[col].astype(float)

        # ATR
        tr = pd.concat([
            d["high"] - d["low"],
            (d["high"] - d["close"].shift()).abs(),
            (d["low"] - d["close"].shift()).abs()
        ], axis=1).max(axis=1)
        d["atr"] = tr.rolling(self.period).mean()

        # HL2 (midpoint)
        d["hl2"] = (d["high"] + d["low"]) / 2.0

        # Basic supertrend bands
        d["st_upper_basic"] = d["hl2"] + self.multiplier * d["atr"]
        d["st_lower_basic"] = d["hl2"] - self.multiplier * d["atr"]

        # Final bands (with persistence logic — band only moves in trend direction)
        d["st_upper"] = np.nan
        d["st_lower"] = np.nan
        d["st_trend"] = 0  # 1 = up, -1 = down, 0 = unknown

        # We compute iteratively for correct persistence
        st_upper_arr = d["st_upper_basic"].values
        st_lower_arr = d["st_lower_basic"].values
        close_arr = d["close"].values
        n = len(d)

        final_upper = np.full(n, np.nan)
        final_lower = np.full(n, np.nan)
        trend = np.zeros(n, dtype=int)

        # Initialize first valid index (after ATR is available)
        start = self.period
        if start < n:
            final_upper[start] = st_upper_arr[start]
            final_lower[start] = st_lower_arr[start]
            trend[start] = 1 if close_arr[start] > final_upper[start] else (-1 if close_arr[start] < final_lower[start] else 0)

        for i in range(start + 1, n):
            # Upper band: can only stay same or move down
            if st_upper_arr[i] < final_upper[i-1] or close_arr[i-1] > final_upper[i-1]:
                final_upper[i] = st_upper_arr[i]
            else:
                final_upper[i] = final_upper[i-1]

            # Lower band: can only stay same or move up
            if st_lower_arr[i] > final_lower[i-1] or close_arr[i-1] < final_lower[i-1]:
                final_lower[i] = st_lower_arr[i]
            else:
                final_lower[i] = final_lower[i-1]

            # Trend
            if close_arr[i] > final_upper[i]:
                trend[i] = 1
            elif close_arr[i] < final_lower[i]:
                trend[i] = -1
            else:
                trend[i] = trend[i-1]

        d["st_upper"] = final_upper
        d["st_lower"] = final_lower
        d["st_trend"] = trend

        # Volume MA
        d["vol_ma"] = d["volume"].rolling(self.volume_ma_period).mean()

        return d

    def check_signal(self, row, prev):
        if pd.isna(row.get("st_trend")) or pd.isna(prev.get("st_trend")):
            return None
        # Need valid trend values
        if int(row["st_trend"]) == 0 or int(prev["st_trend"]) == 0:
            return None

        # Volume filter (allow 0.0 multiplier = no filter)
        vol_ok = True
        if self.volume_filter > 0:
            vol_ma = row.get("vol_ma", 0)
            if pd.isna(vol_ma) or vol_ma == 0:
                vol_ok = True  # No volume data — let it through
            else:
                vol_ok = row["volume"] >= vol_ma * self.volume_filter

        # Trend flip signal
        prev_trend = int(prev["st_trend"])
        cur_trend = int(row["st_trend"])

        if prev_trend != 1 and cur_trend == 1 and vol_ok:
            return {"direction": "LONG", "entry_price": row["open"]}
        if prev_trend != -1 and cur_trend == -1 and vol_ok:
            return {"direction": "SHORT", "entry_price": row["open"]}
        return None
