import duckdb
con = duckdb.connect("/opt/ngen26/chakra/chakra.duckdb")

# Count stuck running
cnt = con.execute("SELECT count(*) FROM executions WHERE status='running'").fetchone()[0]
print(f"Stuck running records: {cnt}")

# Reset all running → interrupted
con.execute("UPDATE executions SET status='interrupted', finished_at=now() WHERE status='running'")
print("Reset all running → interrupted")

# Verify trigger 26 is interval
r = con.execute("SELECT id, job_id, trigger_type, interval_sec, window_start, window_end FROM triggers WHERE id=26").fetchdf()
print("\nTrigger 26:")
print(r.to_string())

con.close()
