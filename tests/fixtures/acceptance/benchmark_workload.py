"""Deterministic object-heavy workload; no measurement instrumentation in baseline."""
import argparse
import json
import time


def process_records(n):
    records = [{"id": i, "group": i % 997, "value": i * 3} for i in range(n)]
    ordered = sorted(records, key=lambda row: (row["group"], row["id"]))
    checksum = 0
    for repeat in range(12):
        for row in ordered:
            checksum += (row["value"] ^ repeat) % 104729
    return len(records), checksum


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, required=True)
    args = parser.parse_args()
    started = time.perf_counter()
    count, checksum = process_records(args.size)
    print(json.dumps({"records": count, "checksum": checksum,
                      "workload_wall_s": time.perf_counter() - started}))
