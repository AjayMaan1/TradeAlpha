"""
Candle roll-up policy (decision: hybrid).

* D is always the broker's daily candle (official close, auction/closing-session volume); W and M roll up from D.
* With ``intraday_source = rollup_1m`` the intraday intervals 3m..1h are rolled up from the stored 1m bars with
  buckets anchored at each session's open (09:15 NSE, 09:00 MCX/CDS, special sessions from the calendar); only 1m is
  downloaded. With ``broker`` (default) every interval is the broker's own candle series.
* Every series served carries its provenance: broker | derived_from_1m | derived_from_D.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from tradealpha.platform.time.calendar import MarketCalendar
from tradealpha.platform.time.intervals import INTERVALS, IntervalSpec, resolve
from tradealpha.platform.time.timeutil import IST

ROLLUP_CODES = ("3m", "5m", "10m", "15m", "30m", "1h")
_EPOCH = date(1970, 1, 1)


def is_rolled_up(code: str, cfg) -> bool:
    return bool(getattr(cfg, "rollup", False)) and resolve(code).code in ROLLUP_CODES


def download_spec(code: str, cfg) -> IntervalSpec:
    """The interval that must be DOWNLOADED to serve ``code`` (1m for rolled-up intervals)."""
    spec = resolve(code)
    return INTERVALS["1m"] if is_rolled_up(spec.code, cfg) else spec


def provenance(code: str, cfg) -> str:
    spec = resolve(code)
    if spec.code in ("W", "M"):
        return "derived_from_D"
    return "derived_from_1m" if is_rolled_up(spec.code, cfg) else "broker"


def session_opens(cal: MarketCalendar, exchanges, start: date, end: date) -> pd.DataFrame:
    """Columns exchange, day (IST day number), open_ts for every trading day of the exchanges in [start, end]."""
    rows = []
    for ex in sorted(set(exchanges)):
        for d in cal.trading_days(ex, start, end):
            s = cal.session_on(ex, d)
            rows.append((ex, (d - _EPOCH).days, int(datetime.combine(d, s.open, IST).timestamp())))
    return pd.DataFrame(rows, columns=["exchange", "day", "open_ts"]).astype({"day": "int64", "open_ts": "int64"})


def opens_for_range(cal: MarketCalendar, exchange: str, ts_from: int | None, ts_to: int | None,
                    fallback_days: int = 3700) -> pd.DataFrame:
    hi = datetime.fromtimestamp(ts_to, IST).date() if ts_to is not None else datetime.now(IST).date()
    lo = datetime.fromtimestamp(ts_from, IST).date() if ts_from is not None else hi - timedelta(days=fallback_days)
    return session_opens(cal, [exchange], lo, hi)


def rollup_frame(m1: pd.DataFrame, code: str, opens: pd.DataFrame) -> pd.DataFrame:
    """Pure pandas roll-up of 1m bars to ``code`` with session-anchored buckets (reference implementation, also
    used by the roll-up audit). ``opens``: day (IST day number), open_ts."""
    from tradealpha.marketdata.providers.normalize import BAR_COLUMNS
    from tradealpha.platform.time.timeutil import IST_OFFSET_S

    sec = INTERVALS[code].seconds
    if m1.empty:
        return m1[BAR_COLUMNS].copy() if set(BAR_COLUMNS) <= set(m1.columns) else m1
    o = ((m1["ts"] + IST_OFFSET_S) // 86400).map(opens.set_index("day")["open_ts"])
    df = m1[o.notna() & (m1["ts"] >= o.fillna(0))].copy()
    o = o[df.index].astype("int64")
    df["bucket"] = o + ((df["ts"] - o) // sec) * sec
    g = df.sort_values("ts").groupby("bucket", sort=True)
    out = pd.DataFrame({"ts": g["bucket"].first(), "open": g["open"].first(), "high": g["high"].max(),
                        "low": g["low"].min(), "close": g["close"].last(), "volume": g["volume"].sum(),
                        "oi": g["oi"].last() if "oi" in df else pd.NA})
    return out.reset_index(drop=True)[BAR_COLUMNS]
