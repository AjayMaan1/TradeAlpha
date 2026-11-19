"""A4: a failure inside publish_replica / rewrite_compact leaves the ATTACHed database attached?"""
import os, sys, tempfile
sys.path.insert(0, "/workspace/historify-master/historify")
import pandas as pd
from app.core.store.duckdb_store import DuckDBBarStore
d = tempfile.mkdtemp(); st = DuckDBBarStore(os.path.join(d, "bars.duckdb"))
st.upsert(1, 8, pd.DataFrame({"ts": [1741000000], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]}))
real = DuckDBBarStore._src
DuckDBBarStore._src = property(lambda self: "no_such_table")          # make the copy step fail after ATTACH
try:
    st.publish_replica(os.path.join(d, "r1.duckdb"))
except Exception as e:
    print("1st publish failed as intended:", type(e).__name__)
DuckDBBarStore._src = real
try:
    print("2nd publish after the failure:", st.publish_replica(os.path.join(d, "r2.duckdb"))["rows"], "rows (recovered)")
except Exception as e:
    print("2nd publish FAILED:", type(e).__name__, str(e)[:110])
st.close()
