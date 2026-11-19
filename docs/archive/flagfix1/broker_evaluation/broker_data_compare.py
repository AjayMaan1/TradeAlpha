"""
Compare two brokers' candles for the same symbol and day (from broker_data_probe.py outputs): are they the same data?

    python broker_data_compare.py results/probe_zerodha.json results/probe_dhan.json

Reports matched bars, bars missing on either side, OHLC differences beyond one tick (Rs 0.05), volume ratio, and the
conventions side by side. This matters if one broker is the DATA source and another the EXECUTION broker: a backtest
on broker A's candles should be validated against the candles broker B's live feed produces.
"""
from __future__ import annotations

import json
import statistics
import sys


def main(a_path, b_path, tick=0.05):
    A, B = (json.load(open(p)) for p in (a_path, b_path))
    ma = {r["ts"]: r for r in A.get("conventions", {}).get("sample_1m", [])}
    mb = {r["ts"]: r for r in B.get("conventions", {}).get("sample_1m", [])}
    common = sorted(set(ma) & set(mb))
    out = {"a": A["label"], "b": B["label"], "day_a": A.get("reference_day"), "day_b": B.get("reference_day"),
           "bars_a": len(ma), "bars_b": len(mb), "common": len(common),
           "only_a": len(set(ma) - set(mb)), "only_b": len(set(mb) - set(ma))}
    if common:
        diffs = {f: sum(abs(ma[t][f] - mb[t][f]) > tick / 2 for t in common) for f in ("open", "high", "low", "close")}
        out["ohlc_mismatch_bars"] = diffs
        out["ohlc_identical_share"] = round(sum(all(abs(ma[t][f] - mb[t][f]) <= tick / 2 for f in ("open", "high", "low", "close"))
                                                for t in common) / len(common), 4)
        vr = [ma[t]["volume"] / mb[t]["volume"] for t in common if mb[t]["volume"]]
        out["volume_ratio_median"] = round(statistics.median(vr), 4) if vr else None
    ca, cb = A.get("conventions", {}), B.get("conventions", {})
    out["conventions"] = {k: [ca.get(k), cb.get(k)] for k in sorted(set(ca) | set(cb)) if k != "sample_1m"}
    out["max_days"] = {"a": A.get("max_days"), "b": B.get("max_days")}
    out["depth"] = {"a": A.get("depth"), "b": B.get("depth")}
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
