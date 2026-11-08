import concurrent.futures as cf
import functools
import pandas as pd
from pandas import read_csv as rc

class Holder:
    numbers = []

buf = b""
for i in range(3):
    buf += b"x"                  # bytes: bytearray advice differs, not a str rule
    label = "é✓"; s2 = ""; s2 += label   # non-ASCII columns, str concat
    out = [(  {"k": i}  )]       # parenthesised, not an append
    rows = []
    rows.append(({"k": i}))      # parenthesised dict append
    rows.append(  # comment inside call
        (i, i))

def uses_attr_only(h):
    for x in h.numbers:           # iterates the param -> data loop
        pass

def shadow(numbers):
    for x in Holder.numbers:      # attribute named like the param: NOT a data loop
        pass

def nested_io(items):
    for it in items:
        helper(it)

def helper(v):
    with open("f") as fh:         # I/O reached through a same-file call
        fh.write(str(v))

with cf.ThreadPoolExecutor(4) as pool:
    pool.submit(uses_attr_only, Holder())   # flagged
    pool.submit(shadow, [1])                # not flagged
    pool.submit(nested_io, [1])             # not flagged: I/O via helper
    pool.submit(lambda: None)               # unknown callable: not flagged

df = rc("x.csv")                 # aliased from-import loader
df2 = pd.read_json("x.json")

@functools.lru_cache(None)
def f(x):
    return x

class Svc:
    @functools.cache
    def m(self):
        return 1
    @staticmethod
    @functools.cache
    def s(x):
        return x

async def main():
    import subprocess
    subprocess.run(["ls"])       # blocking in async
    def inner():
        subprocess.run(["ls"])   # sync nested def: fine
    names = ["a"]
    while True:
        if "a" not in names:     # list membership in loop
            break
