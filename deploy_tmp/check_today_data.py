"""Check if today's 1-min data is available."""
import duckdb, os
os.environ["APP_ENV"] = "prod"
con = duckdb.connect("/opt/swing/data/market_data.duckdb")
r = con.execute("SELECT COUNT(*), MIN(datetime_ist), MAX(datetime_ist) FROM bars_1min WHERE datetime_ist >= '2026-07-17'").fetchone()
print(f"Today rows: {r[0]}, range: {r[1]} to {r[2]}")
con.close()
