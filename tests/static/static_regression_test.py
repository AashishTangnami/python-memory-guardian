"""Run independent cost witnesses before red/green static checks on both backends.

Reproduce: python3 tests/static/static_regression_test.py --report /tmp/static-results.json
Each fixture can also run directly: python3 tests/fixtures/static-acceptance/<name>.py 1000
Rust must be freshly built by the caller. Missing optional pandas cases are incomplete.
"""
import importlib.util
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "static-acceptance"
sys.path.insert(0, str(ROOT / "src" / "python"))
import probe
import rules
import parity_test

REPORT = None
if "--report" in sys.argv:
    option = sys.argv.index("--report")
    REPORT = Path(sys.argv[option + 1])
    del sys.argv[option:option + 2]
RECORDS = []


def record(kind, **data):
    RECORDS.append({"sequence": len(RECORDS), "time_ns": time.time_ns(), "kind": kind, **data})


class StaticRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.facts = probe.probe()
        if not Path(parity_test.RUST_BIN).is_file():
            raise RuntimeError("Build the Rust backend with npm run build:rust before validation.")

    def witness(self, name, variant, size):
        fixture = FIXTURES / f"{name}_{variant}.py"
        command = [sys.executable, str(fixture), str(size)]
        run = subprocess.run(command, capture_output=True, text=True, timeout=30)
        record("independent_execution", fixture=str(fixture), command=command,
               stdout=run.stdout, stderr=run.stderr, returncode=run.returncode)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    def findings(self, name, variant, code):
        fixture = FIXTURES / f"{name}_{variant}.py"
        source = fixture.read_text()
        python = parity_test.run(parity_test.SERVERS["python"], self.facts, source)
        rust = parity_test.run(parity_test.SERVERS["rust"], self.facts, source)
        record("static_analysis", fixture=str(fixture), source=source, facts=self.facts,
               source_sha256=hashlib.sha256(source.encode()).hexdigest(),
               command=[sys.executable, str(HERE / "static_regression_test.py")],
               python_rows=python, rust_rows=rust)
        self.assertEqual(python, rust, "The current Python and freshly built Rust diagnostics differ.")
        return [row for row in python if row[4] == code]

    def sql_pair(self, name):
        red = [self.witness(name, "positive", n) for n in (1000, 10000)]
        self.assertEqual([x["rows_retained_at_cleanup"] for x in red], [1000, 10000])
        self.assertGreater(red[1]["python_peak_bytes"], red[0]["python_peak_bytes"] * 5)
        self.assertTrue(self.findings(name, "positive", "heap-inflation"))
        green = [self.witness(name, "fixed", n) for n in (1000, 10000)]
        self.assertTrue(all(x["rows_retained_at_cleanup"] <= 1 for x in green))
        self.assertTrue(all(x["python_peak_bytes"] < 2000 for x in green))
        self.assertEqual([x["sum"] for x in red], [x["sum"] for x in green])
        self.assertFalse(self.findings(name, "fixed", "heap-inflation"))

    def test_cursor_streaming_is_not_eager(self):
        self.sql_pair("sqlite_cursor")

    def test_fetchone_streaming_is_not_eager(self):
        self.sql_pair("sqlite_fetchone")

    def pandas_pair(self, name):
        if importlib.util.find_spec("pandas") is None:
            record("unsupported", fixture=name, reason="pandas is not installed")
            self.skipTest("INCOMPLETE: optional pandas witness dependency is unavailable")
        red = [self.witness(name, "positive", n) for n in (1000, 10000)]
        self.assertTrue(all(x["numpy_dtype_has_object"] for x in red))
        self.assertGreater(red[1]["pandas_memory_usage_deep_bytes"],
                           red[0]["pandas_memory_usage_deep_bytes"] * 5)
        self.assertTrue(self.findings(name, "positive", "pointer-chasing"))
        green = [self.witness(name, "fixed", n) for n in (1000, 10000)]
        self.assertFalse(any(x["numpy_dtype_has_object"] for x in green))
        self.assertTrue(all(x["pandas_memory_usage_deep_bytes"] == 8 * x["n"] for x in green))
        self.assertEqual([x["total"] for x in red], [x["total"] for x in green])
        self.assertFalse(self.findings(name, "fixed", "pointer-chasing"))

    def test_explicit_object_series_and_numeric_series(self):
        self.pandas_pair("pandas_numeric")

    def test_explicit_object_loader_and_numeric_loader(self):
        self.pandas_pair("pandas_loader")

    def test_slots_with_fixed_fields_preserve_contract(self):
        red = [self.witness("slots", "positive", n) for n in (1000, 10000)]
        self.assertTrue(all(x["mutability_null_order_preserved"] for x in red))
        self.assertGreater(red[1]["python_current_bytes"], red[0]["python_current_bytes"] * 5)
        self.assertTrue(self.findings("slots", "positive", "ram-fragmentation.no-slots"))
        green = [self.witness("slots", "fixed", n) for n in (1000, 10000)]
        self.assertTrue(all(x["mutability_null_order_preserved"] for x in green))
        self.assertTrue(all(g["python_current_bytes"] < r["python_current_bytes"]
                            for r, g in zip(red, green)))
        self.assertFalse(self.findings("slots", "fixed", "ram-fragmentation.no-slots"))

    def test_membership_index_preserves_original_order(self):
        red = [self.witness("membership", "positive", n) for n in (1000, 10000)]
        self.assertEqual([x["equality_calls"] for x in red], [100000, 1000000])
        self.assertTrue(self.findings("membership", "positive", "single-thread-stall.list-membership"))
        green = [self.witness("membership", "fixed", n) for n in (1000, 10000)]
        self.assertTrue(all(x["equality_calls"] == 0 and x["original_order_kept"] for x in green))
        self.assertEqual([x["hits"] for x in red], [x["hits"] for x in green])
        self.assertFalse(self.findings("membership", "fixed", "single-thread-stall.list-membership"))

    def test_stored_exception_keeps_frame_locals(self):
        red = [self.witness("stored_exception", "positive", n) for n in (1000, 10000)]
        self.assertEqual([x["errors"] for x in red], [1000, 10000])
        self.assertTrue(all(x["python_current_bytes"] > x["n"] * x["payload_bytes"] for x in red))
        self.assertTrue(self.findings("stored_exception", "positive", "frame-retention.stored-exception"))
        green = [self.witness("stored_exception", "fixed", n) for n in (1000, 10000)]
        self.assertTrue(all(g["python_current_bytes"] < r["python_current_bytes"] / 10
                            for r, g in zip(red, green)))
        self.assertEqual([x["messages"] for x in red], [x["messages"] for x in green])
        self.assertFalse(self.findings("stored_exception", "fixed", "frame-retention.stored-exception"))

    def test_new_rules_have_documented_severity_and_alternative(self):
        warning, information = 2, 3
        source = ('import multiprocessing, queue\n'
                  'import pandas as pd\n'
                  'jobs = queue.Queue()\n'
                  'multiprocessing.set_start_method("fork")\n'
                  'def collect(chunks, errors):\n'
                  '    frame = pd.DataFrame()\n'
                  '    for chunk in chunks:\n'
                  '        try:\n'
                  '            frame = pd.concat([frame, chunk])\n'
                  '        except ValueError as exc:\n'
                  '            errors.append(exc)\n'
                  '    return frame\n')
        rows = parity_test.run(parity_test.SERVERS["python"], self.facts, source)
        rust = parity_test.run(parity_test.SERVERS["rust"], self.facts, source)
        record("static_analysis", fixture="new_rules", source=source, python_rows=rows, rust_rows=rust)
        self.assertEqual(rows, rust)
        expected = {
            "memory-swell.unbounded-queue": (2, information, ("queue.Queue()", "maxsize", "backpressure")),
            "memory-swell.fork-start-method": (3, information, ("reference count", "shared_memory", "forkserver")),
            "memory-swell.concat-loop": (8, warning, ("pandas.concat()", "`frame`", "once after the loop")),
            "frame-retention.stored-exception": (10, information, ("`errors`", "traceback", "str(exc)",
                                                                   "traceback.clear_frames(exc.__traceback__)")),
        }
        self.assertEqual({row[4]: (row[0], row[5]) for row in rows},
                         {code: (line, severity) for code, (line, severity, _) in expected.items()})
        for row in rows:
            for phrase in expected[row[4]][2]:
                self.assertIn(phrase, row[6])

    def test_every_rule_states_evidence_strength_and_fix_safety(self):
        # The payload separates three things that severity alone carried: impact (severity), how
        # much of the surrounding code the rule checked (evidence.strength), and whether the named
        # alternative keeps behaviour (fix_safety). Every code needs both values in both servers.
        import re
        from rules import tables
        codes = {key for key in json.loads((ROOT / "src/python/messages.json").read_text()) if not key.startswith("_")}
        self.assertEqual(set(tables.RULE_TRAITS), codes, "one entry per rule code, no more")
        for strength, safety in tables.RULE_TRAITS.values():
            self.assertIn(strength, ("syntactic", "structural"))
            self.assertIn(safety, ("maybe-incorrect", "manual"))
        rust_rows = re.findall(r'\("([a-z.\-]+)", "(syntactic|structural)", "(maybe-incorrect|manual)"\)',
                               (ROOT / "src/rust/src/tables.rs").read_text())
        self.assertEqual({code: (strength, safety) for code, strength, safety in rust_rows}, tables.RULE_TRAITS,
                         "src/rust/src/tables.rs holds the same table")
        source = ('def load(cur):\n'
                  '    rows = cur.fetchall()\n'
                  '    return rows\n'
                  'def total(xs):\n'
                  '    values = [x * 2 for x in xs]\n'
                  '    return sum(values)\n')
        rows = parity_test.run(parity_test.SERVERS["python"], self.facts, source)
        rust = parity_test.run(parity_test.SERVERS["rust"], self.facts, source)
        record("static_analysis", fixture="rule_traits", source=source, python_rows=rows, rust_rows=rust)
        self.assertEqual(rows, rust)
        payloads = {row[4]: json.loads(row[8]) for row in rows}
        self.assertEqual({code: (data["schema_version"], data["evidence"]["strength"], data["fix_safety"],
                                 data["confidence"], data["status"])
                          for code, data in payloads.items()},
                         {"heap-inflation": (2, "syntactic", "manual", "static", "potential"),
                          "memory-swell.list-once": (2, "structural", "maybe-incorrect", "static", "potential")})

    def test_structure_advice_names_a_concrete_alternative(self):
        concrete = {
            "heap-inflation": ("fetchmany()", "chunksize=N", "iter_batches=True", "fetch_record_batch()"),
            "pointer-chasing": ('"category"', '"string[pyarrow]"', '"Int64"', 'dtype_backend="pyarrow"'),
            "ram-fragmentation.append": ("one list per column", "array.array", "pyarrow.table(", "polars.DataFrame("),
        }
        for code, phrases in concrete.items():
            for facts in ({}, self.facts):  # the neutral wording, then the sized wording where it exists
                message = rules.render(code, facts, {})
                for phrase in phrases:
                    self.assertIn(phrase, message)
                self.assertIn("preserve", message)  # the compatibility conditions stay in the advice

    def test_set_advice_requires_hashability_and_stable_keys(self):
        values = [[1], None]
        self.assertIn([1], values)
        with self.assertRaises(TypeError):
            set(values)
        class Key:
            def __init__(self, value): self.value = value
            def __hash__(self): return hash(self.value)
            def __eq__(self, other): return isinstance(other, Key) and self.value == other.value
        key = Key(1)
        index = {key}
        key.value = 2
        self.assertIn(key, [key])
        self.assertNotIn(key, index)
        record("independent_semantic_counterexample", fixture="set_advice",
               observations={"unhashable_set_raises": "TypeError", "mutated_key_lost_in_set": True})
        message = rules.render("single-thread-stall.list-membership", {}, {"name": "values"})
        self.assertIn("hashable", message)
        self.assertIn("stable", message)
        self.assertIn("order", message)
        self.assertIn("mutations", message)

    def test_slots_advice_respects_required_dynamic_attributes(self):
        class Dynamic:
            pass
        class Fixed:
            __slots__ = ("value",)
        setattr(Dynamic(), "runtime_field", 42)
        with self.assertRaises(AttributeError):
            setattr(Fixed(), "runtime_field", 42)
        record("independent_semantic_counterexample", fixture="slots_advice",
               observations={"dynamic_field_supported": True, "slotted_dynamic_field_raises": "AttributeError"})
        source = ('class Dynamic:\n'
                  '    def set_field(self, name, value): setattr(self, name, value)\n'
                  'for i in range(100): Dynamic()\n')
        rows = parity_test.run(parity_test.SERVERS["python"], self.facts, source)
        rust = parity_test.run(parity_test.SERVERS["rust"], self.facts, source)
        record("static_analysis", fixture="slots_advice", source=source,
               python_rows=rows, rust_rows=rust)
        self.assertEqual(rows, rust)
        self.assertFalse([row for row in rows if row[4] == "ram-fragmentation.no-slots"])
        message = rules.render("ram-fragmentation.no-slots", {}, {"name": "Dynamic"})
        self.assertIn("dynamic attributes", message)
        self.assertIn("weak references", message)


if __name__ == "__main__":
    program = unittest.main(exit=False, verbosity=2)
    if REPORT:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps({"records": RECORDS,
            "tests_run": program.result.testsRun,
            "failed": len(program.result.failures) + len(program.result.errors),
            "skipped_incomplete": len(program.result.skipped)}, indent=2) + "\n")
    sys.exit(0 if program.result.wasSuccessful() and not program.result.skipped else 1)
