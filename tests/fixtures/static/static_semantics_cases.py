"""Explicit static evidence and semantic compatibility regressions (not executed)."""
import builtins
import pandas as pd
from pandas import read_csv as csv

pd.read_csv("unknown.csv")
pd.read_csv("numeric.csv", dtype="int64")
csv("unknown.csv")
csv("numeric.csv", dtype={"value": "float64"})
pd.read_csv("object.csv", dtype="object")  # expect: pointer-chasing
csv("mixed.csv", dtype={"value": object})  # expect: pointer-chasing
pd.Series([1, 2], dtype=object)  # expect: pointer-chasing
pd.DataFrame({"value": [1]}, dtype=builtins.object)  # expect: pointer-chasing
pd.Series([1, 2], dtype="int64")

def database(cur):
    cur.fetchall()  # expect: heap-inflation
    for row in cur:
        consume(row)
    for row in cur.execute("select value from records"):
        consume(row)
    while True:
        row = cur.fetchone()
        if row is None:
            break
        consume(row)
    while rows := cur.fetchmany(100):
        consume(rows)

class Dynamic:
    def set_field(self, key, value):
        setattr(self, key, value)

class DictDependent:
    def fields(self):
        return self.__dict__

class Fixed:
    def __init__(self):
        self.value = 1

class Slotted:
    __slots__ = ("value",)

for i in range(3):
    Dynamic()
    DictDependent()
    Fixed()  # expect: ram-fragmentation.no-slots
    Slotted()

def shadow_object(object):
    pd.Series([1, 2], dtype=object)

values = [[1], None]
for query in ([1], None):
    query in values  # expect: single-thread-stall.list-membership
