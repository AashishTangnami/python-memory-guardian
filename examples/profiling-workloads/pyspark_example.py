"""Same 2-million-record API dataset as polars_real_world/pyarrow_real_world.

Install: python -m pip install 'pyspark>=4.0,<4.1'
Requires Java 17+; tested with Python 3.12, Spark 4.0.4, and OpenJDK 17.

    python pyspark_real_world.py --records 2000000
    python pyspark_real_world.py --records 100000 --mode both
    python pyspark_real_world.py --records 2000000 --columns 12
    python pyspark_real_world.py --raw-dir /path/to/previous/run/raw
    python pyspark_real_world.py --raw-dir /path/to/run/raw \
        --reference-parquet /path/to/pyarrow/run/streaming/records.parquet

Identical deterministic fake API, JSONL page names/bytes, 2..12 input fields,
fixed 13-column output, defaults, cancelled-row filter, and integer-cent revenue
formula. Default input volume is 2,000,000. No network API server is used.
--raw-dir reuses previous raw files; --records/--columns must describe that fixture.

Native pipeline: schema-on-read -> built-in Spark SQL expressions -> Parquet.
Optional python-udf pipeline: same normalization, but per-row revenue arithmetic
runs in Python workers. --mode both compares every output row with exceptAll.
Only one-row statistics and at most 51 grouped rows are collected to the driver.
--reference-parquet compares every typed output row against external reference
data, independent of file order and partitioning. Duplicates are accounted for.

Spark is lazy: write is the action that executes reading and transformations.
No collect-all, toPandas, or cache/count warm-up occurs before the timed write.
Startup, fixture generation, processing, and verification are timed separately.
This example uses bounded API pages and Spark task execution; it is not Spark
Structured Streaming and does not promise constant memory for every query.

Parquet uses Zstd level 3 and microsecond UTC timestamps. --max-records-per-file
caps file rows; --row-group-bytes controls row-group bytes, not exact row counts.
These writer settings differ from a 50,000-row Polars/PyArrow row-group target.

Python tracemalloc covers only the driver Python process; it misses the JVM,
executors, and UDF workers. Linux reports driver-Python VmHWM and gateway-JVM
VmHWM when its process is visible in /proc; JVM heap usage is also sampled
through the JVM management API before verification. These lifetime peaks must not
be added as a simultaneous peak. In --mode both, peaks are cumulative across
variants and the second variant uses a warmed JVM; use separate runs for
performance comparisons. Remote executor memory and
Python UDF worker memory are not measured here. Non-Linux RSS is unavailable.
"""

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pyspark.sql import SparkSession, functions as F, types as T

ROWS = 2_000_000
BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
INPUT_FIELDS = (
    "id", "amount", "region", "quantity", "status", "user_id", "country",
    "discount_bps", "created_at", "product", "note", "source",
)
RAW_SCHEMA = T.StructType([T.StructField(name, T.StringType(), True) for name in INPUT_FIELDS])
OUTPUT_SCHEMA = T.StructType([
    T.StructField("id", T.LongType()), T.StructField("amount_cents", T.LongType()),
    T.StructField("region", T.StringType()), T.StructField("quantity", T.LongType()),
    T.StructField("status", T.StringType()), T.StructField("user_id", T.LongType()),
    T.StructField("country", T.StringType()), T.StructField("discount_bps", T.LongType()),
    T.StructField("created_at", T.TimestampType()), T.StructField("product", T.StringType()),
    T.StructField("note", T.StringType()), T.StructField("source", T.StringType()),
    T.StructField("net_amount_cents", T.LongType()),
])
DEFAULTS = {"region": "unknown", "quantity": "1", "status": "completed",
            "country": "unknown", "discount_bps": "0", "source": "fake-api"}


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


def raw_digest(folder):
    digest = hashlib.sha256()
    files = sorted(folder.glob("page-*.jsonl"))
    for path in files:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    return {"files": len(files), "sha256": digest.hexdigest()}


