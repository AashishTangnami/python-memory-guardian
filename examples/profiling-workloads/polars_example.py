"""Eager vs lazy Polars, Python row loops, and a million-record API pipeline.

Install: python -m pip install 'polars>=1.35,<2'
Tested with Polars 1.44.2 and Python 3.12; Python 3.10+ required.

    python polars_example.py --records 2000000 --mode lazy
    python polars_example.py --records 100000 --mode all --basics
    python polars_example.py --records 2000000 --columns 12 --mode all
    python polars_example.py --records 100000 --mode all --trace-memory
    python polars_example.py --records 100000 --mode all --in-process

Fake paginated JSON API -> raw JSONL folder -> explicit schema -> filter and
calculate revenue -> serialize to Zstd-compressed Parquet in output folders.
Input records have 2..12 fields, mixed by default. Optional fields get defaults.
Output has 12 normalized fields plus one derived net_amount_cents field.
Amounts use integer cents; discounts use integer basis points (100 = 1%).
No network server, API credentials, pandas, NumPy, or PyArrow dependency.

Three equivalent processing variants:
  eager:  read all JSONL into a DataFrame, use expressions, write_parquet.
  lazy:   scan JSONL, use expressions, sink_parquet(engine='streaming').
  python: read all JSONL, iter_rows, normalize in Python, rebuild a DataFrame.

Each timed variant runs in a fresh subprocess. Linux VmHWM peak RSS includes
native Polars allocations, runtime/imports, and Python allocations. Other Unix
systems use ru_maxrss, which may include a pre-exec inherited high-water mark.
This is process high-water RSS, not incremental function memory. Windows reports
RSS as unknown.
Optional tracemalloc peaks cover Python only and miss most native Polars buffers.
Generation and read-back verification are outside the timed processing workers.
Use --in-process for an editor profiler that does not follow subprocesses.
Python Memory Guardian is one: run Profile Current File with arguments such as
"--records 100000 --mode all --in-process" and fast mode, which sees Polars'
native allocations in process RSS. Do not pass --trace-memory in precise mode:
stopping tracemalloc ends the profiler's precise evidence.
In this mode RSS is cumulative across generation, processing, and verification;
compare function labels in your profiler rather than the printed RSS numbers.
In-process thread count comes from POLARS_MAX_THREADS at import time.

Lazy does not mean constant memory: concurrency, batches, compression buffers,
aggregation cardinality, and non-streaming operators affect memory. collect()
still materializes the final result even with engine='streaming'; use a sink
for large outputs. CSV projection avoids materializing unused columns, but the
text still needs scanning. Native/Python time attribution depends on your profiler.
"""

import argparse
import gc
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

# Override with the environment or --threads for the measured child processes.
os.environ.setdefault("POLARS_MAX_THREADS", "4")
import polars as pl

ROWS = 2_000_000
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
INPUT_FIELDS = (
    "id", "amount", "region", "quantity", "status", "user_id", "country",
    "discount_bps", "created_at", "product", "note", "source",
)
RAW_SCHEMA = {name: pl.String for name in INPUT_FIELDS}
CSV_SCHEMA = {"id": pl.Int64, "region": pl.String, "amount": pl.Float64, "note": pl.String}
OUTPUT_SCHEMA = {
    "id": pl.Int64, "amount_cents": pl.Int64, "region": pl.String,
    "quantity": pl.Int64, "status": pl.String, "user_id": pl.Int64,
    "country": pl.String, "discount_bps": pl.Int64,
    "created_at": pl.Datetime("us", "UTC"), "product": pl.String,
    "note": pl.String, "source": pl.String, "net_amount_cents": pl.Int64,
}
DEFAULTS = {
    "region": "unknown", "quantity": "1", "status": "completed",
    "country": "unknown", "discount_bps": "0", "source": "fake-api",
}


def write_csv(path, n, page_size=10_000):
    """Original CSV fixture, generated in bounded batches outside benchmarks."""
    with Path(path).open("wb") as f:
        for start in range(0, max(n, 1), page_size):
            pl.select(pl.int_range(start, min(start + page_size, n)).alias("id")).with_columns(
                ("r" + (pl.col("id") % 50).cast(pl.String)).alias("region"),
                ((pl.col("id") % 977) * 0.5).alias("amount"),
                (pl.col("id") * 31 % 100_003).cast(pl.String).alias("note"),
            ).write_csv(f, include_header=(start == 0))


def eager_summary(path):
    df = pl.read_csv(path, schema_overrides=CSV_SCHEMA)
    return df.filter(pl.col("amount") > 100).group_by("region").agg(pl.col("amount").sum())


def lazy_summary_plan(path):
    return (pl.scan_csv(path, schema_overrides=CSV_SCHEMA).filter(pl.col("amount") > 100)
            .group_by("region").agg(pl.col("amount").sum()))


