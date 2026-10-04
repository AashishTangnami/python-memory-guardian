"""Python row objects versus Arrow tables versus bounded Arrow batches.

Install: python -m pip install 'pyarrow>=20,<26'
Tested with PyArrow 25.0.1 and Python 3.12; Python 3.10+ required.

    python pyarrow_example.py --records 2000000 --mode streaming
    python pyarrow_example.py --records 100000 --mode all --basics
    python pyarrow_example.py --records 2000000 --columns 12 --mode all
    python pyarrow_example.py --records 100000 --mode all --trace-memory
    python pyarrow_example.py --records 100000 --mode all --in-process

Fake paginated JSON API -> raw JSONL folder -> explicit Arrow schema -> native
filtering and revenue calculations -> Zstd-compressed Parquet output folder.
Input records have 2..12 fields; output has 12 normalized fields plus derived
net_amount_cents. Missing optional fields get explicit defaults. Cancelled
records are excluded. Money uses integer cents, discounts integer basis points.
No HTTP server, credentials, Polars, pandas, or NumPy APIs are required.

Variants:
  python:    retain raw/processed row dicts; Table.from_pylist; write_table.
  eager:     read_json into full Arrow tables; compute kernels; write_table.
  streaming: open_json into batches; compute kernels; ParquetWriter writes.

Arrow buffers are native, but a column can have multiple chunks and each array
can have value, validity, and offset buffers. Converting Python iterables into
Arrow still creates Python values/temporaries. The streaming JSON path avoids
Python row dicts during processing; generating fake API responses does not.

Timed variants run in separate processes by default. Linux VmHWM reports peak
RSS for the current executable; other Unix platforms use ru_maxrss, which may
include inherited pre-exec peaks. Windows RSS is reported as unknown. RSS
includes imports, allocator retention, Python objects, and native allocations.
Arrow pool peak covers allocations tracked by that pool, not all native memory.
--trace-memory additionally measures Python allocations; most Arrow buffers
are not traced. These independent peaks must not be added/subtracted to derive
an accurate 'untraced' value. Allocation attribution depends on your profiler.
--in-process makes function labels visible to profilers that do not follow
subprocesses; printed RSS and Arrow pool peaks are then cumulative for the run.

Python Memory Guardian does not follow subprocesses: run Profile Current File
with arguments such as "--records 100000 --mode all --in-process" and fast
mode, which sees Arrow's native buffers in process RSS. In precise mode they
appear as "native ≈" on the lines that create them. Do not pass --trace-memory
in precise mode: stopping tracemalloc ends the profiler's precise evidence.
Fixture generation and output verification are outside processing timings.
"""

import argparse
import gc
import json
import math
import subprocess
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.json as paj
import pyarrow.parquet as pq

ROWS = 2_000_000
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
INPUT_FIELDS = (
    "id", "amount", "region", "quantity", "status", "user_id", "country",
    "discount_bps", "created_at", "product", "note", "source",
)
RAW_SCHEMA = pa.schema([(name, pa.string()) for name in INPUT_FIELDS])
OUTPUT_SCHEMA = pa.schema([
    ("id", pa.int64()), ("amount_cents", pa.int64()), ("region", pa.string()),
    ("quantity", pa.int64()), ("status", pa.string()), ("user_id", pa.int64()),
    ("country", pa.string()), ("discount_bps", pa.int64()),
    ("created_at", pa.timestamp("us", "UTC")), ("product", pa.string()),
    ("note", pa.string()), ("source", pa.string()), ("net_amount_cents", pa.int64()),
], metadata={b"schema_version": b"1"})
SUMMARY_SCHEMA = pa.schema([
    ("region", pa.string()), ("records", pa.int64()), ("revenue_cents", pa.int64()),
])
DEFAULTS = {"region": "unknown", "quantity": "1", "status": "completed",
            "country": "unknown", "discount_bps": "0", "source": "fake-api"}
