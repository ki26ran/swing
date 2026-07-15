import duckdb, pandas as pd
con = duckdb.connect("/opt/ngen26/chakra/chakra.duckdb")

# Check the last execution for trigger 26
r = con.execute("""
    SELECT id, trigger_id, status, started_at, finished_at, exit_code
    FROM executions
    WHERE trigger_id = 26
    ORDER BY started_at DESC
    LIMIT 10
""").fetchdf()
print("=== Last 10 executions for trigger 26 ===")
print(r.to_string())

# Check what _get_last_run_time would return
r2 = con.execute("""
    SELECT MAX(started_at) as last_run
    FROM executions
    WHERE trigger_id = 26 AND status != 'running'
""").fetchdf()
print(f"\n_get_last_run_time would return: {r2['last_run'][0]}")

# Also check the trigger settings
r3 = con.execute("""
    SELECT t.*, j.name, j.enabled
    FROM triggers t
    JOIN jobs j ON t.job_id = j.id
    WHERE t.id = 26
""").fetchdf()
print(f"\nTrigger 26 settings:")
print(r3.to_string())

con.close()
