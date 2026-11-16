"""Leaks held by different kinds of owners, to test holder naming."""
import time

AUDIT = []                                  # global list
REGISTRY = {}                               # global dict of bytearrays (gc-untracked!)

class Service:
    def __init__(self):
        self.history = []                   # instance attribute
        self.index = {}                     # instance dict (gc-untracked)

    def handle(self, i):
        self.history.append(bytearray(1_500_000))   # L13: leaks via Service.history
        self.index[i] = bytearray(1_200_000)        # L14: leaks via Service.index
        AUDIT.append(bytearray(1_000_000))          # L15: leaks via global AUDIT
        REGISTRY[i] = bytearray(800_000)            # L16: leaks via global REGISTRY
        scratch = bytearray(5_000_000); time.sleep(0.12)   # L17: freed every call, not a leak
        return len(scratch)

svc = Service()
for i in range(30):
    svc.handle(i)
