"""Validate pmg_profile.py against workloads whose correct answers are known."""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROF = os.path.join(HERE, "..", "..", "src", "python", "pmg_profile.py")
W = os.path.join(HERE, "..", "fixtures", "profiler")
OUT = os.path.join(HERE, "out")

def run(script, mode):
    out = os.path.join(OUT, f"{os.path.splitext(script)[0]}_{mode}.json")
    subprocess.run([sys.executable, PROF, "--memory-guard", "off", "--memory", mode, "--out", out, os.path.join(W, script)],
                   check=True, capture_output=True)
    p = json.load(open(out))
    return p, next((v for k, v in p["files"].items() if k.endswith(script)), {})

def dominant(e):
    return max(("python", e["python_s"]), ("native", e["native_s"]), ("system", e["system_s"]),
               key=lambda kv: kv[1])[0]

import sysconfig
# Free-threaded builds sample no time per line and no functions (stack_sampling.active is false;
# profiler_regression_test covers that branch). Every other check still runs there.
SAMPLED = not sysconfig.get_config_var("Py_GIL_DISABLED")

failures = 0
def check(name, cond):
    global failures
    failures += not cond
    print(("PASS " if cond else "FAIL ") + name)

def skip(*names):
    for name in names:
        print("SKIP " + name + " (free-threaded build: no sampled time per line)")

p, f = run("workload.py", "fast")
if SAMPLED:
    check("L7  pure-Python loop -> python", dominant(f["7"]) == "python")
    check("L12 hashlib (releases GIL) -> native", dominant(f["12"]) == "native")
    check("L16 sorted (holds GIL) -> native", dominant(f["16"]) == "native")
    check("L19 time.sleep(0.8) -> system ~0.8 s", dominant(f["19"]) == "system" and abs(f["19"]["system_s"] - 0.8) < 0.1)
    check("L25 12 x sleep(0.25) -> system ~3.0 s", abs(f["25"]["system_s"] - 3.0) < 0.2)
    fn = {v["name"]: v for v in next(iter(p["functions"].values())).values()}
    check("fast memory has no invented per-function attribution", 'rss_growth_mb' not in fn['temporary'])
else:
    skip("L7, L12, L16, L19, L25 time split", "fast memory has no invented per-function attribution")
check("temporary(): substantial process RSS growth", p['rss_peak_mb'] - p['rss_start_mb'] > 80)

p, f = run("workload.py", "precise")
check("L24 LEAK.append flagged as leak, 48 MB held", "leak_runs" in f["24"] and abs(f["24"]["end_mb"] - 48) < 2)
# sorted()'s copy attribution depends on where the OS lets the sampler in, so it is checked
# deterministically in profiler_regression_test.py: test_gil_holding_call_result_is_credited_once_to_its_line.
check("L28 temporary(): ~160 MB observed, then freed",
      p['peak_traced_mb'] > 150
      and f["28"]["end_mb"] < 1)
check("snapshot budget respected (<= 12% of wall)", p["snapshot_cost_s"] <= 0.12 * p["wall_s"])
check("timeline includes exit sample after temporary memory is released",
      abs(p["timeline"][-1][0] - p["wall_s"]) < 0.01
      and p["timeline"][-1][1] < p["peak_traced_mb"] - 100)

p, f = run("leak_workload.py", "precise")
check("leak_workload L4 (+2 MB/call, kept) -> leak", "leak_runs" in f["4"])
check("leak_workload L5 (8 MB/call, freed) -> no leak", "leak_runs" not in f.get("5", {}))
p, f = run("holders_workload.py", "precise")
holder = lambda ln: [h["holder"] for h in f.get(ln, {}).get("held_by", [])]
check("L13 growth owned by global svc.history", holder("13") == ["global svc.history"])
check("L14 growth owned by global svc.index (gc-untracked dict)", holder("14") == ["global svc.index"])
check("L15 leak held by global AUDIT", holder("15") == ["global AUDIT"])
check("L16 leak held by global REGISTRY (gc-untracked dict)", holder("16") == ["global REGISTRY"])
check("L17 scratch buffer: measured, not a leak, no holder", "17" in f and "leak_runs" not in f["17"] and not holder("17"))
check("scope names the method: Service.handle()", f["13"].get("scope") == "Service.handle()")
check("assigned variable named: scratch", f.get("17", {}).get("assigns") == ["scratch"])
check("subscript target named: self.index[…]", f.get("14", {}).get("assigns") == ["self.index[…]"])
if SAMPLED:
    fn = [v["name"] for v in next(v for k, v in p["functions"].items() if k.endswith("holders_workload.py")).values()]
    check("function totals use qualified names", "Service.handle" in fn)
else:
    skip("function totals use qualified names")
p, f = run("workload.py", "precise")
check("workload L24 leak held by global LEAK", [h["holder"] for h in f["24"].get("held_by", [])] == ["global LEAK"])
sys.exit(1 if failures else 0)
