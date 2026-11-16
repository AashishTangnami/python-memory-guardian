import asyncio, functools, hashlib, json, threading, time
from concurrent.futures import ThreadPoolExecutor
from functools import cache, lru_cache

import requests


# ---- RAM fragmentation ---------------------------------------------------
class Point:                       # no __slots__
    def __init__(self, x, y):
        self.x, self.y = x, y

class SlimPoint:
    __slots__ = ("x", "y")
    def __init__(self, x, y):
        self.x, self.y = x, y

class Bad(Exception):              # exempt: exception type
    pass

rows = []
for i in range(1000):
    rows.append({"id": i, "name": "n"})      # per-row dict
    rows.append((i, i))                      # per-row tuple
    rows.append(i)                           # fine: not a container literal
    pts = Point(i, i)                        # no-slots class in loop
    ok = SlimPoint(i, i)                     # fine

# ---- Text inflation --------------------------------------------------------
out = ""
for r in rows:
    out += "x"                               # str += in loop
total = 0
for r in rows:
    total += 1                               # fine: int
with open("big.txt") as f:
    words = f.read().split()                 # whole file -> many strs
    lines = f.readlines()                    # whole file -> list of strs
    for line in f:                           # fine: lazy
        pass

# ---- Memory swell ----------------------------------------------------------
s = sum([r for r in range(10)])              # list materialised
g = sum(r for r in range(10))                # fine: generator
for r in list(range(10)):                    # needless copy
    pass

class Repo:
    @cache                                   # method + unbounded -> keeps self alive
    def lookup(self, k):
        return k
    @lru_cache(maxsize=128)                  # bounded: FAQ-recommended, fine
    def lookup2(self, k):
        return k

@functools.lru_cache(maxsize=None)           # unbounded module-level cache (info)
def parse(k):
    return k

# ---- Single-threaded stall -------------------------------------------------
async def handler():
    time.sleep(1)                            # blocks the event loop
    requests.get("https://example.com")      # blocks the event loop
    await asyncio.sleep(1)                   # fine

seen = []
for r in range(100):
    if r in seen:                            # O(n) scan per iteration
        continue
    seen.append(r)
seen_set = set()
for r in range(100):
    if r in seen_set:                        # fine: set
        pass

def crunch(numbers):                         # CPU-bound pure Python over its input
    acc = 0
    for n in numbers:
        acc += n * n
    return acc

def fetch(url):                              # I/O-bound: threads are appropriate
    return requests.get(url).text

def digest(blobs):                           # native, GIL-releasing work
    return [hashlib.sha256(b).hexdigest() for b in blobs]

def constant_work():                         # loop doesn't depend on input data
    return sum(i for i in range(10))

with ThreadPoolExecutor() as ex:
    ex.submit(crunch, [1, 2, 3])             # flagged (info)
    ex.submit(fetch, "https://example.com")  # fine
    ex.map(digest, [[b"a"]])                 # fine
    ex.submit(constant_work)                 # fine: not data-dependent
threading.Thread(target=crunch, args=([1],)).start()   # flagged (info)
threading.Thread(target=fetch, args=("u",)).start()    # fine
