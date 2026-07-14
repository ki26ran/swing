import duckdb
con = duckdb.connect(r'C:\ngen26\common\market_data\market_data.duckdb')
tables = con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchdf()
print("Tables:", tables['table_name'].tolist())
for t in tables['table_name']:
    cnt = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
    print(f'{t}: {cnt} rows')
# Check date range for daily_bars
if 'daily_bars' in tables['table_name'].tolist():
    dr = con.execute("SELECT min(date), max(date) FROM daily_bars").fetchone()
    print(f'daily_bars range: {dr[0]} to {dr[1]}')
    syms = con.execute("SELECT count(DISTINCT ticker) FROM daily_bars").fetchone()[0]
    print(f'daily_bars symbols: {syms}')
con.close()
