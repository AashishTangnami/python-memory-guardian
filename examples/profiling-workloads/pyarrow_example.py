"""Python objects vs Arrow columns for the same table.

Row dicts put every value in its own Python object; Arrow keeps each column in
one native buffer. Arrow's buffers are allocated outside Python, so fast mode
(process RSS) sees them but precise mode (tracemalloc) does not: in precise mode
they appear in the run's untraced estimate, not on your lines.
Requires: pip install pyarrow
"""
import os
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

ROWS = 1_000_000


def rows_as_dicts(n):
    rows = []
    for i in range(n):
        rows.append({"id": i, "region": f"r{i % 50}", "amount": i % 977 * 0.5})   # one dict per row
    return rows


def total_by_region_python(rows):
    totals = {}
    for row in rows:
        totals[row["region"]] = totals.get(row["region"], 0.0) + row["amount"]
    return totals


def table_as_columns(n):
    return pa.table({
        "id": pa.array(range(n), pa.int64()),
        "region": pa.array((f"r{i % 50}" for i in range(n)), pa.string()),
        "amount": pa.array((i % 977 * 0.5 for i in range(n)), pa.float64()),
    })


def total_by_region_arrow(table):
    grouped = table.group_by("region").aggregate([("amount", "sum")])   # runs in Arrow's C++ code
    return dict(zip(grouped["region"].to_pylist(), grouped["amount_sum"].to_pylist()))


def read_back_as_python(path):
    return pq.read_table(path).to_pylist()           # a dict per row again: the memory comes back


def read_back_columns(path):
    return pq.read_table(path, columns=["region", "amount"])   # only what is needed, still columnar


if __name__ == "__main__":
    rows = rows_as_dicts(ROWS)
    expected = total_by_region_python(rows)
    del rows
    table = table_as_columns(ROWS)
    totals = total_by_region_arrow(table)
    assert all(abs(totals[k] - v) < 1e-6 * max(1.0, abs(v)) for k, v in expected.items())
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "sales.parquet")
        pq.write_table(table, path)
        assert len(read_back_as_python(path)) == ROWS
        assert read_back_columns(path).num_rows == ROWS
    print("pyarrow: Python and Arrow totals match")
