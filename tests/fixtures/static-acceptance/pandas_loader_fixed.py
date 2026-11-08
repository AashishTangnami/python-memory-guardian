import json, sys, tempfile
from pathlib import Path
import pandas as pd
n = int(sys.argv[1])
with tempfile.TemporaryDirectory() as directory:
    path = Path(directory) / "input.csv"
    path.write_text("value\n" + "\n".join(str(i) for i in range(n)), encoding="utf-8")
    data = pd.read_csv(path, dtype={"value": "int64"})
    print(json.dumps({"n": n, "dtype": str(data["value"].dtype),
                      "numpy_dtype_has_object": data["value"].to_numpy().dtype.hasobject,
                      "pandas_memory_usage_deep_bytes": int(data.memory_usage(index=False, deep=True).sum()),
                      "total": int(data["value"].astype("int64").sum()), "nulls": int(data["value"].isna().sum()),
                      "access_contract": "ordered integers, with conversion before arithmetic"}))
