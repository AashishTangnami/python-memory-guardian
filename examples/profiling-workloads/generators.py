"""Lists versus generators, including a million-record ingestion/ETL workload.

Standard library only; Python 3.10+. FakeAPI simulates a JSON API transport
locally: no HTTP server, credentials, network access, or third-party packages.

    python generators.py --records 2000000 --mode streaming
    python generators.py --records 100000 --mode both --trace-memory
    python generators.py --records 2000000 --columns 12
    python generators.py --records 10000 --mode both --basics

Raw records have 2..12 fields (mixed by default). Pages are persisted as JSONL,
then read, validated, normalized to a fixed 12-field schema, serialized, and
compressed into .jsonl.gz shards. schema.json documents the output contract.
Each run uses a new directory beneath --output, so existing runs are preserved.

Generators bound retained data; several generator frames, the current API page,
JSON decoding temporaries, and I/O/compression buffers can still coexist.
Streaming here is O(page_size + row_size + buffers), not literally one object.
The eager variants intentionally retain whole-dataset Python containers.
--trace-memory measures traced Python allocation peaks, not total process RSS.

Python Memory Guardian: run Profile Current File and enter arguments such as
"--records 200000 --mode both"; the defaults process 2,000,000 records with
one variant. Compare ingest_eager/process_eager with their streaming
counterparts in the report's Phases table, then Focus stacks on a phase.
Precise mode traces every allocation and is roughly 10x slower here; with the
cursor in process_eager, "precise, only while process_eager() runs" traces
that function alone (Python 3.12+). Do not pass --trace-memory in precise
mode: stopping tracemalloc ends the profiler's precise evidence. Static
warnings flag eager_total and process_eager, not ingest_eager, whose list is
sliced and measured with len().
"""

import argparse
import gc
import gzip
import hashlib
import itertools
import json
import os
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

N = 2_000_000


def make_log(path, lines=400_000):
    with open(path, "w", encoding="utf-8") as f:
        for i in range(lines):
            f.write(f"{i},user{i % 1000},{i * 7 % 997}\n")


def eager_total(n):
    values = [i * i for i in range(n)]
    selected = [x for x in values if x % 2 == 0]
    return sum(selected)


def squares(n):
    for i in range(n):
        yield i * i


def evens(values):
    for x in values:
        if x % 2 == 0:
            yield x


def streaming_total(n):
    return sum(evens(squares(n)))


def eager_log_sum(path):
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    return sum(int(line.rsplit(",", 1)[1]) for line in lines)