def expected_fixture(records, columns):
    """Independent integer reference based on the deterministic fixture formula."""
    count = total = 0
    groups = {}
    for i in range(records):
        width = 2 + i % 11 if columns == "mixed" else int(columns)
        if width >= 5 and i % 7 == 0:
            continue
        quantity = 1 + i % 5 if width >= 4 else 1
        discount = (i % 6) * 100 if width >= 8 else 0
        revenue = (i * 37 % 100_000) * quantity * (10_000 - discount) // 10_000
        region = f"r{i % 50}" if width >= 3 else "unknown"
        value = groups.setdefault(region, {"records": 0, "revenue_cents": 0})
        value["records"] += 1
        value["revenue_cents"] += revenue
        count += 1
        total += revenue
    return {"records": count, "revenue_cents": total, "groups": groups}


def build_spark(args):
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    builder = (SparkSession.builder.appName("Same dataset: PySpark ETL")
               .master(args.master)
               .config("spark.driver.memory", args.driver_memory)
               .config("spark.sql.session.timeZone", "UTC")
               .config("spark.sql.ansi.enabled", "true")
               .config("spark.sql.shuffle.partitions", args.shuffle_partitions)
               .config("spark.sql.parquet.outputTimestampType", "TIMESTAMP_MICROS")
               .config("spark.hadoop.parquet.compression.codec.zstd.level", args.zstd_level)
               .config("spark.ui.enabled", "false")
               .config("spark.ui.showConsoleProgress", "false"))
    if args.no_python_daemon:
        builder = builder.config("spark.python.use.daemon", "false")
    if args.master.startswith("local"):
        builder = builder.config("spark.driver.host", "127.0.0.1").config("spark.driver.bindAddress", "127.0.0.1")
    spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    return spark


def read_raw(spark, folder):
    files = sorted(folder.glob("page-*.jsonl"))
    if not files:
        return spark.createDataFrame([], RAW_SCHEMA)
    return spark.read.schema(RAW_SCHEMA).option("mode", "FAILFAST").json([str(path) for path in files])


def normalized_columns(raw):
    """Built-in expressions execute in Spark's JVM, not Python row loops."""
    return raw.filter(F.coalesce(F.col("status"), F.lit("completed")) == "completed").select(
        F.col("id").cast("long").alias("id"),
        (F.col("amount").cast(T.DecimalType(18, 2)) * F.lit(100)).cast("long").alias("amount_cents"),
        F.coalesce(F.col("region"), F.lit("unknown")).alias("region"),
        F.coalesce(F.col("quantity"), F.lit("1")).cast("long").alias("quantity"),
        F.coalesce(F.col("status"), F.lit("completed")).alias("status"),
        F.col("user_id").cast("long").alias("user_id"),
        F.upper(F.coalesce(F.col("country"), F.lit("unknown"))).alias("country"),
        F.coalesce(F.col("discount_bps"), F.lit("0")).cast("long").alias("discount_bps"),
        F.to_timestamp("created_at", "yyyy-MM-dd'T'HH:mm:ssXXX").alias("created_at"),
        F.col("product"), F.col("note"),
        F.coalesce(F.col("source"), F.lit("fake-api")).alias("source"),
    )


def python_net_cents(cents, quantity, discount):
    if cents is None or quantity is None or discount is None:
        return None
    return cents * quantity * (10_000 - discount) // 10_000


def native_pipeline(raw):
    return normalized_columns(raw).withColumn(
        "net_amount_cents", F.expr("((amount_cents * quantity) * (10000 - discount_bps)) DIV 10000"))


def python_udf_pipeline(raw):
    calculate = F.udf(python_net_cents, T.LongType())
    return normalized_columns(raw).withColumn(
        "net_amount_cents", calculate("amount_cents", "quantity", "discount_bps"))


def write_parquet(frame, folder, args):
    (frame.write.mode("errorifexists").option("compression", "zstd")
     .option("parquet.compression.codec.zstd.level", args.zstd_level)
     .option("parquet.block.size", args.row_group_bytes)
     .option("maxRecordsPerFile", args.max_records_per_file).parquet(str(folder)))


def process_peak_rss(pid=None, expected_name=None):
    try:
        path = Path("/proc/self/status") if pid is None else Path(f"/proc/{pid}/status")
        lines = path.read_text().splitlines()
        if expected_name is not None and not any(line.split() == ["Name:", expected_name] for line in lines):
            return None
        for line in lines:
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        return None
    return None


