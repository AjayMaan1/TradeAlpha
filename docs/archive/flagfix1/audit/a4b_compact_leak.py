import os, sys, tempfile
sys.path.insert(0, "/workspace/historify-master/historify")
import pandas as pd
import app.core.store.duckdb_store as m
st = m.DuckDBBarStore(os.path.join(tempfile.mkdtemp(), "bars.duckdb"))
st.upsert(1, 8, pd.DataFrame({"ts": [1741000000], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1]}))
real = m._COLS
m._COLS = "instrument_id, nonexistent_column"                      # make the copy step fail after ATTACH nc
try: st.rewrite_compact()
except Exception as e: print("1st compaction failed as intended:", type(e).__name__)
m._COLS = real
try: print("2nd compaction:", st.rewrite_compact()["rows"], "rows (recovered)")
except Exception as e: print("2nd compaction FAILED:", type(e).__name__, str(e)[:100])
st.close()