def lazy_summary(path):
    # Only the small grouped result is materialized. CSV bytes still get scanned.
    return lazy_summary_plan(path).collect(engine="streaming")


def python_loop_summary(path):
    df = pl.read_csv(path, columns=["region", "amount"], schema_overrides=CSV_SCHEMA)
    totals = {}
    for region, amount in df.iter_rows():
        if amount > 100:
            totals[region] = totals.get(region, 0.0) + amount
    return totals


def as_dict(frame):
    # Python conversion is intentional here: at most 50 summary groups.
    return dict(zip(frame["region"].to_list(), frame["amount"].to_list()))


class FakeAPI:
    """Deterministic local transport returning a paginated JSON response body."""

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


def ingest_api(api, raw_folder):
    """Page-bounded ingestion; all processing variants share the same raw files."""
    count = page_index = 0
    cursor = 0
    while cursor is not None and count < api.records:
        response = json.loads(api.fetch_page(cursor))
        with (raw_folder / f"page-{page_index:08d}.jsonl").open("w", encoding="utf-8") as f:
            for row in response["records"]:
                f.write(json.dumps(row, separators=(",", ":")) + "\n")
                count += 1
        cursor = response["next_cursor"]
        page_index += 1
    return count


def raw_paths(folder):
    return sorted(folder.glob("page-*.jsonl"))


def read_raw(folder):
    paths = raw_paths(folder)
    if not paths:
        return pl.DataFrame(schema=RAW_SCHEMA)
    # Intentionally eager: all page DataFrames exist before concatenation.
    return pl.concat([pl.read_ndjson(path, schema=RAW_SCHEMA) for path in paths])


def scan_raw(folder):
    paths = raw_paths(folder)
    if not paths:
        return pl.DataFrame(schema=RAW_SCHEMA).lazy()
    return pl.scan_ndjson(paths, schema=RAW_SCHEMA, low_memory=True)


