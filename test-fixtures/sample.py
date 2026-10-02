import pandas as pd
from pandas import read_json
import sqlite3, weakref

df = pd.read_csv("big.csv")  # héllo ünïcode
s = "cursor.fetchall() in a string is fine"
conn = sqlite3.connect(":memory:")
cur = conn.cursor()
rows = cur.fetchall()
while True:
    r = cur.fetchone()
for row in cur.execute("select 1"):
    pass
x = cur.fetchall()  # memory-guardian: ignore

class Node:
    def __init__(self, parent=None):
        self.parent = parent
        self.next = None

class Safe:
    def __init__(self, parent):
        self.parent = weakref.ref(parent)

nodes = [Node(n) for n in range(10)]
for i in range(10):
    Safe(i)
    Node()
