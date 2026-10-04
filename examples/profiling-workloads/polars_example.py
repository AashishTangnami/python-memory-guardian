"""Eager vs lazy Polars, and Python row loops vs expressions.

read_csv loads every column of every row before filtering; scan_csv builds a
query plan and reads only what the result needs. Polars runs its work in native
threads, so expression time appears as native in the time split, while a Python
loop over rows shows as Python time on your line.
Requires: pip install polars
"""
import os
import tempfile

import polars as pl

ROWS = 2_000_000


def write_csv(path, n):
    pl.select(pl.int_range(0, n).alias("id")).with_columns(
        ("r" + (pl.col("id") % 50).cast(pl.Utf8)).alias("region"),
        ((pl.col("id") % 977) * 0.5).alias("amount"),
        (pl.col("id") * 31 % 100_003).cast(pl.Utf8).alias("note"),   # a column the summaries never use
    ).write_csv(path)


def eager_summary(path):
    df = pl.read_csv(path)                                           # every column, every row
    return df.filter(pl.col("amount") > 100).group_by("region").agg(pl.col("amount").sum())


def lazy_summary(path):
    return (pl.scan_csv(path)                                        # a plan, not data
            .filter(pl.col("amount") > 100)
            .group_by("region").agg(pl.col("amount").sum())
            .collect())                                              # reads only region and amount


def python_loop_summary(path):
    df = pl.read_csv(path, columns=["region", "amount"])
    totals = {}
    for region, amount in df.iter_rows():                            # one Python tuple per row
        if amount > 100:
            totals[region] = totals.get(region, 0.0) + amount
    return totals


def as_dict(frame):
    return dict(zip(frame["region"].to_list(), frame["amount"].to_list()))


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "sales.csv")
        write_csv(path, ROWS)
        eager = as_dict(eager_summary(path))
        lazy = as_dict(lazy_summary(path))
        looped = python_loop_summary(path)
        assert eager.keys() == lazy.keys() == looped.keys()
        assert all(abs(eager[k] - lazy[k]) < 1e-6 * eager[k] and abs(eager[k] - looped[k]) < 1e-6 * eager[k] for k in eager)
    print("polars: eager, lazy and loop summaries match")
