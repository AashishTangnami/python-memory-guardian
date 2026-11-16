"""Compare cycle collection with and without the profiler's frame sampler."""
import argparse
import gc
import json
import time
import weakref

arguments = argparse.ArgumentParser()
arguments.add_argument("--break-links", action="store_true")
args = arguments.parse_args()
finalized = []


class Cycle:
    def __init__(self):
        self.payload = bytearray(1_250_000)
        self.link = self

    def __del__(self):
        finalized.append(id(self))
        # A legal finalizer can release the GIL. Keep the case reproducible by
        # allowing the sampler to observe the live finalizer frame.
        time.sleep(.04)


def surviving_objects():
    # This function's locals do not persist after its return.
    objects = [value for value in gc.get_objects() if type(value) is Cycle]
    return {"survivors": len(objects), "payload_bytes": sum(len(value.payload) for value in objects)}


def execute():
    gc.collect()
    gc.disable()
    owned = [Cycle() for _ in range(6)]
    weak = [weakref.ref(value) for value in owned]
    assert len(owned) == 6
    time.sleep(.3)
    if args.break_links:
        for value in owned:
            value.link = None
        del value
    owned.clear()
    collected = gc.collect()
    time.sleep(.3)
    first = {"event": "after_expected_cleanup", "collected": collected,
             "weak_alive": sum(ref() is not None for ref in weak), "finalized": len(finalized),
             **surviving_objects()}
    print(json.dumps(first), flush=True)
    # A second collection tests whether the observer caused a temporary
    # resurrection into unreachable, already-finalized cycles.
    collected_again = gc.collect()
    time.sleep(.1)
    print(json.dumps({"event": "after_second_collection", "collected": collected_again,
                      **surviving_objects()}), flush=True)
    gc.enable()


execute()
