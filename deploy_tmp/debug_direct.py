import duckdb, pandas as pd
from datetime import datetime, timezone, timedelta

con = duckdb.connect("/opt/ngen26/chakra/chakra.duckdb")

# Check the last execution for trigger 26
r = con.execute("""
    SELECT id, trigger_id, status, started_at, finished_at, exit_code
    FROM executions
    WHERE trigger_id = 26
    ORDER BY started_at DESC
    LIMIT 5
""").fetchdf()
print("=== Last 5 executions for trigger 26 ===")
print(r.to_string())

# Also check the server timezone info
print(f"\nServer TZ info: UTC offset = {timezone.utc}")
print(f"Local time (no tz): {datetime.now()}")
print(f"UTC time: {datetime.now(timezone.utc)}")
print(f"IST time: {datetime.now(timezone(timedelta(hours=5, minutes=30)))}")

# Check what _get_last_run_time returns with the OLD logic (replace)
row = con.execute("""
    SELECT MAX(started_at) FROM executions
    WHERE trigger_id = 26 AND status != 'running'
""").fetchone()
dt = row[0] if row and row[0] else None
print(f"\nMAX(started_at) from DB: {dt} (type={type(dt).__name__})")

con.close()
