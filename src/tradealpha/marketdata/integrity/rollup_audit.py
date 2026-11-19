"""
Roll-up audit: are the broker's 3m..1h / D candles equal to a session-anchored roll-up of its own 1m candles?

Downloads (read-only, nothing is stored) the last N completed sessions of 1m, the selected intraday intervals and D
for a sample of instruments, rolls 1m up, and compares bar by bar: per field (O/H/L/C/V), per bar position in the
session (first / middle / last, which is often partial), per instrument. Also probes how far back 1m history goes.
The report ends with a recommendation for Settings → Intraday source.

About 200 history requests for the default sample (12 instruments x 6-7 intervals + depth probe), paced by the
broker rate limiter like every other download.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from tradealpha.marketdata.bars.rollup import ROLLUP_CODES, rollup_frame, session_opens
from tradealpha.marketdata.providers.errors import ProviderError
from tradealpha.marketdata.providers.factory import make_provider
from tradealpha.marketdata.providers.normalize import normalize_bars
from tradealpha.marketdata.providers.openalgo.client import chunk_ranges
from tradealpha.platform.time.intervals import INTERVALS
from tradealpha.platform.time.timeutil import IST, IST_OFFSET_S

log = logging.getLogger(__name__)
DEFAULT_SAMPLE = [("RELIANCE", "NSE"), ("SBIN", "NSE"), ("TCS", "NSE"), ("INFY", "NSE"), ("HDFCBANK", "NSE"),
                  ("ICICIBANK", "NSE"), ("ITC", "NSE"), ("LT", "NSE"), ("AXISBANK", "NSE"), ("IDEA", "NSE"),
                  ("NIFTY", "NSE_INDEX"), ("BANKNIFTY", "NSE_INDEX")]
_EPOCH = date(1970, 1, 1)


def _fetch(prov, sym, ex, code, start, end, max_days) -> pd.DataFrame:
    spec = INTERVALS[code]
    frames = [prov.history_raw(sym, ex, spec, a, b) for a, b in chunk_ranges(start, end, max_days)]
    raw = pd.concat([f for f in frames if len(f)], ignore_index=True) if any(len(f) for f in frames) else frames[0]
    return normalize_bars(raw, spec)[0]


def _window(cal, exchange, now_ist: datetime, sessions: int) -> tuple[date, date]:
    d = now_ist.date()
    while not (cal.session_close_dt(exchange, d) and cal.session_close_dt(exchange, d) + timedelta(minutes=15)
               <= now_ist):
        d -= timedelta(days=1)
    end, n, cur = d, 1, d
    while n < sessions:
        cur -= timedelta(days=1)
        if cal.is_trading_day(exchange, cur):
            n += 1
    return cur, end


def compare(broker: pd.DataFrame, rolled: pd.DataFrame, code: str, opens: pd.DataFrame, cal, exchange) -> dict:
    sec = INTERVALS[code].seconds
    m = broker.merge(rolled, on="ts", how="outer", suffixes=("_b", "_r"), indicator=True)
    both = m[m["_merge"] == "both"].copy()
    out = {"bars_broker": int(len(broker)), "bars_rollup": int(len(rolled)), "compared": int(len(both)),
           "only_broker": int((m["_merge"] == "left_only").sum()), "only_rollup": int((m["_merge"] == "right_only").sum())}
    if both.empty:
        return {**out, "match_pct": {}, "ohlc_match_pct": None, "all_fields_match_pct": None, "by_position": {}}
    eq = {c: np.isclose(both[f"{c}_b"].to_numpy("float64"), both[f"{c}_r"].to_numpy("float64"), rtol=0, atol=1e-6)
          for c in ("open", "high", "low", "close")}
    eq["volume"] = both["volume_b"].to_numpy("int64") == both["volume_r"].to_numpy("int64")
    ohlc = eq["open"] & eq["high"] & eq["low"] & eq["close"]
    allf = ohlc & eq["volume"]
    day = (both["ts"] + IST_OFFSET_S) // 86400
    op = day.map(opens.set_index("day")["open_ts"]).to_numpy("float64")
    k = ((both["ts"].to_numpy("int64") - op) // sec).astype("int64")
    last = np.array([-(-cal.session_on(exchange, _EPOCH + timedelta(days=int(d))).minutes() * 60 // sec) - 1
                     for d in day])
    pos = np.where(k == 0, "first", np.where(k == last, "last", "middle"))
    pct = lambda a: round(100.0 * float(a.mean()), 3) if len(a) else None       # noqa: E731
    out.update({"match_pct": {c: pct(v) for c, v in eq.items()}, "ohlc_match_pct": pct(ohlc),
                "all_fields_match_pct": pct(allf),
                "by_position": {p: {"compared": int((pos == p).sum()), "all_fields_match_pct": pct(allf[pos == p])}
                                for p in ("first", "middle", "last")}})
    bad = both[~allf].head(5)
    out["examples"] = [{"ts": int(r.ts), "ist": datetime.fromtimestamp(int(r.ts), IST).strftime("%Y-%m-%d %H:%M"),
                        "broker": [r.open_b, r.high_b, r.low_b, r.close_b, int(r.volume_b)],
                        "rollup": [r.open_r, r.high_r, r.low_r, r.close_r, int(r.volume_r)]}
                       for r in bad.itertuples(index=False)]
    return out


def compare_daily(d: pd.DataFrame, m1: pd.DataFrame) -> dict:
    if d.empty or m1.empty:
        return {"days": 0}
    g = m1.assign(day=(m1["ts"] + IST_OFFSET_S) // 86400).sort_values("ts").groupby("day")
    r = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "volume": g["volume"].sum()})
    dd = d.assign(day=(d["ts"] + IST_OFFSET_S) // 86400).set_index("day")
    j = dd.join(r.add_suffix("_r"), how="inner")
    if j.empty:
        return {"days": 0}
    bps = (j["close"] - j["close_r"]).abs() / j["close"] * 1e4
    vol = j["volume_r"] / j["volume"].replace(0, np.nan)
    return {"days": int(len(j)),
            "close_equal_pct": round(100.0 * float(np.isclose(j["close"], j["close_r"], atol=1e-6).mean()), 2),
            "close_diff_bps_median": round(float(bps.median()), 2), "close_diff_bps_p95": round(float(bps.quantile(.95)), 2),
            "open_equal_pct": round(100.0 * float(np.isclose(j["open"], j["open_r"], atol=1e-6).mean()), 2),
            "high_equal_pct": round(100.0 * float(np.isclose(j["high"], j["high_r"], atol=1e-6).mean()), 2),
            "low_equal_pct": round(100.0 * float(np.isclose(j["low"], j["low_r"], atol=1e-6).mean()), 2),
            "volume_1m_sum_over_daily_median": round(float(vol.median()), 4) if vol.notna().any() else None}


def depth_probe(prov, sym, ex, today: date, years=(1, 2, 3, 5, 7, 10)) -> dict:
    """How far back does the broker serve 1m bars? One small request per probe year."""
    out = {}
    for y in years:
        d = today - timedelta(days=365 * y)
        try:
            bars = prov.history_raw(sym, ex, INTERVALS["1m"], d, d + timedelta(days=6))
            out[f"{y}y"] = int(len(bars))
        except ProviderError as e:
            out[f"{y}y"] = f"error: {e.kind.value}"
    reach = [int(k[:-1]) for k, v in out.items() if isinstance(v, int) and v > 0]
    return {"symbol": f"{sym}:{ex}", "bars_in_probe_week": out, "oldest_year_with_data": max(reach) if reach else 0}


def run_audit(prov, cal, instruments, sessions: int, intervals, now_ist: datetime, max_days, probe=True,
              progress=None) -> dict:
    per_inst, totals = [], {c: [] for c in intervals}
    daily = []
    for n, (sym, ex) in enumerate(instruments):
        start, end = _window(cal, ex, now_ist, sessions)
        opens = session_opens(cal, [ex], start, end)
        row = {"symbol": sym, "exchange": ex, "from": start.isoformat(), "to": end.isoformat(), "intervals": {}}
        try:
            m1 = _fetch(prov, sym, ex, "1m", start, end, max_days("1m"))
            for code in intervals:
                broker = _fetch(prov, sym, ex, code, start, end, max_days(code))
                res = compare(broker, rollup_frame(m1, code, opens), code, opens, cal, ex)
                row["intervals"][code] = res
                totals[code].append(res)
            dd = _fetch(prov, sym, ex, "D", start, end, max_days("D"))
            row["daily"] = compare_daily(dd, m1)
            daily.append(row["daily"])
        except ProviderError as e:
            row["error"] = f"{e.kind.value}: {e.message}"
        per_inst.append(row)
        if progress:
            progress(n + 1, len(instruments))
    summary = {}
    def weighted(lst, key, comp):
        return round(sum((x.get(key) or 0) * x["compared"] for x in lst) / comp, 3) if comp else None

    for code, lst in totals.items():
        comp = sum(x["compared"] for x in lst)
        summary[code] = {"compared": comp, "ohlc_match_pct": weighted(lst, "ohlc_match_pct", comp),
                         "all_fields_match_pct": weighted(lst, "all_fields_match_pct", comp),
                         "only_broker": sum(x["only_broker"] for x in lst),
                         "only_rollup": sum(x["only_rollup"] for x in lst)}
    valid_daily = [d for d in daily if d.get("days")]
    dsum = {"days": sum(d["days"] for d in valid_daily),
            "close_equal_pct": round(float(np.mean([d["close_equal_pct"] for d in valid_daily])), 2) if valid_daily else None,
            "close_diff_bps_median": round(float(np.median([d["close_diff_bps_median"] for d in valid_daily])), 2)
            if valid_daily else None}
    report = {"generated_at": datetime.now(UTC).isoformat(), "sessions": sessions, "intervals": list(intervals),
              "instruments": per_inst, "summary": summary, "daily": dsum}
    if probe and instruments:
        sym, ex = instruments[0]
        report["depth_probe"] = depth_probe(prov, sym, ex, now_ist.date())
    report["recommendation"] = recommend(summary, dsum)
    return report


def recommend(summary: dict, daily: dict) -> dict:
    ohlc = [v["ohlc_match_pct"] for v in summary.values() if v["ohlc_match_pct"] is not None]
    allf = [v["all_fields_match_pct"] for v in summary.values() if v["all_fields_match_pct"] is not None]
    extra = sum(v["only_broker"] + v["only_rollup"] for v in summary.values())
    compared = sum(v["compared"] for v in summary.values()) or 1
    if not ohlc:
        verdict, text = "inconclusive", "No bars could be compared (no data / errors). Check the connection."
    elif min(ohlc) >= 99.9 and extra / compared < 0.001:
        verdict = "rollup_1m"
        text = ("Broker intraday candles equal the session-anchored roll-up of its 1m candles "
                f"(OHLC {min(ohlc)}%+, volume {min(allf)}%+). Switch Settings → Intraday source to '1m roll-up'.")
    elif min(ohlc) >= 99.0:
        verdict = "rollup_1m_with_caveats"
        text = (f"Roll-up matches {min(ohlc)}%+ of bars (see by_position/examples for the differences). Roll-up is "
                "still recommended for multi-timeframe backtests; keep broker candles if you need exact parity "
                "with what the broker shows live.")
    else:
        verdict = "broker"
        text = (f"Only {min(ohlc)}% of bars match - the broker builds intraday candles differently. Keep "
                "'broker candles'; use roll-ups only as a separate, labelled series.")
    dtext = ("Daily close differs from the last 1m close as expected (official closing price): keep D from the "
             "broker." if daily.get("close_equal_pct") is not None and daily["close_equal_pct"] < 99 else
             "Daily bars equal the 1m roll-up in this sample (the broker may use the last trade as close).")
    return {"intraday_source": verdict, "text": text, "daily": dtext}


def to_markdown(rep: dict) -> str:
    lines = [f"# Roll-up audit - {rep['generated_at']}", "",
             f"**Recommendation:** `{rep['recommendation']['intraday_source']}` - {rep['recommendation']['text']}",
             "", f"**Daily:** {rep['recommendation']['daily']}", "",
             "| Interval | Bars compared | OHLC match | All fields (incl. volume) | Only broker | Only roll-up |",
             "|---|---|---|---|---|---|"]
    for code, v in rep["summary"].items():
        lines.append(f"| {code} | {v['compared']:,} | {v['ohlc_match_pct']}% | {v['all_fields_match_pct']}% | "
                     f"{v['only_broker']} | {v['only_rollup']} |")
    d = rep["daily"]
    lines += ["", f"Daily (D vs roll-up of 1m): {d['days']} days, close equal {d['close_equal_pct']}%, median "
              f"difference {d['close_diff_bps_median']} bps", ""]
    if rep.get("depth_probe"):
        p = rep["depth_probe"]
        lines.append(f"1m history depth ({p['symbol']}): oldest probed year with data = {p['oldest_year_with_data']} "
                     f"({p['bars_in_probe_week']})")
    lines += ["", "Per-instrument details (positions, examples) are in the JSON report."]
    return "\n".join(lines) + "\n"


def instruments_for(sv, symbols: list[str] | None, watchlist_id: int | None) -> list[tuple[str, str]]:
    if symbols:
        out = []
        for x in symbols:
            ex, _, sym = x.partition(":") if ":" in x else ("NSE", "", x)
            out.append((sym.strip().upper(), ex.strip().upper()))
        return out
    if watchlist_id:
        from sqlalchemy import select

        from tradealpha.platform.db.models import Instrument, WatchlistItem
        with sv.meta.session() as s:
            rows = s.execute(select(Instrument.symbol, Instrument.exchange).join(
                WatchlistItem, WatchlistItem.instrument_id == Instrument.id).where(
                WatchlistItem.watchlist_id == int(watchlist_id)).limit(30)).all()
        return [(a, b) for a, b in rows]
    return list(DEFAULT_SAMPLE)


def run_job(sv, job_id: str, params: dict):
    cfg = sv.config()
    if not cfg.configured:
        raise RuntimeError("OpenAlgo is not configured")
    insts = instruments_for(sv, params.get("symbols"), params.get("watchlist_id"))
    intervals = [c for c in (params.get("intervals") or ROLLUP_CODES) if c in ROLLUP_CODES]
    prov = make_provider(cfg, timeout_s=cfg.http_timeout_s, max_retries=3)
    try:
        rep = run_audit(prov, sv.calendar(), insts, int(params.get("sessions", 20)), intervals,
                        sv.clock.now().astimezone(IST), cfg.max_days, probe=params.get("probe", True))
    finally:
        prov.close()
    folder = os.path.join(sv.instance_path, "audit_reports")
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    base = os.path.join(folder, f"rollup-audit-{stamp}")
    with open(base + ".json", "w") as f:
        json.dump(rep, f, indent=2, default=str)
    with open(base + ".md", "w") as f:
        f.write(to_markdown(rep))
    short = {"recommendation": rep["recommendation"], "summary": rep["summary"], "daily": rep["daily"],
             "depth_probe": rep.get("depth_probe"), "report": os.path.basename(base) + ".md",
             "instruments": len(insts), "generated_at": rep["generated_at"]}
    sv.settings.set("last_rollup_audit", json.dumps(short, default=str))
    return short, 0