def verify_output(spark, folder, expected):
    frame = spark.read.parquet(str(folder))
    if [(f.name, f.dataType) for f in frame.schema] != [(f.name, f.dataType) for f in OUTPUT_SCHEMA]:
        raise RuntimeError("Output names/types do not match the common schema")
    required = ["id", "amount_cents", "region", "quantity", "status", "country",
                "discount_bps", "source", "net_amount_cents"]
    bad = F.col("status") != "completed"
    for name in required:
        bad = bad | F.col(name).isNull()
    stats = frame.agg(F.count("*").alias("records"),
                      F.coalesce(F.sum("net_amount_cents"), F.lit(0)).alias("revenue_cents"),
                      F.count_distinct("id").alias("unique_ids"),
                      F.coalesce(F.sum(F.when(bad, 1).otherwise(0)), F.lit(0)).alias("bad_rows"),
                      ).first().asDict()
    if stats["records"] != expected["records"] or stats["revenue_cents"] != expected["revenue_cents"]:
        raise RuntimeError(f"Output count/revenue mismatch: {stats}")
    if stats["records"] != stats["unique_ids"] or stats["bad_rows"]:
        raise RuntimeError("Output has duplicate IDs or invalid required values")
    grouped = frame.groupBy("region").agg(F.count("*").alias("records"),
                                          F.sum("net_amount_cents").alias("revenue_cents"))
    groups = {row["region"]: {"records": row["records"], "revenue_cents": row["revenue_cents"]}
              for row in grouped.collect()}  # Only 51 fixture groups maximum.
    if groups != expected["groups"]:
        raise RuntimeError("Grouped counts/revenue differ from the independent fixture reference")
    grouped.orderBy("region").write.mode("errorifexists").parquet(str(folder.parent / "summary"))
    return frame, dict(stats, groups=groups)


def compare_exact(left, right):
    """All rows, all fields, duplicate-aware; never collect the dataset to Python."""
    right = right.select([F.col(f.name).cast(f.dataType).alias(f.name) for f in OUTPUT_SCHEMA])
    if left.exceptAll(right).limit(1).count() or right.exceptAll(left).limit(1).count():
        raise RuntimeError("Typed output rows differ from the reference")


