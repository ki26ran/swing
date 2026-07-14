import pandas as pd


class BaseStrategy:
    """Abstract base class for all swing strategies."""

    @property
    def strategy_id(self):
        raise NotImplementedError

    @property
    def name(self):
        raise NotImplementedError

    @property
    def description(self):
        raise NotImplementedError

    def prepare_data(self, df):
        """Add indicator columns to the daily OHLCV DataFrame.
        Input columns may be capitalized (Open, High, Low, Close, Volume)
        or lowercase. Override in subclass.
        """
        raise NotImplementedError

    def _validate_df(self, df):
        """Ensure required OHLCV columns exist, normalize to lowercase, drop invalid rows."""
        d = df.copy()
        if d.columns.dtype != 'object':
            d.columns = [str(c) for c in d.columns]
        d.columns = [c.lower() for c in d.columns]
        required = ['open', 'high', 'low', 'close']
        missing = [c for c in required if c not in d.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")
        if 'volume' not in d.columns:
            d['volume'] = 0
        # Drop rows where close is NaN (incomplete candle)
        d = d.dropna(subset=['close'])
        return d

    def check_signal(self, row, prev_row):
        """Check for entry signal on a given day. Returns dict or None."""
        raise NotImplementedError

    def scan_universe(self, data_dict, date):
        """Scan all stocks for signals on a given date with error isolation."""
        signals = []
        scan_ts = pd.Timestamp(date)
        for sym, df in data_dict.items():
            try:
                if len(df) < 50:
                    continue
                if scan_ts not in df.index:
                    continue
                d = self._validate_df(df)
                d = self.prepare_data(d)
                if scan_ts not in d.index:
                    continue
                ix = list(d.index).index(scan_ts)
                if ix < 5:
                    continue
                row = d.iloc[ix]
                prev = d.iloc[ix - 1]
                sig = self.check_signal(row, prev)
                if sig:
                    signals.append({
                        "symbol": sym.replace(".NS", ""),
                        "direction": sig["direction"],
                        "entry_price": round(sig["entry_price"], 2),
                        "signal_date": str(scan_ts.date()),
                        "close_price": round(row["close"], 2),
                        "filled": False,
                    })
            except Exception:
                continue  # skip problematic stocks
        return signals
