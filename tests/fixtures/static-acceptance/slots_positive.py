import json, sys, tracemalloc
class Row:

    def __init__(self, a, b, c):
        self.a, self.b, self.c = a, b, c
n = int(sys.argv[1])
tracemalloc.start()
rows = []
for _ in range(n): rows.append(Row(1, None, 3))
current, peak = tracemalloc.get_traced_memory()
rows[0].a = 9
assert rows[0].b is None and rows[1].a == 1
print(json.dumps({"n": n, "objects": len(rows), "python_current_bytes": current,
                  "python_peak_bytes": peak, "mutability_null_order_preserved": True}))