PARSE_OPTIONS = paj.ParseOptions(explicit_schema=RAW_SCHEMA, unexpected_field_behavior="error")


def rows_as_dicts(n):
    rows = []
    for i in range(n):
        rows.append({"id": i, "region": f"r{i % 50}", "amount": i % 977 * 0.5})
    return rows


def total_by_region_python(rows):
    totals = {}
    for row in rows:
        totals[row["region"]] = totals.get(row["region"], 0.0) + row["amount"]
    return totals


def table_as_columns(n, start=0):
    # These iterables create Python values before Arrow builds native arrays.
    return pa.table({
        "id": pa.array(range(start, start + n), pa.int64()),
        "region": pa.array((f"r{i % 50}" for i in range(start, start + n)), pa.string()),
        "amount": pa.array((i % 977 * 0.5 for i in range(start, start + n)), pa.float64()),
    })


def total_by_region_arrow(table):
    grouped = table.group_by("region").aggregate([("amount", "sum")])
    # Convert only the small grouped result (at most 50 rows) to Python.
    return dict(zip(grouped["region"].to_pylist(), grouped["amount_sum"].to_pylist()))


def read_back_as_python(path):
    return pq.read_table(path).to_pylist()  # All Python row dicts are recreated.


def read_back_columns(path):
    return pq.read_table(path, columns=["region", "amount"])


def read_back_batches(path, batch_size):
    totals = {}
    count = 0
    with pq.ParquetFile(path) as source:
        for batch in source.iter_batches(batch_size=batch_size, columns=["region", "amount"]):
            count += batch.num_rows
            partial = total_by_region_arrow(pa.Table.from_batches([batch]))
            for region, amount in partial.items():
                totals[region] = totals.get(region, 0.0) + amount
    return {"records": count, "totals": totals}


class FakeAPI:
    """Deterministic local transport; all scalar fields arrive as strings."""

    def __init__(self, records, page_size, columns="mixed"):
        self.records, self.page_size, self.columns = records, page_size, columns

    def make_record(self, i):
        width = 2 + i % 11 if self.columns == "mixed" else int(self.columns)
        cents = i * 37 % 100_000
        row = {"id": str(i), "amount": f"{cents // 100}.{cents % 100:02d}"}
        if width >= 3:
            row["region"] = f"r{i % 50}"
        if width >= 4:
            row["quantity"] = str(1 + i % 5)
        if width >= 5:
            row["status"] = "cancelled" if i % 7 == 0 else "completed"
        if width >= 6:
            row["user_id"] = str(i % 100_000)
        if width >= 7:
            row["country"] = ("ca", "np", "us")[i % 3]
        if width >= 8:
            row["discount_bps"] = str((i % 6) * 100)
        if width >= 9:
            row["created_at"] = (BASE_TIME + timedelta(seconds=i)).isoformat()
        if width >= 10:
            row["product"] = f"product-{i % 500}"
        if width >= 11:
            row["note"] = f"API event {i}: " + "unused summary detail; " * 4
        if width >= 12:
            row["source"] = "fake-api"
        return row

    def fetch_page(self, cursor):
        stop = min(cursor + self.page_size, self.records)
        return json.dumps({"records": [self.make_record(i) for i in range(cursor, stop)],
                           "next_cursor": stop if stop < self.records else None},
                          separators=(",", ":")).encode("utf-8")


def ingest_api(api, folder):
    """Retain one bounded API page plus response decoding/serialization buffers."""
    count = index = 0
    cursor = 0
    while cursor is not None and count < api.records:
        response = json.loads(api.fetch_page(cursor))
        with (folder / f"page-{index:08d}.jsonl").open("w", encoding="utf-8") as f:
            for row in response["records"]:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
                count += 1
        cursor = response["next_cursor"]
        index += 1
    return count


def raw_paths(folder):
    return sorted(folder.glob("page-*.jsonl"))


