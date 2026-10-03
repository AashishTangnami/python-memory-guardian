"""Drive both language servers over stdio LSP and require identical diagnostics."""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = ".exe" if os.name == "nt" else ""
RUST_BIN = os.path.join(ROOT, "rust-server", "target", "release", "guardian-server" + EXE)
SERVERS = {
    "python": [sys.executable, os.path.join(ROOT, "server", "guardian_server.py")],
    "rust": [RUST_BIN],
}


class ServerDied(RuntimeError):
    pass


def run(cmd, profile, text):
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    def died():
        p.kill()
        err = p.stderr.read().decode(errors="replace").strip().splitlines()
        tail = "\n    ".join(err[-6:]) or "(no output)"
        return ServerDied(f"{os.path.basename(cmd[-1])} stopped before answering. Its last output:\n    {tail}")
    def send(m):
        b = json.dumps(m).encode()
        try:
            p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(b) + b); p.stdin.flush()
        except BrokenPipeError:
            raise died() from None
    def recv():
        h = {}
        while (line := p.stdout.readline().decode().strip()):
            k, v = line.split(": "); h[k] = v
        if "Content-Length" not in h:      # EOF: the server exited
            raise died()
        return json.loads(p.stdout.read(int(h["Content-Length"])))
    send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "capabilities": {}, "processId": None, "rootUri": None,
        "initializationOptions": {"profile": profile}}})
    while "id" not in recv(): pass
    send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    send({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {
        "uri": "file:///t.py", "languageId": "python", "version": 1, "text": text}}})
    while True:
        m = recv()
        if m.get("method") == "textDocument/publishDiagnostics":
            p.kill()
            return sorted(
                (d["range"]["start"]["line"], d["range"]["start"]["character"],
                 d["range"]["end"]["line"], d["range"]["end"]["character"],
                 d["code"], d["severity"], d["message"],
                 json.dumps(d.get("relatedInformation"), sort_keys=True))
                for d in m["params"]["diagnostics"])

if __name__ == "__main__":
    sys.path.insert(0, os.path.join(ROOT, "server"))
    import probe
    measured = probe.probe()
    profiles = {
        "measured": measured,
        "free-threaded": dict(measured, gil_state="disabled", allocator="mimalloc heaps (free-threaded build)"),
        "unknown-sizes": {"implementation": "CPython", "py_version": measured["py_version"]},
        "probe-failed": {},
    }
    if not os.path.isdir(os.path.join(ROOT, "server", "libs")):
        sys.exit("server/libs/ is missing: run `npm run vendor:python` first.")
    have_rust = os.path.isfile(RUST_BIN)
    if not have_rust:
        msg = (f"Rust server not built ({os.path.relpath(RUST_BIN, ROOT)} not found). "
               "Build it with `npm run build:rust` to run the Python-vs-Rust comparison.")
        if os.environ.get("PMG_REQUIRE_RUST"):
            sys.exit("FAIL " + msg)
        print("SKIP " + msg + "\n     Checking the Python server on its own instead.")
    failures = 0
    for fixture in ("sample.py", "native_patterns.py", "edge_cases.py", "scope_cases.py", "new_rules_cases.py"):
        text = open(os.path.join(HERE, fixture), encoding="utf-8").read()
        for pname, prof in profiles.items():
            try:
                py = run(SERVERS["python"], prof, text)
                if fixture in {"scope_cases.py", "new_rules_cases.py"}:
                    expected = set()
                    for line, source in enumerate(text.splitlines()):
                        if '# expect:' in source:
                            expected.add((line, source.split('# expect:')[1].strip()))
                        if '# expect-gil:' in source and prof.get('gil_state') != 'disabled':
                            expected.add((line, source.split('# expect-gil:')[1].strip()))
                    actual = {(row[0], row[4]) for row in py}
                    if actual != expected:
                        failures += 1
                        print(f"FAIL  scope expectations / {pname}: missing={expected - actual}, extra={actual - expected}")
                if not have_rust:
                    ok = len(py) > 0
                    failures += not ok
                    print(f"{'PASS' if ok else 'FAIL'}  {fixture:<20} {pname:<14} {len(py)} diagnostics (Python server only)")
                    continue
                rs = run(SERVERS["rust"], prof, text)
            except ServerDied as e:
                sys.exit(f"FAIL  {fixture} / {pname}: {e}")
            ok = py == rs
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {fixture:<20} {pname:<14} {len(py)} diagnostics")
            if not ok:
                for row in sorted(set(py) ^ set(rs)):
                    print("   ", "py-only" if row in py else "rs-only", row[:6])
    sys.exit(1 if failures else 0)
