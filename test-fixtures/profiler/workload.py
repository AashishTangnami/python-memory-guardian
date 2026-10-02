"""Workload with KNOWN behaviour per line, to validate the profiler's attribution."""
import hashlib, random, time

def python_loop():
    t = 0
    for i in range(6_000_000):        # L6: pure Python bytecode
        t += i * i
    return t

def native_releases_gil(blob):
    for _ in range(40):
        hashlib.sha256(blob).digest()  # L12: C code, releases the GIL (large buffer)

def native_holds_gil(data):
    for _ in range(4):
        sorted(data)                   # L16: C code, holds the GIL

def waiting():
    time.sleep(0.8)                    # L19: off-CPU (system time)

LEAK = []
def leaky():
    for _ in range(12):
        LEAK.append(bytearray(4_000_000))   # L24: grows and is never freed
        time.sleep(0.25)

def temporary():
    big = [0] * 20_000_000                  # L28: big but freed right after
    return len(big)

if __name__ == "__main__":
    python_loop()
    native_releases_gil(b"x" * 20_000_000)
    native_holds_gil([random.random() for _ in range(1_500_000)])
    waiting()
    leaky()
    temporary()
