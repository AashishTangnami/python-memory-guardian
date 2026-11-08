"""Steady workloads for retention diagnosis: growing history versus bounded cache."""
from collections import deque
import sys
import time


class Service:
    def __init__(self, bounded):
        self.history = deque(maxlen=2) if bounded else []

    def handle(self):
        self.history.append(bytearray(2_000_000))
        time.sleep(.14)


service = Service('--bounded' in sys.argv)
for _ in range(10):
    service.handle()
