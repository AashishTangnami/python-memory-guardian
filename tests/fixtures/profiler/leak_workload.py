import time
CACHE = []
def handle(i):
    CACHE.append(bytearray(2_000_000))     # L4: leaks 2 MB per call
    tmp = bytearray(8_000_000)             # L5: big but freed every call
    time.sleep(0.15)
    return len(tmp)
for i in range(30):
    handle(i)
