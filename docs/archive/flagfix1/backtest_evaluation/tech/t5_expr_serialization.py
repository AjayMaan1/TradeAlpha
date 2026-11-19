"""T5: strategies as data. A generated rule is a Polars expression -> JSON -> stored/hashed -> rebuilt -> identical signal."""
import hashlib, io, json
import polars as pl
df = pl.read_ipc("/tmp/eval/t3/m1.arrow").filter(pl.col("instrument_id") == 1).select("ts", "close")
c = pl.col("close")
entry = ((c.ewm_mean(span=9, adjust=False) > c.ewm_mean(span=21, adjust=False)) &
         (c > c.rolling_mean(50))).alias("entry")
blob = entry.meta.serialize(format="json")
print("serialised expression:", len(blob), "bytes; sha256", hashlib.sha256(blob.encode()).hexdigest()[:16])
rebuilt = pl.Expr.deserialize(io.StringIO(blob), format="json")
a, b = df.select(entry)["entry"], df.select(rebuilt)["entry"]
print("signals identical after round-trip:", a.equals(b), "| entries:", int(a.sum()))
print("readable form:", str(rebuilt)[:140])
