import pandas as pd
import numpy as np
from core.strategy_base import BaseStrategy


class DonchianADXStrategy(BaseStrategy):
    strategy_id = "donchian_adx"
    name = "Donchian + ADX"
    description = "Donchian 20d breakout + ADX>35 + DI confirmation"

    def __init__(self):
        self.donchian_period = 20
        self.atr_period = 14
        self.adx_threshold = 35
        self.atr_multiplier = 2.0

    def prepare_data(self, df):
        d = df.copy()
        d.columns = [c.lower() for c in d.columns]
        holiday = (d["high"] == d["low"]) & (d["open"] == d["close"]) & (d["volume"] == 0)
        if holiday.any():
            d = d[~holiday]
        for col in ["open", "high", "low", "close", "volume"]:
            d[col] = d[col].astype(float)
        d["dc20"] = d["high"].rolling(self.donchian_period).max()
        d["dc20_low"] = d["low"].rolling(self.donchian_period).min()
        tr = pd.concat([d["high"]-d["low"], (d["high"]-d["close"].shift()).abs(), (d["low"]-d["close"].shift()).abs()], axis=1).max(axis=1)
        d["atr"] = tr.rolling(self.atr_period).mean()
        up = d["high"] - d["high"].shift(1)
        down = d["low"].shift(1) - d["low"]
        pdm = pd.Series(np.where((up > down) & (up > 0), up, 0), index=d.index)
        mdm = pd.Series(np.where((down > up) & (down > 0), down, 0), index=d.index)
        def w(s, p): return s.ewm(alpha=1/p, min_periods=p, adjust=False).mean()
        pdi = 100 * w(pdm, self.atr_period) / w(tr, self.atr_period)
        mdi = 100 * w(mdm, self.atr_period) / w(tr, self.atr_period)
        dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
        d["adx"] = w(dx, self.atr_period)
        d["pdi"] = pdi
        d["mdi"] = mdi
        return d

    def check_signal(self, row, prev):
        indicator_cols = ["dc20", "dc20_low", "adx", "pdi", "mdi"]
        if any(pd.isna(prev.get(c)) for c in indicator_cols):
            return None
        if row["adx"] <= self.adx_threshold or pd.isna(row.get("adx")) or pd.isna(row.get("pdi")) or pd.isna(row.get("mdi")):
            return None
        if row["pdi"] > row["mdi"] and row["close"] > prev["dc20"]:
            return {"direction": "LONG", "entry_price": row["open"]}
        if row["mdi"] > row["pdi"] and row["close"] < prev["dc20_low"]:
            return {"direction": "SHORT", "entry_price": row["open"]}
        return None
