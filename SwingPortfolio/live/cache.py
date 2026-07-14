"""
SwingTradingCache — isolated DuckDB for SwingPortfolio live trading data.
Separate from market_data.duckdb to avoid file-locking conflicts.
"""
import os, sys, json, duckdb
from datetime import datetime
from typing import Dict, List, Any

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)
ROOT = os.path.dirname(BASE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DB_PATH = os.path.join(BASE, "swingtrading.duckdb")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_positions (
    id INTEGER PRIMARY KEY,
    symbol VARCHAR NOT NULL,
    direction VARCHAR NOT NULL,
    entry_price DOUBLE,
    initial_sl DOUBLE,
    target DOUBLE,
    trail_sl DOUBLE,
    best_price DOUBLE,
    qty INTEGER,
    entry_time VARCHAR,
    entry_date VARCHAR,
    status VARCHAR DEFAULT 'OPEN',
    last_candle_idx INTEGER,
    last_checked VARCHAR,
    strategy VARCHAR,
    last_price DOUBLE
);

CREATE TABLE IF NOT EXISTS positions_history (
    id BIGINT,
    symbol VARCHAR NOT NULL,
    direction VARCHAR NOT NULL,
    entry_price DOUBLE,
    qty INTEGER,
    initial_sl DOUBLE,
    target DOUBLE,
    entry_time VARCHAR,
    entry_date VARCHAR,
    exit_price DOUBLE,
    exit_time VARCHAR,
    exit_reason VARCHAR,
    pnl DOUBLE,
    strategy VARCHAR
);

CREATE TABLE IF NOT EXISTS selections (
    strategy_id VARCHAR NOT NULL,
    date VARCHAR NOT NULL,
    data VARCHAR NOT NULL,
    PRIMARY KEY (strategy_id, date)
);

CREATE TABLE IF NOT EXISTS trade_log (
    id BIGINT,
    strategy_id VARCHAR NOT NULL,
    timestamp TIMESTAMP,
    symbol VARCHAR,
    direction VARCHAR,
    entry DOUBLE,
    sl DOUBLE,
    target DOUBLE,
    qty INTEGER,
    entry_time VARCHAR,
    scanned_at VARCHAR
);

CREATE TABLE IF NOT EXISTS signals (
    id BIGINT,
    timestamp TIMESTAMP,
    symbol VARCHAR,
    direction VARCHAR,
    entry DOUBLE,
    sl DOUBLE,
    target DOUBLE,
    qty INTEGER,
    entry_time VARCHAR,
    strategy VARCHAR,
    scanned_at VARCHAR
);
"""


class SwingTradingCache:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _init_db(self):
        if SwingTradingCache._initialized:
            return
        con = None
        try:
            con = duckdb.connect(DB_PATH)
            for stmt in _SCHEMA.strip().split(";"):
                s = stmt.strip()
                if s:
                    con.execute(s)
            SwingTradingCache._initialized = True
        except Exception as e:
            print(f"[WARN] SwingTradingCache init: {e}")
        finally:
            if con:
                con.close()

    def _db_read(self):
        self._init_db()
        return duckdb.connect(DB_PATH, read_only=True)

    def _db_write(self):
        self._init_db()
        return duckdb.connect(DB_PATH)

    # --- Positions ---
    def load_positions(self) -> list:
        con = None
        try:
            con = self._db_read()
            return [dict(r) for r in con.execute("SELECT * FROM live_positions ORDER BY id").fetchall()]
        except Exception:
            return []
        finally:
            if con:
                con.close()

    def save_positions(self, positions: list) -> bool:
        con = None
        try:
            con = self._db_write()
            con.execute("DELETE FROM live_positions")
            for i, p in enumerate(positions):
                con.execute("""
                    INSERT INTO live_positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (i, p.get("symbol",""), p.get("direction",""),
                      p.get("entry_price"), p.get("initial_sl"), p.get("target"),
                      p.get("trail_sl"), p.get("best_price"), p.get("qty"),
                      p.get("entry_time",""), p.get("entry_date",""),
                      p.get("status","OPEN"), p.get("last_candle_idx",0),
                      p.get("last_checked",""), p.get("strategy",""),
                      p.get("last_price")))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] save_positions: {e}")
            return False
        finally:
            if con:
                con.close()

    def clear_positions(self) -> bool:
        con = None
        try:
            con = self._db_write()
            con.execute("DELETE FROM live_positions")
            con.commit()
            return True
        except Exception:
            return False
        finally:
            if con:
                con.close()

    # --- History ---
    def load_history(self) -> list:
        con = None
        try:
            con = self._db_read()
            return [dict(r) for r in con.execute("SELECT * FROM positions_history ORDER BY id").fetchall()]
        except Exception:
            return []
        finally:
            if con:
                con.close()

    def append_history(self, entry: dict) -> bool:
        con = None
        try:
            con = self._db_write()
            next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM positions_history").fetchone()[0]
            con.execute("""
                INSERT INTO positions_history (id, symbol, direction, entry_price, qty, initial_sl, target,
                    entry_time, entry_date, exit_price, exit_time, exit_reason, pnl, strategy)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (next_id, entry.get("symbol",""), entry.get("direction",""),
                  entry.get("entry_price"), entry.get("qty"),
                  entry.get("initial_sl"), entry.get("target"),
                  entry.get("entry_time",""), entry.get("entry_date",""),
                  entry.get("exit_price"), entry.get("exit_time",""),
                  entry.get("exit_reason",""), entry.get("pnl"),
                  entry.get("strategy","")))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] append_history: {e}")
            return False
        finally:
            if con:
                con.close()

    def save_history(self, entries: list) -> bool:
        """Replace all history entries."""
        con = None
        try:
            con = self._db_write()
            con.execute("DELETE FROM positions_history")
            for i, e in enumerate(entries):
                con.execute("""
                    INSERT INTO positions_history (id, symbol, direction, entry_price, qty, initial_sl, target,
                        entry_time, entry_date, exit_price, exit_time, exit_reason, pnl, strategy)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (i + 1, e.get("symbol",""), e.get("direction",""),
                      e.get("entry_price"), e.get("qty"),
                      e.get("initial_sl"), e.get("target"),
                      e.get("entry_time",""), e.get("entry_date",""),
                      e.get("exit_price"), e.get("exit_time",""),
                      e.get("exit_reason",""), e.get("pnl"),
                      e.get("strategy","")))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] save_history: {e}")
            return False
        finally:
            if con:
                con.close()

    def clear_history(self) -> bool:
        con = None
        try:
            con = self._db_write()
            con.execute("DELETE FROM positions_history")
            con.commit()
            return True
        except Exception:
            return False
        finally:
            if con:
                con.close()

    # --- Selections ---
    def save_selections(self, strategy_id: str, date_str: str, data: dict) -> bool:
        con = None
        try:
            con = self._db_write()
            con.execute("""
                INSERT OR REPLACE INTO selections (strategy_id, date, data) VALUES (?, ?, ?)
            """, (strategy_id, date_str, json.dumps(data)))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] save_selections: {e}")
            return False
        finally:
            if con:
                con.close()

    def load_selections(self, strategy_id: str = None, date_str: str = None) -> dict:
        con = None
        try:
            con = self._db_read()
            if strategy_id and date_str:
                r = con.execute("SELECT data FROM selections WHERE strategy_id=? AND date=?", (strategy_id, date_str)).fetchone()
                return json.loads(r[0]) if r else {}
            elif strategy_id:
                r = con.execute("SELECT data FROM selections WHERE strategy_id=? ORDER BY date DESC LIMIT 1", (strategy_id,)).fetchone()
                return json.loads(r[0]) if r else {}
            else:
                rows = con.execute("SELECT * FROM selections").fetchall()
                return {f"{r[0]}|{r[1]}": json.loads(r[2]) for r in rows}
        except Exception:
            return {}
        finally:
            if con:
                con.close()

    # --- Trade log ---
    def append_trade_log(self, strategy_id: str, row: dict) -> bool:
        con = None
        try:
            con = self._db_write()
            next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM trade_log").fetchone()[0]
            con.execute("""
                INSERT INTO trade_log (id, strategy_id, timestamp, symbol, direction, entry, sl, target,
                    qty, entry_time, scanned_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (next_id, strategy_id, datetime.now(), row.get("symbol",""), row.get("direction",""),
                  row.get("entry"), row.get("sl"), row.get("target"), row.get("qty"),
                  row.get("entry_time",""), row.get("scanned_at","")))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] append_trade_log: {e}")
            return False
        finally:
            if con:
                con.close()

    # --- Signals ---
    def append_signal(self, row: dict) -> bool:
        con = None
        try:
            con = self._db_write()
            next_id = con.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM signals").fetchone()[0]
            con.execute("""
                INSERT INTO signals (id, timestamp, symbol, direction, entry, sl, target, qty, entry_time, strategy, scanned_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (next_id, datetime.now(), row.get("symbol",""), row.get("direction",""),
                  row.get("entry"), row.get("sl"), row.get("target"), row.get("qty"),
                  row.get("entry_time",""), row.get("strategy",""), row.get("scanned_at","")))
            con.commit()
            return True
        except Exception as e:
            print(f"[WARN] append_signal: {e}")
            return False
        finally:
            if con:
                con.close()


def get_swing_cache() -> SwingTradingCache:
    return SwingTradingCache()