def iter_python_rows(folder):
    for path in raw_paths(folder):
        with path.open(encoding="utf-8") as source:
            for line in source:
                yield json.loads(line)


def normalize_python(row):
    if row.get("status", "completed") != "completed":
        return None
    cents = int(Decimal(row["amount"]) * 100)
    quantity = int(row.get("quantity", "1"))
    discount = int(row.get("discount_bps", "0"))
    return {
        "id": int(row["id"]), "amount_cents": cents,
        "region": row.get("region", "unknown"), "quantity": quantity,
        "status": "completed", "user_id": int(row["user_id"]) if "user_id" in row else None,
        "country": row.get("country", "unknown").upper(), "discount_bps": discount,
        "created_at": datetime.fromisoformat(row["created_at"]) if "created_at" in row else None,
        "product": row.get("product"), "note": row.get("note"), "source": row.get("source", "fake-api"),
        "net_amount_cents": cents * quantity * (10_000 - discount) // 10_000,
    }


def normalize_arrow(raw):
    """Schema conversion via native kernels; raw may be Table or RecordBatch.

    The fixture guarantees valid enums and nonnegative two-decimal amounts.
    Missing optional fields are null after JSON parsing. Casts are safe and
    arithmetic is checked for overflow. This is not a general JSON validator.
    """
    status = pc.fill_null(raw["status"], "completed")
    raw = raw.filter(pc.equal(status, "completed"))
    cents = pc.cast(pc.multiply_checked(pc.cast(raw["amount"], pa.decimal128(18, 2)),
                                       pa.scalar(100, pa.int64())), pa.int64())
    quantity = pc.cast(pc.fill_null(raw["quantity"], "1"), pa.int64())
    discount = pc.cast(pc.fill_null(raw["discount_bps"], "0"), pa.int64())
    gross = pc.multiply_checked(cents, quantity)
    numerator = pc.multiply_checked(gross, pc.subtract_checked(pa.scalar(10_000, pa.int64()), discount))
    # Integer division truncates; with this nonnegative fixture it equals floor.
    net = pc.divide_checked(numerator, pa.scalar(10_000, pa.int64()))
    return pa.Table.from_arrays([
        pc.cast(raw["id"], pa.int64()), cents, pc.fill_null(raw["region"], "unknown"),
        quantity, pc.fill_null(raw["status"], "completed"), pc.cast(raw["user_id"], pa.int64()),
        pc.utf8_upper(pc.fill_null(raw["country"], "unknown")), discount,
        pc.cast(raw["created_at"], pa.timestamp("us", "UTC")), raw["product"], raw["note"],
        pc.fill_null(raw["source"], "fake-api"), net,
    ], schema=OUTPUT_SCHEMA)


def python_pipeline(raw_folder, output_folder, args):
    raw_rows = list(iter_python_rows(raw_folder))
    processed = [result for row in raw_rows if (result := normalize_python(row)) is not None]
    # Row dicts remain alive while the native table is built and serialized.
    table = pa.Table.from_pylist(processed, schema=OUTPUT_SCHEMA)
    pq.write_table(table, output_folder / "records.parquet", compression="zstd",
                   row_group_size=args.batch_size)


def eager_arrow_pipeline(raw_folder, output_folder, args):
    tables = [paj.read_json(path, parse_options=PARSE_OPTIONS,
                           read_options=paj.ReadOptions(block_size=args.json_block_size))
              for path in raw_paths(raw_folder)]
    raw = pa.concat_tables(tables) if tables else pa.Table.from_batches([], schema=RAW_SCHEMA)
    table = normalize_arrow(raw)
    pq.write_table(table, output_folder / "records.parquet", compression="zstd",
                   row_group_size=args.batch_size)


