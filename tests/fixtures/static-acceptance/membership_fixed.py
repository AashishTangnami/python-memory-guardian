import json, sys, time
n = int(sys.argv[1])
class Key:
    compares = 0
    def __init__(self, value): self.value = value
    def __hash__(self): return hash(self.value)
    def __eq__(self, other):
        Key.compares += 1
        return isinstance(other, Key) and self.value == other.value
values = [Key(i) for i in range(n)]
lookup = set(values)

started = time.perf_counter()
hits = 0
for _ in range(100):
    hits += Key(-1) in lookup
print(json.dumps({"n": n, "hits": hits, "equality_calls": Key.compares,
                  "elapsed_s": time.perf_counter() - started, "original_order_kept": values[0].value == 0}))