def read_amounts(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            yield int(line.rsplit(",", 1)[1])


def streaming_log_sum(path):
    return sum(read_amounts(path))


def report_by_concat(n):
    out = ""
    for i in range(n):
        out += f"row {i}\n"  # CPython can optimize this; quadratic cost isn't guaranteed.
    return len(out)


def report_by_join(n):
    # join consumes this generator into an internal sequence; it isn't streaming.
    return len("".join(f"row {i}\n" for i in range(n)))


def report_by_file(n, path):
    with open(path, "wb") as f:
        for i in range(n):
            f.write(f"row {i}\n".encode("utf-8"))
    return Path(path).stat().st_size


INPUT_FIELDS = (
    "id", "amount", "user_id", "created_at", "status", "currency",
    "quantity", "product", "country", "active", "tags", "metadata",
)
OUTPUT_FIELDS = (
    "id", "amount_cents", "user_id", "created_at", "status", "currency",
    "quantity", "product", "country", "active", "tags", "metadata",
)
SCHEMA = {
    "version": 1,
    "format": "jsonl",
    "compression": "gzip",
    "fields": {
        "id": {"type": "integer", "required": True},
        "amount_cents": {"type": "integer", "required": True},
        "user_id": {"type": "integer", "nullable": True},
        "created_at": {"type": "string", "nullable": True, "format": "UTC ISO-8601"},
        "status": {"type": "string", "default": "completed"},
        "currency": {"type": "string", "default": "USD"},
        "quantity": {"type": "integer", "default": 1},
        "product": {"type": "string", "nullable": True},
        "country": {"type": "string", "nullable": True},
        "active": {"type": "boolean", "default": True},
        "tags": {"type": "array[string]", "default": []},
        "metadata": {"type": "object", "default": {}},
    },
    "processing": "Cancelled records are excluded; all other records are normalized.",
}
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def json_line(value):
    return json_bytes(value) + b"\n"


class FakeAPI:
    """Deterministic paginated API; numeric fields arrive as JSON strings."""

    def __init__(self, records, page_size, columns="mixed"):
        self.records = records
        self.page_size = page_size
        self.columns = columns

    def make_record(self, i):
        width = 2 + i % 11 if self.columns == "mixed" else int(self.columns)
        cents = (i * 37) % 100_000
        # Build only the requested fields; values are deterministic across runs.
        row = {"id": str(i), "amount": f"{cents // 100}.{cents % 100:02d}"}
        if width >= 3:
            row["user_id"] = str(i % 100_000)
        if width >= 4:
            row["created_at"] = (BASE_TIME + timedelta(seconds=i)).isoformat()
        if width >= 5:
            row["status"] = "cancelled" if i % 7 == 0 else "completed"
        if width >= 6:
            row["currency"] = "usd"
        if width >= 7:
            row["quantity"] = str(1 + i % 5)
        if width >= 8:
            row["product"] = f"product-{i % 500}"
        if width >= 9:
            row["country"] = ("ca", "np", "us")[i % 3]
        if width >= 10:
            row["active"] = "false" if i % 4 == 0 else "true"
        if width >= 11:
            row["tags"] = ["api", f"cohort-{i % 10}"]
        if width >= 12:
            row["metadata"] = {"source": "fake-api", "campaign": i % 20}
        return row

    def fetch_page(self, cursor=0):
        """Return a response body like an HTTP client would receive."""
        stop = min(cursor + self.page_size, self.records)
        return json_bytes({
            "records": [self.make_record(i) for i in range(cursor, stop)],
            "next_cursor": stop if stop < self.records else None,
        })


def api_pages(api):
    cursor = 0
    while cursor is not None:
        response = json.loads(api.fetch_page(cursor))
        yield response["records"]
        cursor = response["next_cursor"]


def write_raw_page(folder, index, rows):
    with (folder / f"page-{index:08d}.jsonl").open("wb") as f:
        for row in rows:
            f.write(json_line(row))


def ingest_eager(api, folder):
    # Whole-dataset retention plus bounded API response/decoder temporaries.
    all_records = [row for page in api_pages(api) for row in page]
    for index, start in enumerate(range(0, len(all_records), api.page_size)):
        write_raw_page(folder, index, all_records[start:start + api.page_size])
    return len(all_records)


def ingest_streaming(api, folder):
    count = 0
    for index, page in enumerate(api_pages(api)):
        write_raw_page(folder, index, page)
        count += len(page)
    return count


def iter_raw_records(folder):
    # File names are sorted for repeatable order. Only path metadata accumulates.
    for path in sorted(folder.glob("page-*.jsonl")):
        with path.open("rb") as f:
            for number, line in enumerate(f, 1):
                try:
                    yield json.loads(line)
                except ValueError as exc:
                    raise ValueError(f"Invalid JSON in {path.name}:{number}") from exc


def normalize(row):
    """Explicit schema conversion, defaults, and validation; no schema dependency."""
    if not isinstance(row, dict) or not {"id", "amount"} <= row.keys():
        raise ValueError("Record must contain id and amount")
    unknown = row.keys() - set(INPUT_FIELDS)
    if unknown:
        raise ValueError(f"Unknown fields: {sorted(unknown)}")
    amount = Decimal(str(row["amount"])) * 100
    if not amount.is_finite() or amount != amount.to_integral_value() or amount < 0:
        raise ValueError("amount must be nonnegative with at most two decimal places")
    record_id = int(row["id"])
    quantity = int(row.get("quantity", 1))
    user_id = int(row["user_id"]) if row.get("user_id") is not None else None
    if record_id < 0 or quantity < 1 or (user_id is not None and user_id < 0):
        raise ValueError("Invalid id, user_id, or quantity")
    timestamp = row.get("created_at")
    if timestamp is not None:
        parsed = datetime.fromisoformat(timestamp)
        if parsed.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        timestamp = parsed.astimezone(timezone.utc).isoformat()
    status = str(row.get("status", "completed")).lower()
    if status not in {"completed", "cancelled"}:
        raise ValueError("Invalid status")
    active = row.get("active", True)
    if type(active) is not bool:
        if active not in ("true", "false"):
            raise ValueError("active must be a boolean or 'true'/'false'")
        active = active == "true"
    tags = row.get("tags", [])
    metadata = row.get("metadata", {})
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("tags must be an array of strings")
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    for name in ("product", "country", "currency"):
        if name in row and not isinstance(row[name], str):
            raise ValueError(f"{name} must be a string")
    if status == "cancelled":
        return None
    return dict(zip(OUTPUT_FIELDS, (
        record_id, int(amount), user_id, timestamp, status,
        row.get("currency", "USD").upper(), quantity, row.get("product"),
        row["country"].upper() if "country" in row else None, active, tags, metadata,
    )))


def processed_records(rows):
    for row in rows:
        normalized = normalize(row)
        if normalized is not None:
            yield normalized


def serialized_records(rows):
    for row in rows:
        yield json_line(row)


def write_compressed_shards(lines, folder, rows_per_file, eager=False):
    """Shard by row count; streaming compression never accumulates a shard."""
    iterator = iter(lines)
    count = 0
    shard = 0
    digest = hashlib.sha256()
    while True:
        first = next(iterator, None)
        if first is None:
            break
        chunk = itertools.chain((first,), itertools.islice(iterator, rows_per_file - 1))
        destination = folder / f"part-{shard:08d}.jsonl.gz"
        if eager:
            # Per-shard copies live alongside the whole-dataset serialized list.
            batch = list(chunk)
            payload = b"".join(batch)
            digest.update(payload)
            count += len(batch)
            destination.write_bytes(gzip.compress(payload, compresslevel=6, mtime=0))
        else:
            with destination.open("wb") as output:
                with gzip.GzipFile(filename="", fileobj=output, mode="wb",
                                   compresslevel=6, mtime=0) as compressor:
                    for line in chunk:
                        compressor.write(line)
                        digest.update(line)
                        count += 1
        shard += 1
    return {"records": count, "files": shard, "sha256": digest.hexdigest()}


def process_eager(raw_folder, output_folder, rows_per_file):
    # All raw dicts, normalized dicts, and serialized row bytes remain reachable.
    raw_records = list(iter_raw_records(raw_folder))
    normalized = list(processed_records(raw_records))
    serialized = [json_line(row) for row in normalized]
    return write_compressed_shards(serialized, output_folder, rows_per_file, eager=True)


def process_streaming(raw_folder, output_folder, rows_per_file):
    rows = processed_records(iter_raw_records(raw_folder))
    return write_compressed_shards(serialized_records(rows), output_folder, rows_per_file)


def measure(label, function, *args, trace_memory=False):
    gc.collect()
    if trace_memory:
        tracemalloc.start()
    start = time.perf_counter()
    try:
        result = function(*args)
        seconds = time.perf_counter() - start
        peak = tracemalloc.get_traced_memory()[1] if trace_memory else None
    finally:
        if trace_memory:
            tracemalloc.stop()
    suffix = f", peak Python allocations={peak / 1024**2:.2f} MiB" if peak is not None else ""
    print(f"{label}: {seconds:.2f}s{suffix}", flush=True)
    return result, {"seconds": seconds, "peak_python_bytes": peak}


def verify_output(folder):
    """Read actual compressed files back, validating ordering, rows and checksum."""
    digest = hashlib.sha256()
    count = total = 0
    last_id = -1
    paths = sorted(folder.glob("part-*.jsonl.gz"))
    for path in paths:
        with gzip.open(path, "rb") as f:
            for line in f:
                digest.update(line)
                row = json.loads(line)
                if tuple(row) != OUTPUT_FIELDS:
                    raise ValueError(f"Schema mismatch in {path}")
                if type(row["id"]) is not int or row["id"] <= last_id:
                    raise ValueError("IDs must be strictly increasing")
                if type(row["amount_cents"]) is not int or row["status"] != "completed":
                    raise ValueError("Invalid processed record")
                last_id = row["id"]
                count += 1
                total += row["amount_cents"]
    return {"records": count, "files": len(paths), "sha256": digest.hexdigest(),
            "total_amount_cents": total}


def run_pipeline(mode, api, folder, rows_per_file, trace_memory):
    raw = folder / "raw"
    curated = folder / "curated"
    raw.mkdir(parents=True)
    curated.mkdir()
    (curated / "schema.json").write_text(json.dumps(SCHEMA, indent=2) + "\n", encoding="utf-8")
    ingest = ingest_eager if mode == "eager" else ingest_streaming
    process = process_eager if mode == "eager" else process_streaming
    ingested, ingest_metrics = measure(ingest.__name__, ingest, api, raw, trace_memory=trace_memory)
    written, process_metrics = measure(process.__name__, process, raw, curated, rows_per_file,
                                       trace_memory=trace_memory)
    verified, verification_metrics = measure("verify_output", verify_output, curated,
                                            trace_memory=trace_memory)
    if ingested != api.records or any(verified[key] != value for key, value in written.items()):
        raise RuntimeError("Written output does not match ingested/processed data")
    manifest = {"mode": mode, "input_records": ingested, "input_columns": api.columns,
                "page_size": api.page_size, "rows_per_file": rows_per_file,
                "output": verified, "metrics": {"ingest": ingest_metrics,
                "process": process_metrics, "verification": verification_metrics}}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{mode}: {ingested:,} input -> {verified['records']:,} output records, "
          f"{verified['files']} gzip files", flush=True)
    return verified