def normalize_expressions(frame):
    """Same expression pipeline works with either DataFrame or LazyFrame.

    The fixture guarantees nonnegative, two-decimal amounts and valid enums.
    Explicit raw schema includes missing fields as nulls; defaults are deliberate.
    Casts are strict so malformed numeric or timestamp strings raise errors.
    This is a fixture transformation contract, not a general JSON validator.
    """
    normalized = frame.select(
        pl.col("id").cast(pl.Int64),
        (pl.col("amount").cast(pl.Decimal(18, 2)) * 100).cast(pl.Int64).alias("amount_cents"),
        pl.col("region").fill_null("unknown"),
        pl.col("quantity").fill_null("1").cast(pl.Int64),
        pl.col("status").fill_null("completed"),
        pl.col("user_id").cast(pl.Int64),
        pl.col("country").fill_null("unknown").str.to_uppercase(),
        pl.col("discount_bps").fill_null("0").cast(pl.Int64),
        pl.col("created_at").str.to_datetime(
            format="%Y-%m-%dT%H:%M:%S%#z", time_unit="us", time_zone="UTC", strict=True),
        pl.col("product"), pl.col("note"), pl.col("source").fill_null("fake-api"),
    )
    return (normalized.filter(pl.col("status") == "completed")
            .with_columns((pl.col("amount_cents") * pl.col("quantity")
                           * (10_000 - pl.col("discount_bps")) // 10_000)
                          .alias("net_amount_cents")))


def summary_plan(frame):
    # note/product/timestamp/etc are unused and can be projected out of a scan.
    return frame.group_by("region").agg(
        pl.len().cast(pl.Int64).alias("records"),
        pl.col("net_amount_cents").sum().alias("revenue_cents"),
    ).sort("region")


def eager_pipeline(raw_folder, output_folder, row_group_size):
    raw = read_raw(raw_folder)
    result = normalize_expressions(raw)
    result.write_parquet(output_folder / "records.parquet", compression="zstd",
                         row_group_size=row_group_size)


def lazy_pipeline(raw_folder, output_folder, row_group_size):
    # No to_dicts(), iter_rows(), Python UDF, or full-result collect().
    normalize_expressions(scan_raw(raw_folder)).sink_parquet(
        output_folder / "records.parquet", compression="zstd",
        row_group_size=row_group_size, maintain_order=True, engine="streaming",
    )


def normalize_python(row):
    """Deliberate Python row processing for comparison with native expressions."""
    status = row["status"] or "completed"
    if status != "completed":
        return None
    cents = int(Decimal(row["amount"]) * 100)
    quantity = int(row["quantity"] or "1")
    discount = int(row["discount_bps"] or "0")
    timestamp = datetime.fromisoformat(row["created_at"]) if row["created_at"] else None
    return {
        "id": int(row["id"]), "amount_cents": cents,
        "region": row["region"] or "unknown", "quantity": quantity,
        "status": status, "user_id": int(row["user_id"]) if row["user_id"] else None,
        "country": (row["country"] or "unknown").upper(), "discount_bps": discount,
        "created_at": timestamp, "product": row["product"], "note": row["note"],
        "source": row["source"] or "fake-api",
        "net_amount_cents": cents * quantity * (10_000 - discount) // 10_000,
    }


def python_pipeline(raw_folder, output_folder, row_group_size):
    raw = read_raw(raw_folder)
    records = []
    for row in raw.iter_rows(named=True):  # A Python dict for every input row.
        normalized = normalize_python(row)
        if normalized is not None:
            records.append(normalized)  # All output dicts remain alive.
    result = pl.DataFrame(records, schema=OUTPUT_SCHEMA)
    result.write_parquet(output_folder / "records.parquet", compression="zstd",
                         row_group_size=row_group_size)


PIPELINES = {"eager": eager_pipeline, "lazy": lazy_pipeline, "python": python_pipeline}
CSV_FUNCTIONS = {"eager": eager_summary, "lazy": lazy_summary, "python": python_loop_summary}


def peak_rss_bytes():
    if sys.platform.startswith("linux"):
        # ru_maxrss can retain the parent's pre-exec peak after fork/exec on Linux.
        # VmHWM is specific to this worker's current executable address space.
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
    output = args.run_dir / (args.mode if args.worker == "pipeline" else f"csv-{args.mode}")
    output.mkdir()
    gc.collect()
    if args.trace_memory:
        tracemalloc.start()
    start = time.perf_counter()
    summary = None
    try:
        if args.worker == "pipeline":
            PIPELINES[args.mode](args.run_dir / "raw", output, args.row_group_size)
        else:
            summary = CSV_FUNCTIONS[args.mode](args.run_dir / "sales.csv")
            if isinstance(summary, pl.DataFrame):
                summary = as_dict(summary)
        seconds = time.perf_counter() - start
        peak_python = tracemalloc.get_traced_memory()[1] if args.trace_memory else None
        peak_rss = peak_rss_bytes()
    finally:
        if args.trace_memory:
            tracemalloc.stop()
    metrics = {"job": args.worker, "mode": args.mode, "seconds": seconds,
               "peak_process_rss_bytes": peak_rss, "peak_python_bytes": peak_python,
               "polars_version": pl.__version__, "threads": pl.thread_pool_size(),
               "execution": "in-process" if args.in_process else "fresh-worker"}
    if summary is not None:
        metrics["summary"] = summary
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")


def benchmark(args, root, job, mode):
    env = dict(os.environ, POLARS_MAX_THREADS=str(args.threads))
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", job,
               "--run-dir", str(root), "--mode", mode,
               "--row-group-size", str(args.row_group_size)]
    if args.trace_memory:
        command.append("--trace-memory")
    if args.in_process:
        worker_args = argparse.Namespace(**vars(args))
        worker_args.worker, worker_args.run_dir, worker_args.mode = job, root, mode
        worker(worker_args)
    else:
        subprocess.run(command, check=True, env=env)
    folder = root / (mode if job == "pipeline" else f"csv-{mode}")
    metrics = json.loads((folder / "metrics.json").read_text(encoding="utf-8"))
    rss = metrics["peak_process_rss_bytes"]
    label = f"{rss / 1024**2:.2f} MiB" if rss is not None else "unknown on this platform"
    note = " (cumulative across this run)" if args.in_process else ""
    print(f"{job}/{mode}: {metrics['seconds']:.2f}s, peak process RSS={label}{note}", flush=True)
    return metrics


def inspect_output(path):
    scan = pl.scan_parquet(path)
    if dict(scan.collect_schema()) != OUTPUT_SCHEMA:
        raise RuntimeError(f"Unexpected Parquet schema: {path}")
    stats = scan.select(pl.len().alias("records"),
                        pl.col("net_amount_cents").sum().alias("revenue_cents"),
                        pl.col("id").n_unique().alias("unique_ids"),
                        (pl.col("status") != "completed").sum().alias("bad_status"),
                        pl.col("id").is_null().sum().alias("null_ids"),
                        pl.col("amount_cents").is_null().sum().alias("null_amounts"),
                        ).collect(engine="streaming").row(0, named=True)
    if (stats["records"] != stats["unique_ids"] or stats["bad_status"]
            or stats["null_ids"] or stats["null_amounts"]):
        raise RuntimeError("Output failed validation")
    summary = summary_plan(scan).collect(engine="streaming")
    summary.write_parquet(path.parent / "summary.parquet", compression="zstd")
    return stats, summary


