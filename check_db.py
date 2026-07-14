import duckdb
con = duckdb.connect('SwingPortfolio/data/swing.duckdb')
tables = con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchdf()
print("Tables:", tables['table_name'].tolist())
for t in tables['table_name']:
    cnt = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
    print(f'{t}: {cnt} rows')
con.close()