def run_basics(n, trace_memory):
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "events.csv")
        make_log(log, min(n, 400_000))
        for eager, streaming, arg in ((eager_total, streaming_total, n),
                                      (eager_log_sum, streaming_log_sum, log)):
            a, _ = measure(eager.__name__, eager, arg, trace_memory=trace_memory)
            b, _ = measure(streaming.__name__, streaming, arg, trace_memory=trace_memory)
            if a != b:
                raise RuntimeError("Basic examples differ")
        rows = min(n, 200_000)
        a, _ = measure("report_by_concat", report_by_concat, rows, trace_memory=trace_memory)
        b, _ = measure("report_by_join", report_by_join, rows, trace_memory=trace_memory)
        c, _ = measure("report_by_file", report_by_file, rows, os.path.join(tmp, "report.txt"),
                       trace_memory=trace_memory)
        if a != b or b != c:
            raise RuntimeError("Report examples differ")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--records", type=int, default=N)
    parser.add_argument("--page-size", type=int, default=10_000)
    parser.add_argument("--rows-per-file", type=int, default=50_000)
    parser.add_argument("--columns", choices=["mixed"] + [str(n) for n in range(2, 13)], default="mixed")
    parser.add_argument("--mode", choices=("streaming", "eager", "both"), default="streaming")
    parser.add_argument("--output", type=Path, default=Path("pipeline_output"))
    parser.add_argument("--trace-memory", action="store_true", help="Measure Python allocation peaks; adds overhead")
    parser.add_argument("--basics", action="store_true", help="Also run the original list/generator examples")
    args = parser.parse_args()
    if args.records < 0 or args.page_size < 1 or args.rows_per_file < 1:
        parser.error("records must be >= 0; page-size and rows-per-file must be >= 1")
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=args.output)).resolve()
    print(f"Output: {root}", flush=True)
    if args.basics:
        run_basics(args.records, args.trace_memory)
    api = FakeAPI(args.records, args.page_size, args.columns)
    modes = ("eager", "streaming") if args.mode == "both" else (args.mode,)
    results = [run_pipeline(mode, api, root / mode, args.rows_per_file, args.trace_memory)
               for mode in modes]
    if len(results) == 2:
        if results[0] != results[1]:
            raise RuntimeError("Eager and streaming output differ")
        print("Verified: eager and streaming decompressed outputs match exactly.", flush=True)


if __name__ == "__main__":
    main()