def run_variant(spark, raw_folder, root, mode, expected, args):
    folder = root / mode
    folder.mkdir()
    if args.trace_memory:
        tracemalloc.start()
    start = time.perf_counter()
    try:
        raw = read_raw(spark, raw_folder)
        frame = native_pipeline(raw) if mode == "native" else python_udf_pipeline(raw)
        write_parquet(frame, folder / "records", args)  # Executes the lazy plan.
        seconds = time.perf_counter() - start
        python_peak = tracemalloc.get_traced_memory()[1] if args.trace_memory else None
        gateway_process = getattr(spark.sparkContext._gateway, "proc", None)
        gateway_pid = getattr(gateway_process, "pid", None)
        memory_bean = spark._jvm.java.lang.management.ManagementFactory.getMemoryMXBean()
        metrics = {"mode": mode, "read_transform_write_seconds": seconds,
                   "driver_python_peak_rss_bytes": process_peak_rss(),
                   "gateway_jvm_peak_rss_bytes": process_peak_rss(gateway_pid, "java") if gateway_pid else None,
                   "driver_python_traced_peak_bytes": python_peak,
                   "jvm_max_heap_bytes": int(spark._jvm.java.lang.Runtime.getRuntime().maxMemory()),
                   "jvm_heap_used_after_write_bytes": int(memory_bean.getHeapMemoryUsage().getUsed()),
                   "jvm_non_heap_used_after_write_bytes": int(memory_bean.getNonHeapMemoryUsage().getUsed()),
                   "spark_version": spark.version, "master": spark.sparkContext.master,
                   "memory_note": "RSS uses lifetime peaks; JVM heap/non-heap values are post-write snapshots; remote executors and Python UDF workers are excluded"}
    finally:
        if args.trace_memory:
            tracemalloc.stop()
    (folder / "execution-plan.txt").write_text(frame._jdf.queryExecution().toString(), encoding="utf-8")
    (folder / "schema.json").write_text(json.dumps({"spark_schema": frame.schema.jsonValue(),
                                                  "defaults": DEFAULTS}, indent=2) + "\n", encoding="utf-8")
    verify_start = time.perf_counter()
    written, stats = verify_output(spark, folder / "records", expected)
    metrics["verification_seconds"] = time.perf_counter() - verify_start
    (folder / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(f"{mode}: read/transform/write={seconds:.2f}s; verified {stats['records']:,} output rows", flush=True)
    return written, metrics, stats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--records", type=int, default=ROWS)
    parser.add_argument("--columns", choices=["mixed"] + [str(i) for i in range(2, 13)], default="mixed")
    parser.add_argument("--page-size", type=int, default=10_000)
    parser.add_argument("--mode", choices=("native", "python-udf", "both"), default="native")
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--reference-parquet", type=Path)
    parser.add_argument("--output", type=Path, default=Path("pyspark_output"))
    parser.add_argument("--master", default="local[4]")
    parser.add_argument("--driver-memory", default="2g")
    parser.add_argument("--shuffle-partitions", type=int, default=8)
    parser.add_argument("--zstd-level", type=int, choices=range(1, 23), default=3)
    parser.add_argument("--max-records-per-file", type=int, default=50_000)
    parser.add_argument("--row-group-bytes", type=int, default=128 * 1024 * 1024)
    parser.add_argument("--trace-memory", action="store_true")
    parser.add_argument("--no-python-daemon", action="store_true", help="Launch Python UDF workers directly for environments that restrict daemon forking")
    args = parser.parse_args()
    if args.records < 0 or min(args.page_size, args.shuffle_partitions, args.max_records_per_file, args.row_group_bytes) < 1:
        parser.error("records must be >= 0; all sizes and partitions must be positive")
    if args.raw_dir is not None and not args.raw_dir.is_dir():
        parser.error("raw-dir must be an existing folder containing page-*.jsonl")
    if args.reference_parquet is not None and not args.reference_parquet.exists():
        parser.error("reference-parquet must exist")
    args.output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=args.output)).resolve()
    print(f"Output: {root}", flush=True)
    generation_seconds = None
    if args.raw_dir is None:
        raw = root / "raw"
        raw.mkdir()
        start = time.perf_counter()
        if ingest_api(FakeAPI(args.records, args.page_size, args.columns), raw) != args.records:
            raise RuntimeError("Ingestion count mismatch")
        generation_seconds = time.perf_counter() - start
        print(f"Generated {args.records:,} identical fixture records in {generation_seconds:.2f}s", flush=True)
    else:
        raw = args.raw_dir.resolve()
    digest = raw_digest(raw)
    expected = expected_fixture(args.records, args.columns)
    start = time.perf_counter()
    spark = build_spark(args)
    startup_seconds = time.perf_counter() - start
    print(f"Spark {spark.version}; startup={startup_seconds:.2f}s; master={spark.sparkContext.master}", flush=True)
    try:
        modes = ("native", "python-udf") if args.mode == "both" else (args.mode,)
        frames, metrics, stats = [], [], []
        for mode in modes:
            frame, measured, verified = run_variant(spark, raw, root, mode, expected, args)
            frames.append(frame)
            metrics.append(measured)
            stats.append(verified)
        # Count reused input after processing, avoiding a pre-write warm-up job.
        if args.raw_dir is not None and read_raw(spark, raw).count() != args.records:
            raise RuntimeError("Reused raw input does not contain the requested record count")
        if len(frames) == 2:
            compare_exact(frames[0], frames[1])
            print("Verified: native and Python UDF outputs match in every field.", flush=True)
        if args.reference_parquet is not None:
            reference = spark.read.parquet(str(args.reference_parquet.resolve()))
            for frame in frames:
                compare_exact(frame, reference)
            print("Verified: every output row matches the external Parquet reference.", flush=True)
        manifest = {"input_records": args.records, "input_columns": args.columns,
                    "raw_input": str(raw), "raw_digest": digest,
                    "generation_seconds": generation_seconds, "spark_startup_seconds": startup_seconds,
                    "writer": {"compression": "zstd", "level": args.zstd_level,
                               "max_records_per_file": args.max_records_per_file, "row_group_bytes": args.row_group_bytes},
                    "output": stats[0], "benchmarks": metrics,
                    "exact_external_reference_checked": args.reference_parquet is not None}
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    finally:
        spark.stop()
    print("Verified: the requested PySpark fixture processing completed.", flush=True)


if __name__ == "__main__":
    main()