def compare_outputs(left, right, count, chunk_size):
    """Exact typed row comparison in bounded slices; outside benchmark timings."""
    a, b = pl.scan_parquet(left), pl.scan_parquet(right)
    for offset in range(0, count, chunk_size):
        if not a.slice(offset, chunk_size).collect(engine="streaming").equals(
                b.slice(offset, chunk_size).collect(engine="streaming")):
            raise RuntimeError(f"Output mismatch beginning at row {offset}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--records", type=int, default=ROWS)
    parser.add_argument("--page-size", type=int, default=10_000)
    parser.add_argument("--row-group-size", type=int, default=50_000)
    parser.add_argument("--columns", choices=["mixed"] + [str(i) for i in range(2, 13)], default="mixed")
    parser.add_argument("--mode", choices=("eager", "lazy", "python", "all"), default="lazy")
    parser.add_argument("--output", type=Path, default=Path("polars_output"))
    parser.add_argument("--threads", type=int, default=int(os.environ["POLARS_MAX_THREADS"]))
    parser.add_argument("--trace-memory", action="store_true", help="Also trace Python allocations; excludes native buffers")
    parser.add_argument("--in-process", action="store_true", help="Run in the current process for editor profiling; RSS becomes cumulative")
    parser.add_argument("--basics", action="store_true", help="Also run all three original CSV summary examples")
    parser.add_argument("--worker", choices=("pipeline", "csv"), help=argparse.SUPPRESS)
    parser.add_argument("--run-dir", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.records < 0 or min(args.page_size, args.row_group_size, args.threads) < 1:
        parser.error("records must be >= 0; page-size, row-group-size, and threads must be >= 1")
    if args.worker:
        if args.run_dir is None or args.mode == "all":
            parser.error("workers require run-dir and a single mode")
        worker(args)
        return
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=args.output)).resolve()
    raw = root / "raw"
    raw.mkdir()
    print(f"Polars {pl.__version__}; output: {root}", flush=True)
    start = time.perf_counter()
    ingested = ingest_api(FakeAPI(args.records, args.page_size, args.columns), raw)
    if ingested != args.records:
        raise RuntimeError("API ingestion count mismatch")
    print(f"Ingested {ingested:,} records in {time.perf_counter() - start:.2f}s", flush=True)
    modes = ("eager", "lazy", "python") if args.mode == "all" else (args.mode,)
    metrics, stats, summaries = [], [], []
    for mode in modes:
        metrics.append(benchmark(args, root, "pipeline", mode))
        actual_stats, actual_summary = inspect_output(root / mode / "records.parquet")
        stats.append(actual_stats)
        summaries.append(actual_summary)
        contract = {"polars_schema": {k: str(v) for k, v in OUTPUT_SCHEMA.items()},
                    "defaults": DEFAULTS, "nullable": ["user_id", "created_at", "product", "note"],
                    "filter": "status == completed", "rounding": "net cents truncated downward"}
        (root / mode / "schema.json").write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
        print(f"{mode}: verified {actual_stats['records']:,} output rows", flush=True)
    for index in range(1, len(modes)):
        if stats[index] != stats[0] or not summaries[index].equals(summaries[0]):
            raise RuntimeError("Output statistics or summaries differ")
        compare_outputs(root / modes[0] / "records.parquet", root / modes[index] / "records.parquet",
                        stats[0]["records"], args.row_group_size)
    # Persist the optimized raw-data summary plan to inspect projection pushdown.
    plan = summary_plan(normalize_expressions(scan_raw(raw)))
    (root / "optimized-plan.txt").write_text(plan.explain(optimized=True) + "\n", encoding="utf-8")
    if not plan.collect(engine="streaming").equals(summaries[0]):
        raise RuntimeError("Raw lazy summary and written Parquet summary differ")
    if args.basics:
        write_csv(root / "sales.csv", args.records, args.page_size)
        csv_metrics = [benchmark(args, root, "csv", mode) for mode in ("eager", "lazy", "python")]
        expected = csv_metrics[0]["summary"]
        for measured in csv_metrics[1:]:
            result = measured["summary"]
            if expected.keys() != result.keys() or any(
                    not math.isclose(expected[k], result[k], rel_tol=1e-9, abs_tol=1e-9) for k in expected):
                raise RuntimeError("Original CSV summaries differ")
        metrics.extend(csv_metrics)
        (root / "csv-optimized-plan.txt").write_text(lazy_summary_plan(root / "sales.csv").explain() + "\n",
                                                       encoding="utf-8")
    manifest = {"input_records": args.records, "input_columns": args.columns,
                "page_size": args.page_size, "row_group_size": args.row_group_size,
                "output": stats[0], "benchmarks": metrics,
                "rss_note": "Process high-water RSS including runtime/native allocations; Linux uses VmHWM; other Unix uses ru_maxrss; in-process mode is cumulative"}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("Verified: requested pipeline outputs and summaries match.", flush=True)


if __name__ == "__main__":
    main()
