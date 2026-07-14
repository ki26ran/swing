import pandas as pd
import numpy as np
from core.strategy_base import BaseStrategy


class KeltnerRSIStrategy(BaseStrategy):
    strategy_id = "keltner_rsi"
    name = "Keltner + RSI + Volume"
    description = "Keltner 20/2 + RSI 55/45 + Volume 2.0x + ADX>25"

    def __init__(self):
        self.keltner_period = 20
        self.keltner_multiplier = 2.0
        self.atr_period = 14
        self.rsi_period = 14
        self.rsi_long_min = 55
        self.rsi_short_max = 45
        self.volume_ma_period = 20
        self.volume_multiplier = 2.0
        self.adx_threshold = 25

    def compute_ema(self, series, period):
        return series.ewm(span=period, adjust=False).mean()

    def prepare_data(self, df):
        d = df.copy()
        d.columns = [c.lower() for c in d.columns]
        holiday = (d["high"] == d["low"]) & (d["open"] == d["close"]) & (d["volume"] == 0)
        if holiday.any():
            d = d[~holiday]
        for col in ["open", "high", "low", "close", "volume"]:
            d[col] = d[col].astype(float)
        d["ema20"] = self.compute_ema(d["close"], self.keltner_period)
        tr = pd.concat([d["high"]-d["low"], (d["high"]-d["close"].shift()).abs(), (d["low"]-d["close"].shift()).abs()], axis=1).max(axis=1)
        d["atr"] = tr.rolling(self.atr_period).mean()
        d["kelt_upper"] = d["ema20"] + d["atr"] * self.keltner_multiplier
        d["kelt_lower"] = d["ema20"] - d["atr"] * self.keltner_multiplier
        delta = d["close"].diff()
        gain = delta.where(delta > 0, 0)
        loss = (-delta).where(delta < 0, 0)
        avg_g = gain.rolling(self.rsi_period).mean()
        avg_l = loss.rolling(self.rsi_period).mean()
        rs = avg_g / avg_l.replace(0, np.nan)
        d["rsi"] = 100 - (100 / (1 + rs))
        d["vol_ma20"] = d["volume"].rolling(self.volume_ma_period).mean()
        h, l, c = d["high"], d["low"], d["close"]
        up = h - h.shift(1)
        down = l.shift(1) - l
        pdm = pd.Series(np.where((up > down) & (up > 0), up, 0), index=d.index)
        mdm = pd.Series(np.where((down > up) & (down > 0), down, 0), index=d.index)
        def w(s, p): return s.ewm(alpha=1/p, min_periods=p, adjust=False).mean()
        atr_w = w(tr, 14)
        pdi = 100 * w(pdm, 14) / atr_w
        mdi = 100 * w(mdm, 14) / atr_w
        dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
        d["adx"] = w(dx, 14)
        d["pdi"] = pdi
        d["mdi"] = mdi
        return d

    def check_signal(self, row, prev):
        indicator_cols = ["close", "volume", "kelt_upper", "kelt_lower", "rsi", "adx", "pdi", "mdi", "vol_ma20"]
        if any(pd.isna(row.get(c)) for c in indicator_cols):
            return None
        if row["adx"] <= self.adx_threshold:
            return None
        if row["close"] > row["kelt_upper"] and row["pdi"] > row["mdi"] and row["rsi"] > self.rsi_long_min and row["volume"] > row["vol_ma20"] * self.volume_multiplier:
            return {"direction": "LONG", "entry_price": row["open"]}
        if row["close"] < row["kelt_lower"] and row["mdi"] > row["pdi"] and row["rsi"] < self.rsi_short_max and row["volume"] > row["vol_ma20"] * self.volume_multiplier:
            return {"direction": "SHORT", "entry_price": row["open"]}
        return None