def streaming_arrow_pipeline(raw_folder, output_folder, args):
    # No Python per-row loop, full-data read_all(), to_pylist(), or concat_tables().
    with pq.ParquetWriter(output_folder / "records.parquet", OUTPUT_SCHEMA,
                          compression="zstd") as writer:
        for path in raw_paths(raw_folder):
            with paj.open_json(path, parse_options=PARSE_OPTIONS,
                               read_options=paj.ReadOptions(block_size=args.json_block_size)) as reader:
                for raw_batch in reader:
                    table = normalize_arrow(raw_batch)
                    writer.write_table(table, row_group_size=args.batch_size)
    # open_json is single-threaded. Compute kernels and Parquet encoding can
    # allocate additional native buffers. This is bounded batching, not zero-copy.


PIPELINES = {"python": python_pipeline, "eager": eager_arrow_pipeline, "streaming": streaming_arrow_pipeline}
BASIC_TASKS = ("build_python", "build_arrow", "read_python", "read_columns", "read_batches")


def basic_task(name, root, args):
    if name == "build_python":
        rows = rows_as_dicts(args.records)
        return {"records": len(rows), "totals": total_by_region_python(rows)}
    if name == "build_arrow":
        table = table_as_columns(args.records)
        return {"records": table.num_rows, "totals": total_by_region_arrow(table)}
    path = root / "sales.parquet"
    if name == "read_python":
        rows = read_back_as_python(path)
        return {"records": len(rows), "totals": total_by_region_python(rows)}
    if name == "read_columns":
        table = read_back_columns(path)
        return {"records": table.num_rows, "totals": total_by_region_arrow(table)}
    return read_back_batches(path, args.batch_size)


def peak_rss_bytes():
    if sys.platform.startswith("linux"):
        # ru_maxrss can retain the parent's pre-exec RSS high-water mark.
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak if sys.platform == "darwin" else peak * 1024)
    except ImportError:
        return None


def worker(args):
    pa.set_cpu_count(args.threads)
    pa.set_io_thread_count(args.threads)
    name = args.mode if args.worker == "pipeline" else f"basic-{args.basic_task}"
    output = args.run_dir / name
    output.mkdir()
    gc.collect()
    if args.trace_memory:
        tracemalloc.start()
    start = time.perf_counter()
    result = None
    try:
        if args.worker == "pipeline":
            PIPELINES[args.mode](args.run_dir / "raw", output, args)
        else:
            result = basic_task(args.basic_task, args.run_dir, args)
        seconds = time.perf_counter() - start
        rss = peak_rss_bytes()
        python_peak = tracemalloc.get_traced_memory()[1] if args.trace_memory else None
        arrow_peak = pa.default_memory_pool().max_memory()
    finally:
        if args.trace_memory:
            tracemalloc.stop()
    metrics = {"job": args.worker, "variant": name, "seconds": seconds,
               "peak_process_rss_bytes": rss, "peak_python_bytes": python_peak,
               "peak_arrow_pool_bytes": arrow_peak, "arrow_version": pa.__version__,
               "threads": pa.cpu_count(), "execution": "in-process" if args.in_process else "fresh-worker"}
    if result is not None:
        metrics["result"] = result
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")


def benchmark(args, root, job, variant):
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", job,
               "--run-dir", str(root), "--records", str(args.records),
               "--batch-size", str(args.batch_size), "--json-block-size", str(args.json_block_size),
               "--threads", str(args.threads)]
    command.extend(["--mode", variant] if job == "pipeline" else ["--basic-task", variant])
    if args.trace_memory:
        command.append("--trace-memory")
    if args.in_process:
        child = argparse.Namespace(**vars(args))
        child.worker, child.run_dir = job, root
        if job == "pipeline":
            child.mode = variant
        else:
            child.basic_task = variant
        worker(child)
    else:
        subprocess.run(command, check=True)
    folder = root / (variant if job == "pipeline" else f"basic-{variant}")
    measured = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
    def mib(value):
        return f"{value / 1024**2:.2f} MiB" if value is not None else "unknown"
    note = " (cumulative run peaks)" if args.in_process else ""
    text = (f"{job}/{variant}: {measured['seconds']:.2f}s, RSS={mib(measured['peak_process_rss_bytes'])}, "
            f"Arrow pool={mib(measured['peak_arrow_pool_bytes'])}")
    if args.trace_memory:
        text += f", Python traced={mib(measured['peak_python_bytes'])}"
    print(text + note, flush=True)
    return measured


