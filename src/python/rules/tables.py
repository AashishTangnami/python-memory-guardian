"""Rule tables, the counterpart of src/rust/src/tables.rs."""
from __future__ import annotations

PANDAS_LOADERS = {
    "read_csv", "read_json", "read_table", "read_sql", "read_sql_query",
    "read_sql_table", "read_excel", "read_fwf",
}
LINK_ATTRS = {
    "parent", "prev", "previous", "next", "left", "right", "sibling", "owner",
    "back", "peer", "twin", "partner", "container", "root",
}
WEAKREF_CALLS = {"ref", "proxy", "WeakValueDictionary", "WeakKeyDictionary", "WeakSet"}

# Blocking calls that freeze an asyncio event loop (docs: library/asyncio-dev).
BLOCKING_CALLS = {
    "time.sleep", "requests.get", "requests.post", "requests.put", "requests.patch",
    "requests.delete", "requests.head", "requests.request", "urllib.request.urlopen",
    "subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output",
    "socket.create_connection",
}
AGGREGATORS = {"sum", "any", "all", "min", "max"}

# Thread classification. docs.python.org/3/library/threading: only one thread runs
# Python code at a time, but threading "is still an appropriate model" for I/O-bound
# tasks; the glossary notes some native modules release the GIL (compression, hashing).
THREAD_CTORS = {"threading.Thread"}
EXECUTOR_CTORS = {
    "concurrent.futures.ThreadPoolExecutor", "multiprocessing.pool.ThreadPool",
    "multiprocessing.dummy.Pool",
}
EXECUTOR_METHODS = {"submit", "map", "apply", "apply_async", "imap", "imap_unordered", "starmap"}
IO_PREFIXES = (
    "requests.", "urllib.", "http.", "socket.", "subprocess.", "httpx.", "boto3.",
    "ftplib.", "smtplib.", "sqlite3.", "psycopg2.", "pymysql.", "shutil.",
)
IO_NAMES = {"open", "input", "time.sleep"}
IO_ATTRS = {
    "read", "readline", "readlines", "write", "writelines", "recv", "recv_into", "send",
    "sendall", "connect", "execute", "executemany", "fetchone", "fetchmany", "fetchall",
    "commit", "urlopen", "read_text", "read_bytes", "write_text", "write_bytes",
    "download_file", "upload_file", "get_object", "put_object",
}
NATIVE_PREFIXES = (
    "numpy.", "polars.", "pyarrow.", "hashlib.", "zlib.", "bz2.", "lzma.", "duckdb.",
    "scipy.", "numexpr.",
)
SLOTS_EXEMPT_BASES = {
    "NamedTuple", "Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "TypedDict",
    "Protocol", "Exception", "BaseException",
}

# Queues whose maxsize defaults to 0, which means no limit (docs: library/queue,
# library/asyncio-queue, library/multiprocessing).
QUEUE_CTORS = {
    "queue.Queue", "queue.LifoQueue", "queue.PriorityQueue",
    "asyncio.Queue", "asyncio.LifoQueue", "asyncio.PriorityQueue",
    "multiprocessing.Queue", "multiprocessing.JoinableQueue",
}
# Calls that choose a multiprocessing start method by name (docs: library/multiprocessing).
START_METHOD_CALLS = {"multiprocessing.set_start_method", "multiprocessing.get_context"}
# Calls that return a new object holding a copy of their inputs: a sequence of pieces as the
# first argument, or the array to extend as the first argument (docs: pandas.concat, numpy.append).
CONCAT_SEQUENCE_CALLS = {"pandas.concat", "numpy.concatenate", "numpy.vstack", "numpy.hstack"}
CONCAT_FIRST_ARG_CALLS = {"numpy.append"}
# Methods that add their one argument to a collection.
STORE_METHODS = {"append", "appendleft", "add"}

# Evidence strength and fix safety of every rule code, for the diagnostic `data` payload
# (docs/06-rules.md). Strength: "syntactic" = one call or expression decided the finding;
# "structural" = the rule also checked the code around it in the same file (an enclosing loop,
# class, handler or `async def`, or how a name is bound and used in its scope). Fix safety is about
# the alternative that the message names, because the servers apply no edits: "maybe-incorrect" = a
# local rewrite that usually keeps behaviour but can change it in a case the rule cannot see;
# "manual" = the alternative changes what the code returns, when it runs, or its design.
SYNTACTIC, STRUCTURAL = "syntactic", "structural"
MAYBE_INCORRECT, MANUAL = "maybe-incorrect", "manual"
RULE_TRAITS = {
    "heap-inflation": (SYNTACTIC, MANUAL),
    "pointer-chasing": (SYNTACTIC, MANUAL),
    "cyclic-reference": (STRUCTURAL, MANUAL),
    "gc-cycle-risk": (STRUCTURAL, MANUAL),
    "ram-fragmentation.append": (STRUCTURAL, MANUAL),
    "ram-fragmentation.no-slots": (STRUCTURAL, MAYBE_INCORRECT),
    "text-inflation.concat": (STRUCTURAL, MAYBE_INCORRECT),
    "text-inflation.whole-file": (SYNTACTIC, MAYBE_INCORRECT),
    "memory-swell.list-arg": (SYNTACTIC, MAYBE_INCORRECT),
    "memory-swell.list-copy": (SYNTACTIC, MAYBE_INCORRECT),
    "memory-swell.list-once": (STRUCTURAL, MAYBE_INCORRECT),
    "memory-swell.method-cache": (STRUCTURAL, MANUAL),
    "memory-swell.unbounded-cache": (SYNTACTIC, MANUAL),
    "memory-swell.deepcopy-loop": (STRUCTURAL, MANUAL),
    "memory-swell.recompile-loop": (STRUCTURAL, MAYBE_INCORRECT),
    "memory-swell.setdefault-loop": (STRUCTURAL, MAYBE_INCORRECT),
    "memory-swell.list-extend-loop": (STRUCTURAL, MAYBE_INCORRECT),
    "memory-swell.concat-loop": (STRUCTURAL, MAYBE_INCORRECT),
    "memory-swell.unbounded-queue": (SYNTACTIC, MANUAL),
    "memory-swell.fork-start-method": (SYNTACTIC, MANUAL),
    "task-retention.asyncio-task": (SYNTACTIC, MAYBE_INCORRECT),
    "frame-retention.stored-exception": (STRUCTURAL, MANUAL),
    "resource-leak.file-handle": (STRUCTURAL, MAYBE_INCORRECT),
    "single-thread-stall.async-blocking": (STRUCTURAL, MAYBE_INCORRECT),
    "single-thread-stall.list-membership": (STRUCTURAL, MAYBE_INCORRECT),
    "single-thread-stall.cpu-thread": (STRUCTURAL, MANUAL),
}
# A code that is missing above still gets a payload, with the weaker claim in both fields.
DEFAULT_TRAITS = (SYNTACTIC, MANUAL)
