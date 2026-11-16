import json, sqlite3, sys, tracemalloc
n = int(sys.argv[1])
connection = sqlite3.connect(":memory:")
connection.execute("create table records (value integer, label text)")
connection.executemany("insert into records values (?, ?)", ((i, "value") for i in range(n)))
cursor = connection.execute("select value, label from records")
tracemalloc.start()
total = 0

for row in cursor:
    total += row[0]
retained_rows = 1

current, peak = tracemalloc.get_traced_memory()
print(json.dumps({"n": n, "sum": total, "python_current_bytes": current,
                  "python_peak_bytes": peak, "rows_retained_at_cleanup": retained_rows,
                  "native_sqlite_db_memory": "not measured by this Python allocator witness"}))
connection.close()
