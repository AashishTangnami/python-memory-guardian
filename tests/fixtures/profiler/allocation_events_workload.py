"""Independent witnesses, with optional allocator-event capture around the same work."""
import argparse
import ctypes
import json
import mmap
from pathlib import Path
import sys
import threading
import time
import tracemalloc


def churn(count, size, fixed):
    operations = payload = checksum = 0
    reusable = bytearray(size) if fixed else None
    if fixed:
        operations, payload = 1, len(reusable)
    for index in range(count):
        value = reusable if fixed else bytearray(size)
        if not fixed:
            operations += 1
            payload += len(value)
        value[0] = index % 251
        checksum += value[0]
    return {"allocation_operations": operations, "requested_payload_bytes": payload,
            "checksum": checksum, "operations": count}, None


def copies(count, size, fixed):
    items = [None] * (size // ctypes.sizeof(ctypes.c_void_p))
    copy_count = payload = checksum = 0
    for _ in range(count):
        value = items if fixed else items.copy()
        if not fixed:
            copy_count += 1
            payload += len(value) * ctypes.sizeof(ctypes.c_void_p)
        checksum += len(value)
        assert value[0] is None
    return {"copy_operations": copy_count, "copied_slot_bytes": payload,
            "checksum": checksum, "operations": count, "ordering_mutability_null_contract": "read-only same list values"}, None


def native(count, size, fixed):
    owners = []
    checksum = 0
    for _ in range(count):
        region = mmap.mmap(-1, size)
        for offset in range(0, size, mmap.PAGESIZE):
            region[offset] = 1
        checksum += region[0]
        if fixed:
            region.close()
        else:
            owners.append(region)
    return {"native_mapping_operations": count, "native_requested_bytes": count * size,
            "native_held_payload_bytes": sum(len(value) for value in owners),
            "checksum": checksum}, owners


def threads(count, size, fixed):
    result = []
    def allocating_worker():
        for _ in range(1 if fixed else count):
            result.append(bytearray(size))
    worker = threading.Thread(target=allocating_worker, name="allocation-worker")
    worker.start()
    # Sampling can see this busy main thread; allocator events must still identify the worker.
    while worker.is_alive():
        time.perf_counter()
    worker.join()
    return {"sole_allocating_worker": "allocation-worker", "allocation_operations": len(result),
            "requested_payload_bytes": sum(len(value) for value in result)}, result


def unhooked_native(count, size, fixed):
    libc = ctypes.CDLL(None)
    libc.malloc.argtypes = [ctypes.c_size_t]
    libc.malloc.restype = ctypes.c_void_p
    libc.free.argtypes = [ctypes.c_void_p]
    address = libc.malloc(size)
    if not address:
        raise MemoryError("independent native allocation failed")
    ctypes.memset(address, 1, size)
    libc.free(address)
    return {"direct_native_function_pointer_bytes": size, "native_malloc_and_free_observed": True}, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("churn", "copies", "native", "threads", "unhooked_native"), required=True)
    parser.add_argument("--fixed", action="store_true")
    parser.add_argument("--count", type=int, default=128)
    parser.add_argument("--size", type=int, default=65536)
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument('--max-sites', type=int, default=2048)
    args = parser.parse_args()
    recorder = None
    if args.capture:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src" / "python"))
        from pmg_profiler.allocations import AllocationRecorder
        recorder = AllocationRecorder(args.capture, Path(__file__).resolve().parent, max_sites=args.max_sites)
        recorder.start()
    tracemalloc.start()
    observation, held = globals()[args.kind](args.count, args.size, args.fixed)
    observation["python_traced_current_bytes"], observation["python_traced_peak_bytes"] = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if recorder:
        recorder.stop()
        summary = recorder.report()
        if args.summary:
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            args.summary.write_text(json.dumps(summary, indent=2) + "\n")
        observation["capture_summary"] = str(args.summary) if args.summary else summary
    print(json.dumps(observation))
    # Keep deliberate positive ownership until capture stops, then perform actual cleanup.
    for value in held or []:
        if isinstance(value, mmap.mmap):
            value.close()


if __name__ == "__main__":
    main()
