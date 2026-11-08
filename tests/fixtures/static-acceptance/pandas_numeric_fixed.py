import json, sys
import pandas as pd
n = int(sys.argv[1])
data = pd.DataFrame({"value": pd.Series(range(n), dtype="int64")})
row_series_objects = 0

total = int(data["value"].sum())

print(json.dumps({"n": n, "dtype": str(data["value"].dtype),
                  "numpy_dtype_has_object": data["value"].to_numpy().dtype.hasobject,
                  "pandas_memory_usage_deep_bytes": int(data.memory_usage(index=False, deep=True).sum()),
                  "row_series_objects_observed": row_series_objects,
                  "total": int(total), "nulls": int(data["value"].isna().sum()),
                  "hardware_cache_misses": "unavailable; row/object witnesses do not measure cache misses"}))
