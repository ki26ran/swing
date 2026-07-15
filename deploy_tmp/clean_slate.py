import duckdb
con = duckdb.connect("/opt/ngen26/chakra/chakra.duckdb")

# Delete ALL executions for trigger 26 
cnt = con.execute("SELECT count(*) FROM executions WHERE trigger_id=26").fetchone()[0]
print(f"Deleting {cnt} executions for trigger 26")
con.execute("DELETE FROM executions WHERE trigger_id=26")

# Verify
cnt2 = con.execute("SELECT count(*) FROM executions WHERE trigger_id=26").fetchone()[0]
print(f"Remaining: {cnt2}")

# Check last_run after delete
row = con.execute("SELECT MAX(started_at) FROM executions WHERE trigger_id=26 AND status != 'running'").fetchone()
print(f"Last run (should be None): {row[0]}")

con.close()
