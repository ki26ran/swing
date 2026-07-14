"""
Scan Logger — per-project scan history in DuckDB.
Creates project-specific DuckDB files (swing.duckdb, intra.duckdb).
"""
import os, json
from datetime import datetime
from typing import List, Dict, Optional

import pandas as pd


def _db_path(data_dir: str) -> str:
    """Return project-specific scan DB path based on project folder name."""
    name = os.path.basename(os.path.dirname(data_dir.rstrip('/').rstrip('\\')))
    if name == "SwingPortfolio":
        return os.path.join(data_dir, "swing.duckdb")
    elif name == "IntraPortfolio":
        return os.path.join(data_dir, "intra.duckdb")
    return _db_path(data_dir)


def _ensure_db(db_path: str):
    import duckdb
    con = duckdb.connect(db_path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS scan_summary (
            date DATE NOT NULL,
            strategy VARCHAR NOT NULL,
            timestamp TIMESTAMP,
            scanned INTEGER,
            signals INTEGER,
            direction VARCHAR,
            PRIMARY KEY (date, strategy)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS signal_details (
            date DATE NOT NULL,
            strategy VARCHAR NOT NULL,
            symbol VARCHAR NOT NULL,
            direction VARCHAR,
            entry DOUBLE,
            sl DOUBLE,
            target DOUBLE,
            span DOUBLE,
            extra JSON
        )
    """)
    con.close()


def save_scan(data_dir: str, date_str: str, strategy_id: str,
              results: dict) -> str:
    """
    Save a strategy's scan results for a given date.
    Overwrites previous data for the same date+strategy.
    
    Args:
        data_dir: project's data directory (e.g., C:/.../SwingPortfolio/data)
        date_str: "2026-06-17"
        strategy_id: "donchian_adx"
        results: {"scanned": 212, "signals": 1, "timestamp": "...", "direction": "Long", 
                   "data": [{"symbol": "...", "direction": "LONG", "entry": 100.0, ...}]}
    Returns: db_path
    """
    db_path = _db_path(data_dir)
    _ensure_db(db_path)

    import duckdb
    con = duckdb.connect(db_path)

    # Upsert scan_summary
    signals_data = results.get("data", results.get("signals_data", []))
    con.execute("""
        INSERT OR REPLACE INTO scan_summary (date, strategy, timestamp, scanned, signals, direction)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        pd.Timestamp(date_str).date(),
        strategy_id,
        datetime.now(),
        results.get("scanned", 0),
        len(signals_data),
        results.get("direction", ""),
    ))

    # Delete old signals for this date+strategy
    con.execute("""
        DELETE FROM signal_details WHERE date = ? AND strategy = ?
    """, (pd.Timestamp(date_str).date(), strategy_id))

    # Insert new signals
    for sig in signals_data:
        extra = {k: v for k, v in sig.items()
                 if k not in ("symbol", "direction", "entry", "sl", "target", "span")}
        extra_str = json.dumps(extra, default=str) if extra else None
        con.execute("""
            INSERT INTO signal_details
                (date, strategy, symbol, direction, entry, sl, target, span, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            pd.Timestamp(date_str).date(),
            strategy_id,
            sig.get("symbol", ""),
            sig.get("direction", ""),
            sig.get("entry"),
            sig.get("sl"),
            sig.get("target"),
            sig.get("span"),
            extra_str,
        ))

    con.close()
    return db_path


def query_scan_history(data_dir: str, days: int = 30) -> pd.DataFrame:
    """Return pivoted scan summary for dashboard display."""
    db_path = _db_path(data_dir)
    if not os.path.exists(db_path):
        return pd.DataFrame()

    import duckdb
    con = duckdb.connect(db_path)
    try:
        df = con.execute(f"""
            SELECT date, strategy, signals
            FROM scan_summary
            WHERE date >= CURRENT_DATE - {days}
            ORDER BY date DESC, strategy
        """).df()
        con.close()
        if df.empty:
            return df
        pivot = df.pivot(index="date", columns="strategy", values="signals").fillna(0).astype(int)
        pivot = pivot.sort_index(ascending=False)
        pivot["Total"] = pivot.sum(axis=1)
        return pivot
    except Exception:
        con.close()
        return pd.DataFrame()


def query_signal_details(data_dir: str, date_str: str = None,
                         strategy: str = None) -> pd.DataFrame:
    """Return individual signals, optionally filtered by date and strategy."""
    db_path = _db_path(data_dir)
    if not os.path.exists(db_path):
        return pd.DataFrame()

    import duckdb
    con = duckdb.connect(db_path)
    try:
        conditions = []
        params = []
        if date_str:
            conditions.append("date = ?")
            params.append(pd.Timestamp(date_str).date())
        if strategy:
            conditions.append("strategy = ?")
            params.append(strategy)
        where = " AND ".join(conditions) if conditions else "1=1"
        df = con.execute(f"""
            SELECT date, strategy, symbol, direction, entry, sl, target, span
            FROM signal_details
            WHERE {where}
            ORDER BY date DESC, strategy, symbol
        """, params).df()
        con.close()
        return df
    except Exception:
        con.close()
        return pd.DataFrame()


def rebuild_db(data_dir: str) -> str:
    """Rebuild scans.duckdb from JSON log files. Called after each scan run."""
    # Currently save_scan writes directly to DuckDB.
    # This function exists for future JSON → DB migrations.
    return _db_path(data_dir)