def inspect_output(path, batch_size):
    count = total = 0
    last_id = -1
    groups = {}
    with pq.ParquetFile(path) as source:
        if not source.schema_arrow.equals(OUTPUT_SCHEMA, check_metadata=True):
            raise RuntimeError(f"Schema mismatch: {path}")
        for batch in source.iter_batches(batch_size=batch_size):
            if not batch.num_rows:
                continue
            ids = batch["id"]
            if ids.null_count or batch["amount_cents"].null_count or batch["net_amount_cents"].null_count:
                raise RuntimeError("Null required field")
            if ids[0].as_py() <= last_id or (len(ids) > 1 and not pc.all(
                    pc.greater(ids.slice(1), ids.slice(0, len(ids) - 1))).as_py()):
                raise RuntimeError("IDs must be strictly increasing")
            if batch["status"].null_count or not pc.all(pc.equal(batch["status"], "completed")).as_py():
                raise RuntimeError("Unexpected status")
            last_id = ids[-1].as_py()
            count += batch.num_rows
            total += pc.sum(batch["net_amount_cents"]).as_py()
            grouped = pa.Table.from_batches([batch]).select(["region", "id", "net_amount_cents"]).group_by(
                "region").aggregate([("id", "count"), ("net_amount_cents", "sum")])
            for region, records, revenue in zip(grouped["region"].to_pylist(),
                                                grouped["id_count"].to_pylist(),
                                                grouped["net_amount_cents_sum"].to_pylist()):
                value = groups.setdefault(region, {"records": 0, "revenue_cents": 0})
                value["records"] += records
                value["revenue_cents"] += revenue
        if count != source.metadata.num_rows:
            raise RuntimeError("Parquet row count mismatch")
    summary = pa.Table.from_pylist([dict(region=k, **v) for k, v in sorted(groups.items())], schema=SUMMARY_SCHEMA)
    pq.write_table(summary, path.parent / "summary.parquet", compression="zstd")
    return {"records": count, "revenue_cents": total, "groups": groups}


def compare_outputs(left, right, batch_size):
    """Exact typed comparison, independent of Parquet row-group boundaries."""
    with pq.ParquetFile(left) as a, pq.ParquetFile(right) as b:
        streams = [iter(a.iter_batches(batch_size=batch_size)), iter(b.iter_batches(batch_size=batch_size))]
        batches = [next(stream, None) for stream in streams]
        offsets = [0, 0]
        while all(batch is not None for batch in batches):
            length = min(batch.num_rows - offset for batch, offset in zip(batches, offsets))
            if not batches[0].slice(offsets[0], length).equals(batches[1].slice(offsets[1], length)):
                raise RuntimeError("Parquet output rows differ")
            for index in range(2):
                offsets[index] += length
                if offsets[index] == batches[index].num_rows:
                    batches[index] = next(streams[index], None)
                    offsets[index] = 0
        if any(batch is not None for batch in batches):
            raise RuntimeError("Parquet output lengths differ")


