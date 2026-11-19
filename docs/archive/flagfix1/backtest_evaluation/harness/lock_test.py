import os, sys, subprocess, time
sys.path.insert(0, "/workspace/historify-master/historify"); os.environ["HISTORIFY_SECRET_BACKEND"]="file"
from app.core.services import Services
sv = Services("/tmp/eval/inst")           # what the running app holds
probe = r'''
import duckdb, sys
for mode in ("read_only", "read_write"):
    try:
        c = duckdb.connect("/tmp/eval/inst/bars.duckdb", read_only=(mode=="read_only"))
        print(mode, "OK", c.execute("select count(*) from ohlcv").fetchone()); c.close()
    except Exception as e:
        print(mode, "FAIL", type(e).__name__, str(e)[:160])
'''
print("--- while Historify holds the store:")
print(subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True).stdout)
sv.close()
print("--- after Historify closed:")
print(subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True).stdout)
