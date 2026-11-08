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