def run_basics(args, root):
    schema = table_as_columns(0).schema
    with pq.ParquetWriter(root / "sales.parquet", schema, compression="zstd") as writer:
        for start in range(0, args.records, args.batch_size):
            writer.write_table(table_as_columns(min(args.batch_size, args.records - start), start))
    results = [benchmark(args, root, "basic", task) for task in BASIC_TASKS]
    expected = results[0]["result"]
    for measured in results[1:]:
        actual = measured["result"]
        if actual["records"] != expected["records"] or actual["totals"].keys() != expected["totals"].keys():
            raise RuntimeError("Basic example rows/groups differ")
        if any(not math.isclose(expected["totals"][k], actual["totals"][k], rel_tol=1e-9, abs_tol=1e-9)
               for k in expected["totals"]):
            raise RuntimeError("Basic example totals differ")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--records", type=int, default=ROWS)
    parser.add_argument("--page-size", type=int, default=10_000)
    parser.add_argument("--batch-size", type=int, default=50_000, help="Maximum Parquet row group/read-back batch rows")
    parser.add_argument("--json-block-size", type=int, default=1_048_576, help="JSON reader block bytes; individual records must fit")
    parser.add_argument("--columns", choices=["mixed"] + [str(i) for i in range(2, 13)], default="mixed")
    parser.add_argument("--mode", choices=("python", "eager", "streaming", "all"), default="streaming")
    parser.add_argument("--output", type=Path, default=Path("pyarrow_output"))
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--trace-memory", action="store_true")
    parser.add_argument("--in-process", action="store_true", help="For editor profiling; reported RSS/pool peaks become cumulative")
    parser.add_argument("--basics", action="store_true", help="Also compare original construction/read-back examples")
    parser.add_argument("--worker", choices=("pipeline", "basic"), help=argparse.SUPPRESS)
    parser.add_argument("--basic-task", choices=BASIC_TASKS, help=argparse.SUPPRESS)
    parser.add_argument("--run-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.records < 0 or min(args.page_size, args.batch_size, args.json_block_size, args.threads) < 1:
        parser.error("records must be >= 0; all sizes and threads must be >= 1")
    if args.worker:
        if args.run_dir is None or args.mode == "all" or (args.worker == "basic" and args.basic_task is None):
            parser.error("invalid worker arguments")
        worker(args)
        return
    pa.set_cpu_count(args.threads)
    pa.set_io_thread_count(args.threads)
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=args.output)).resolve()
    raw = root / "raw"
    raw.mkdir()
    print(f"PyArrow {pa.__version__}; output: {root}", flush=True)
    start = time.perf_counter()
    ingested = ingest_api(FakeAPI(args.records, args.page_size, args.columns), raw)
    if ingested != args.records:
        raise RuntimeError("Ingestion count mismatch")
    print(f"Ingested {ingested:,} records in {time.perf_counter() - start:.2f}s", flush=True)
    modes = ("python", "eager", "streaming") if args.mode == "all" else (args.mode,)
    metrics, stats = [], []
    for mode in modes:
        metrics.append(benchmark(args, root, "pipeline", mode))
        stats.append(inspect_output(root / mode / "records.parquet", args.batch_size))
        contract = {"schema": [{"name": f.name, "type": str(f.type)} for f in OUTPUT_SCHEMA],
                    "defaults": DEFAULTS, "nullable": ["user_id", "created_at", "product", "note"],
                    "filter": "status == completed", "rounding": "net cents truncated downward"}
        (root / mode / "schema.json").write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
        print(f"{mode}: verified {stats[-1]['records']:,} output rows", flush=True)
    for index in range(1, len(modes)):
        if stats[index] != stats[0]:
            raise RuntimeError("Pipeline counts, totals, or grouped summaries differ")
        compare_outputs(root / modes[0] / "records.parquet", root / modes[index] / "records.parquet", args.batch_size)
    if args.basics:
        metrics.extend(run_basics(args, root))
    manifest = {"input_records": args.records, "input_columns": args.columns, "page_size": args.page_size,
                "batch_size": args.batch_size, "json_block_size": args.json_block_size,
                "output": stats[0], "benchmarks": metrics,
                "memory_note": "RSS, Arrow pool and Python tracing measure different things; in-process RSS/pool peaks are cumulative"}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Verified: requested pipeline outputs and grouped totals match.", flush=True)


if __name__ == "__main__":
    main()
