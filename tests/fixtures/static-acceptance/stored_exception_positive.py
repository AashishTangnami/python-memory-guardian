import gc, json, sys, tracemalloc
n = int(sys.argv[1])
PAYLOAD = 2000


def load(i):
    payload = bytearray(PAYLOAD)  # a local variable of the frame that raises
    raise ValueError("row %d" % i)


class Collector:
    def __init__(self):
        self.errors = []

    def run(self, count):
        for i in range(count):
            try:
                load(i)
            except ValueError as exc:
                self.errors.append(exc)


tracemalloc.start()
collector = Collector()
collector.run(n)
gc.collect()
current, peak = tracemalloc.get_traced_memory()
print(json.dumps({"n": n, "errors": len(collector.errors), "payload_bytes": PAYLOAD,
                  "messages": [str(error) for error in collector.errors],
                  "python_current_bytes": current, "python_peak_bytes": peak}))
