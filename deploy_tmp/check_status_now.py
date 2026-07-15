import duckdb, pandas as pd, subprocess

# ── Chakra DB ──
con = duckdb.connect("/opt/ngen26/chakra/chakra.duckdb")

# Recent executions (last 30 min)
r = con.execute("""
    SELECT e.id, j.name, e.started_at, e.finished_at, e.status, e.exit_code
    FROM executions e
    JOIN jobs j ON e.job_id = j.id
    WHERE e.started_at >= now() - INTERVAL '30 minutes'
    ORDER BY e.id DESC
    LIMIT 25
""").fetchdf()
print("=== RECENT EXECS (last 30m) ===")
for _, row in r.iterrows():
    fin = str(row['finished_at'])[:19] if row['finished_at'] and not pd.isna(row['finished_at']) else "—"
    print(f"  {row['name']:35s} {str(row['started_at'])[:19]} → {fin} [{row['status']:8s}] exit={row['exit_code']}")

# Active processes
ps = subprocess.run(["ps", "aux"], capture_output=True, text=True)
print("\n=== RUNNING PROCESSES ===")
for line in ps.stdout.split("\n"):
    if any(x in line for x in ["live_trader", "unified_agent", "scan_pairs", "stock_selection", "PairTrading", "swap_streamlit"]):
        if "grep" not in line and "check_" not in line:
            parts = line.split()
            if len(parts) > 10:
                print(f"  PID={parts[1]} CPU={parts[2]}% {parts[10][:60]}")

con.close()
