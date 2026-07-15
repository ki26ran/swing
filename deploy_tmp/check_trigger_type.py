import duckdb, pandas as pd
con = duckdb.connect("/opt/ngen26/chakra/chakra.duckdb", read_only=True)
r = con.execute("SELECT id, job_id, trigger_type, interval_sec, window_start, window_end FROM triggers WHERE id IN (26, 13, 20)").fetchdf()
print(r.to_string())
con.close()
