"""
Revision detection: a FINAL stored bar whose values change on a later download (broker correction, late volume
update, split/bonus adjustment). The old values are archived before the new ones are written, so a backtest on the
old data can always be reproduced.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from tradealpha.marketdata.integrity.hashing import trade_days

_PRICE = ["open", "high", "low", "close"]


@dataclass
class DayRevision:
    trade_date: date
    kind: str                     # correction | late_update | probable_adjustment
    bars_changed: int
    factor: float | None
    old_rows: pd.DataFrame        # the stored bars that are about to be overwritten


def detect(old: pd.DataFrame, new: pd.DataFrame, final_until_ts: int | None, latest_final_day: date | None
           ) -> list[DayRevision]:
    """Compare stored (old) and incoming (new) bars on common timestamps that were already FINAL."""
    if old.empty or new.empty or final_until_ts is None:
        return []
    m = old.merge(new, on="ts", suffixes=("_o", "_n"))
    m = m[m["ts"] <= final_until_ts]
    if m.empty:
        return []
    diff = np.zeros(len(m), dtype=bool)
    for c in _PRICE:
        diff |= ~np.isclose(m[f"{c}_o"].to_numpy("float64"), m[f"{c}_n"].to_numpy("float64"), rtol=0, atol=1e-6)
    diff |= m["volume_o"].to_numpy("int64") != m["volume_n"].to_numpy("int64")
    changed = m[diff]
    if changed.empty:
        return []
    ratio = changed["close_n"].to_numpy("float64") / changed["close_o"].to_numpy("float64")
    days = trade_days(changed["ts"].to_numpy("int64"))
    price_changed = ~np.isclose(ratio, 1.0, rtol=0, atol=2e-3)
    adjustment = None
    if price_changed.sum() >= 3 and len(np.unique(days[price_changed])) >= 3:
        r = ratio[price_changed]
        if float(np.std(r) / np.mean(r)) < 1e-3:
            adjustment = round(float(np.median(r)), 6)              # consistent factor across days -> split/bonus
    out = []
    epoch = date(1970, 1, 1)
    old_cols = ["ts", *_PRICE, "volume", "oi"]
    for d in np.unique(days):
        sel = days == d
        day = date.fromordinal(epoch.toordinal() + int(d))
        if adjustment is not None:
            kind = "probable_adjustment"
        elif latest_final_day is not None and day >= latest_final_day:
            kind = "late_update"                                    # e.g. closing-session volume on the last day
        else:
            kind = "correction"
        rows = old[old["ts"].isin(changed["ts"][sel])][old_cols].reset_index(drop=True)
        out.append(DayRevision(day, kind, int(sel.sum()), adjustment, rows))
    return out
