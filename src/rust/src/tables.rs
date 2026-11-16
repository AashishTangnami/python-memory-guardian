//! Rule tables, shared in meaning with src/python/rules/tables.py.
// ---------------------------------------------------------------- rule tables (= rules/tables.py)
pub(crate) const PANDAS_LOADERS: &[&str] = &["read_csv", "read_json", "read_table", "read_sql",
    "read_sql_query", "read_sql_table", "read_excel", "read_fwf"];
pub(crate) const LINK_ATTRS: &[&str] = &["parent", "prev", "previous", "next", "left", "right", "sibling",
    "owner", "back", "peer", "twin", "partner", "container", "root"];
pub(crate) const WEAKREF_CALLS: &[&str] = &["ref", "proxy", "WeakValueDictionary", "WeakKeyDictionary", "WeakSet"];
pub(crate) const LITERALS: &[&str] = &["none", "integer", "float", "string", "concatenated_string", "true", "false"];
pub(crate) const BLOCKING_CALLS: &[&str] = &["time.sleep", "requests.get", "requests.post", "requests.put",
    "requests.patch", "requests.delete", "requests.head", "requests.request",
    "urllib.request.urlopen", "subprocess.run", "subprocess.call", "subprocess.check_call",
    "subprocess.check_output", "socket.create_connection"];
pub(crate) const AGGREGATORS: &[&str] = &["sum", "any", "all", "min", "max"];
pub(crate) const THREAD_CTORS: &[&str] = &["threading.Thread"];
pub(crate) const EXECUTOR_CTORS: &[&str] = &["concurrent.futures.ThreadPoolExecutor",
    "multiprocessing.pool.ThreadPool", "multiprocessing.dummy.Pool"];
pub(crate) const EXECUTOR_METHODS: &[&str] = &["submit", "map", "apply", "apply_async", "imap",
    "imap_unordered", "starmap"];
pub(crate) const IO_PREFIXES: &[&str] = &["requests.", "urllib.", "http.", "socket.", "subprocess.", "httpx.",
    "boto3.", "ftplib.", "smtplib.", "sqlite3.", "psycopg2.", "pymysql.", "shutil."];
pub(crate) const IO_NAMES: &[&str] = &["open", "input", "time.sleep"];
pub(crate) const IO_ATTRS: &[&str] = &["read", "readline", "readlines", "write", "writelines", "recv",
    "recv_into", "send", "sendall", "connect", "execute", "executemany", "fetchone", "fetchmany",
    "fetchall", "commit", "urlopen", "read_text", "read_bytes", "write_text", "write_bytes",
    "download_file", "upload_file", "get_object", "put_object"];
pub(crate) const NATIVE_PREFIXES: &[&str] = &["numpy.", "polars.", "pyarrow.", "hashlib.", "zlib.", "bz2.",
    "lzma.", "duckdb.", "scipy.", "numexpr."];
pub(crate) const SLOTS_EXEMPT_BASES: &[&str] = &["NamedTuple", "Enum", "IntEnum", "StrEnum", "Flag",
    "IntFlag", "TypedDict", "Protocol", "Exception", "BaseException"];
pub(crate) const COMPREHENSIONS: &[&str] = &["list_comprehension", "set_comprehension",
    "dictionary_comprehension", "generator_expression"];
pub(crate) const QUEUE_CTORS: &[&str] = &["queue.Queue", "queue.LifoQueue", "queue.PriorityQueue",
    "asyncio.Queue", "asyncio.LifoQueue", "asyncio.PriorityQueue", "multiprocessing.Queue",
    "multiprocessing.JoinableQueue"];
pub(crate) const START_METHOD_CALLS: &[&str] = &["multiprocessing.set_start_method", "multiprocessing.get_context"];
pub(crate) const CONCAT_SEQUENCE_CALLS: &[&str] = &["pandas.concat", "numpy.concatenate", "numpy.vstack",
    "numpy.hstack"];
pub(crate) const CONCAT_FIRST_ARG_CALLS: &[&str] = &["numpy.append"];
pub(crate) const STORE_METHODS: &[&str] = &["append", "appendleft", "add"];
// Evidence strength and fix safety of every rule code, for the diagnostic `data` payload
// (docs/06-rules.md); the meaning of each value is in src/python/rules/tables.py.
pub(crate) const RULE_TRAITS: &[(&str, &str, &str)] = &[
    ("heap-inflation", "syntactic", "manual"),
    ("pointer-chasing", "syntactic", "manual"),
    ("cyclic-reference", "structural", "manual"),
    ("gc-cycle-risk", "structural", "manual"),
    ("ram-fragmentation.append", "structural", "manual"),
    ("ram-fragmentation.no-slots", "structural", "maybe-incorrect"),
    ("text-inflation.concat", "structural", "maybe-incorrect"),
    ("text-inflation.whole-file", "syntactic", "maybe-incorrect"),
    ("memory-swell.list-arg", "syntactic", "maybe-incorrect"),
    ("memory-swell.list-copy", "syntactic", "maybe-incorrect"),
    ("memory-swell.list-once", "structural", "maybe-incorrect"),
    ("memory-swell.method-cache", "structural", "manual"),
    ("memory-swell.unbounded-cache", "syntactic", "manual"),
    ("memory-swell.deepcopy-loop", "structural", "manual"),
    ("memory-swell.recompile-loop", "structural", "maybe-incorrect"),
    ("memory-swell.setdefault-loop", "structural", "maybe-incorrect"),
    ("memory-swell.list-extend-loop", "structural", "maybe-incorrect"),
    ("memory-swell.concat-loop", "structural", "maybe-incorrect"),
    ("memory-swell.unbounded-queue", "syntactic", "manual"),
    ("memory-swell.fork-start-method", "syntactic", "manual"),
    ("task-retention.asyncio-task", "syntactic", "maybe-incorrect"),
    ("frame-retention.stored-exception", "structural", "manual"),
    ("resource-leak.file-handle", "structural", "maybe-incorrect"),
    ("single-thread-stall.async-blocking", "structural", "maybe-incorrect"),
    ("single-thread-stall.list-membership", "structural", "maybe-incorrect"),
    ("single-thread-stall.cpu-thread", "structural", "manual"),
];
// A code that is missing above still gets a payload, with the weaker claim in both fields.
pub(crate) const DEFAULT_TRAITS: (&str, &str) = ("syntactic", "manual");
