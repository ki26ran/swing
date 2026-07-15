import duckdb
con = duckdb.connect("/opt/ngen26/chakra/chakra.duckdb")

# Check recent executions for swing scan jobs (22, 23, 24)
r = con.execute("""
    SELECT e.id, e.job_id, j.name, e.started_at, e.finished_at, e.exit_code, e.status, e.error, e.output
    FROM executions e
    JOIN jobs j ON e.job_id = j.id
    WHERE e.job_id IN (22, 23, 24)
    ORDER BY e.id DESC
    LIMIT 10
""").fetchdf()
print("=== Recent Swing Scan Executions ===")
for _, row in r.iterrows():
    print(f"\nJob #{row['job_id']} ({row['name']})")
    print(f"  Started: {row['started_at']}")
    print(f"  Finished: {row['finished_at']}")
    print(f"  Exit: {row['exit_code']}, Status: {row['status']}")
    if row['error']:
        print(f"  Error: {row['error'][:500]}")
    if row['output']:
        print(f"  Output: {row['output'][:500]}")
con.close()
