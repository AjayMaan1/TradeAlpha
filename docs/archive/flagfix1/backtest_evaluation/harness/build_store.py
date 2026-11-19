"""
Build a realistic Historify instance for the backtesting evaluation, using Historify's own Services/BarStore.

* 50 NSE equities (EQ00..EQ49) + NIFTY (NSE_INDEX)
* D  : 2015-01-01 .. 2024-12-31  (GBM with regimes; 2023-24 D derived from 1m with an "official close" = VWAP of
       the last 30 minutes, volume = 1m sum + auction/closing-session volume, like NSE)
* 1m : 2023-01-02 .. 2024-12-31  (09:15-15:29 IST, 375 bars/session, U-shaped volume)
* EQ07 has an UNADJUSTED 1:2 split on 2024-06-03 in both D and 1m (to test corporate-action handling)

    python build_store.py /tmp/eval/inst
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, "/workspace/historify-master/historify")
os.environ.setdefault("HISTORIFY_SECRET_BACKEND", "file")

from app.core.intervals import INTERVALS  # noqa: E402
from app.core.meta.models import Instrument, Watchlist, WatchlistItem  # noqa: E402
from app.core.services import Services  # noqa: E402
from app.core.timeutil import IST  # noqa: E402

N_SYM = 50
D_START, M_START, END = date(2015, 1, 1), date(2023, 1, 2), date(2024, 12, 31)
SPLIT_SYM, SPLIT_DAY = "EQ07", date(2024, 6, 3)


def trading_days(cal, a, b):
    return cal.trading_days("NSE", a, b)


def gen_symbol(rng, days_all, days_1m, p0):
    """Returns (daily_df, m1_df)."""
    n_all = len(days_all)
    # regime drift: persistent trends so trend/mean-reversion strategies have something to find
    regime = np.repeat(rng.normal(0, 0.0012, n_all // 60 + 1), 60)[:n_all]
    vol_d = rng.uniform(0.012, 0.025)
    m1_set = set(days_1m)
    # pre-1m period: daily GBM directly
    rows_d, m1_frames = [], []
    price = p0
    minutes = np.arange(375)
    u_shape = 1.0 + 2.5 * (np.exp(-minutes / 25) + np.exp(-(374 - minutes) / 25))
    for k, d in enumerate(days_all):
        gap = rng.normal(0, vol_d * 0.4)
        o = price * np.exp(gap)
        if d in m1_set:
            sig = vol_d / np.sqrt(375)
            r = rng.normal(regime[k] / 375, sig, 375)
            close_path = o * np.exp(np.cumsum(r))
            opens = np.r_[o, close_path[:-1]]
            wick = np.abs(rng.normal(0, sig * 0.6, (2, 375)))
            highs = np.maximum(opens, close_path) * (1 + wick[0])
            lows = np.minimum(opens, close_path) * (1 - wick[1])
            vol = (rng.gamma(2.0, 1.0, 375) * u_shape * 800).astype("int64") + 1
            t0 = int(datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST).timestamp())
            ts = t0 + minutes * 60
            m1 = pd.DataFrame({"ts": ts, "open": opens.round(2), "high": highs.round(2), "low": lows.round(2),
                               "close": close_path.round(2), "volume": vol})
            m1["high"] = m1[["open", "high", "close"]].max(axis=1)
            m1["low"] = m1[["open", "low", "close"]].min(axis=1)
            m1_frames.append(m1)
            last30 = m1.iloc[-30:]
            official = float((last30.close * last30.volume).sum() / last30.volume.sum())
            rows_d.append((d, m1.open.iloc[0], m1.high.max(), m1.low.min(), round(official, 2),
                           int(m1.volume.sum() * 1.06)))
            price = float(m1.close.iloc[-1])
        else:
            c = o * np.exp(rng.normal(regime[k], vol_d))
            h = max(o, c) * (1 + abs(rng.normal(0, vol_d * 0.5)))
            lo = min(o, c) * (1 - abs(rng.normal(0, vol_d * 0.5)))
            rows_d.append((d, round(o, 2), round(h, 2), round(lo, 2), round(c, 2),
                           int(rng.gamma(2.0, 1.0) * 300_000)))
            price = c
    daily = pd.DataFrame(rows_d, columns=["d", "open", "high", "low", "close", "volume"])
    daily["ts"] = [int(datetime(x.year, x.month, x.day, tzinfo=IST).timestamp()) for x in daily.d]
    daily = daily[["ts", "open", "high", "low", "close", "volume"]]
    m1 = pd.concat(m1_frames, ignore_index=True) if m1_frames else None
    return daily, m1


def apply_split(daily, m1, split_ts):
    """Unadjusted 1:2 split from split day: prices halve, volume doubles (what a raw broker feed shows)."""
    for df in (daily, m1):
        sel = df.ts >= split_ts
        for c in ("open", "high", "low", "close"):
            df.loc[sel, c] = (df.loc[sel, c] / 2).round(2)
        df.loc[sel, "volume"] = df.loc[sel, "volume"] * 2


def main(inst_path):
    t0 = time.perf_counter()
    sv = Services(inst_path)
    cal = sv.calendar()
    days_all = trading_days(cal, D_START, END)
    days_1m = trading_days(cal, M_START, END)
    rng = np.random.default_rng(42)
    with sv.meta.session() as s:
        wl = Watchlist(name="EVAL-50")
        s.add(wl)
        s.flush()
        syms = [(f"EQ{i:02d}", "NSE", "EQ") for i in range(N_SYM)] + [("NIFTY", "NSE_INDEX", "IDX")]
        ids = {}
        for n, (sym, ex, typ) in enumerate(syms):
            inst = Instrument(symbol=sym, exchange=ex, name=sym, instrument_type=typ, lot_size=1)
            s.add(inst)
            s.flush()
            ids[sym] = inst.id
            s.add(WatchlistItem(watchlist_id=wl.id, instrument_id=inst.id, position=n))
        s.commit()
    D, M1 = INTERVALS["D"].id, INTERVALS["1m"].id
    total = 0
    closes = []
    for sym, _ex, _t in syms[:N_SYM]:
        daily, m1 = gen_symbol(rng, days_all, days_1m, float(rng.uniform(80, 3000)))
        if sym == SPLIT_SYM:
            apply_split(daily, m1, int(datetime(SPLIT_DAY.year, SPLIT_DAY.month, SPLIT_DAY.day, tzinfo=IST).timestamp()))
        total += sv.store.upsert(ids[sym], D, daily)
        total += sv.store.upsert(ids[sym], M1, m1)
        closes.append(daily.set_index("ts").close / daily.close.iloc[0])
    # index = equal-weight of constituents (daily only)
    idx = pd.concat(closes, axis=1).mean(axis=1) * 10_000
    nifty = pd.DataFrame({"ts": idx.index, "open": idx.values, "high": idx.values * 1.004,
                          "low": idx.values * 0.996, "close": idx.values, "volume": 0})
    total += sv.store.upsert(ids["NIFTY"], D, nifty)
    sv.store.maintenance()
    print(f"rows={total:,} symbols={len(syms)} days={len(days_all)} 1m-days={len(days_1m)} "
          f"in {time.perf_counter() - t0:.1f}s -> {inst_path}")
    sv.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/eval/inst")
