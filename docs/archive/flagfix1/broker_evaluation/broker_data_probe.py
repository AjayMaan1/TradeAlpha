"""
Measure a broker's historical-data capabilities THROUGH OPENALGO (the path Historify actually uses). Read-only.

Run it once per shortlisted broker (point OpenAlgo at that broker, or run one OpenAlgo instance per broker):

    export OPENALGO_HOST=http://127.0.0.1:5000 OPENALGO_API_KEY=...          # never commit the key
    python broker_data_probe.py --label zerodha --out results/probe_zerodha.json
    python broker_data_probe.py --label dhan    --out results/probe_dhan.json \
        --future NIFTY28NOV24FUT:NFO --expired NIFTY26SEP24FUT:NFO          # optional F&O probes (your symbols)

What it measures (each with the exact requests made, so results are auditable):
  intervals          what OpenAlgo reports for this broker (/intervals)
  max_days           largest single-request window that is accepted AND complete, per interval (doubling + bisection)
  depth              earliest month with data, per interval (bisection over months; 1 small request per step)
  conventions        daily timestamp convention; first 1m bar time; 1m bars per full session;
                     daily close vs last 1m close (official close vs last trade); 1m volume sum vs daily volume
  oi / expired       OI column present for a live future; any data for an expired contract
  latency            median / p95 request latency
It paces itself (default 1 request/s, well under every published limit) and stops a probe on the first auth error.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from datetime import date, datetime, timedelta, timezone

import httpx

IST = timezone(timedelta(hours=5, minutes=30))


class Probe:
    def __init__(self, host, api_key, pace_s=1.0, timeout=60):
        self.c = httpx.Client(base_url=f"{host.rstrip('/')}/api/v1/", timeout=timeout)
        self.key, self.pace, self.last = api_key, pace_s, 0.0
        self.log, self.lat, self.rate_limited = [], [], 0

    def call(self, endpoint, **payload):
        """One logical request. Rate-limit answers (HTTP 429 / 'rate' messages) are NEVER taken as a data answer:
        back off exponentially and retry, and count them (rate_limited) - they are a finding of their own."""
        for attempt in range(8):
            wait = self.pace - (time.monotonic() - self.last)
            if wait > 0:
                time.sleep(wait)
            t0 = time.monotonic()
            try:
                r = self.c.post(endpoint, json={"apikey": self.key, **payload})
                ctype = r.headers.get("content-type", "")
                body = r.json() if ctype.startswith("application/json") else {"raw": r.text[:300]}
                status = r.status_code
            except httpx.HTTPError as e:
                body, status = {"status": "error", "message": f"{type(e).__name__}: {e}"}, 0
            self.last = time.monotonic()
            self.lat.append(self.last - t0)
            msg = str(body.get("message") or "")
            if status == 429 or "rate" in msg.lower() and "exceed" in msg.lower():
                self.rate_limited += 1
                time.sleep(min(30.0, 0.5 * 2 ** attempt))
                continue
            if status >= 500 or status == 0:
                time.sleep(min(30.0, 0.5 * 2 ** attempt))
                if attempt < 3:
                    continue
            break
        ok = status == 200 and body.get("status") == "success"
        data = body.get("data") if ok else None
        entry = {"endpoint": endpoint, **payload, "http": status, "ok": ok, "attempts": attempt + 1,
                 "rows": len(data) if isinstance(data, list) else None, "message": None if ok else msg[:200] or str(body)[:200]}
        self.log.append(entry)
        if status in (401, 403) or "login" in str(entry["message"]).lower():
            raise PermissionError(f"auth error: {entry['message']}")
        return ok, data, entry

    def history(self, symbol, exchange, interval, start: date, end: date):
        return self.call("history", symbol=symbol, exchange=exchange, interval=interval,
                         start_date=start.isoformat(), end_date=end.isoformat())


def weekdays(a: date, b: date) -> int:
    return sum(1 for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5)


def bars_per_day(interval: str) -> float:
    m = {"1m": 375, "3m": 125, "5m": 75, "10m": 38, "15m": 25, "30m": 13, "1h": 7, "D": 1}
    return m.get(interval, 1)


def complete(rows, interval, a, b, tol=0.85):
    """Accepted AND roughly complete (>= tol of the weekday-based expectation; holidays explain the rest)."""
    exp = weekdays(a, b) * bars_per_day(interval)
    return rows is not None and exp > 0 and rows >= tol * exp


def probe_max_days(p: Probe, sym, ex, interval, end: date, limit=4000):
    lo, hi, d = 0, None, 1
    while d <= limit:
        ok, data, _ = p.history(sym, ex, interval, end - timedelta(days=d - 1), end)
        if ok and complete(len(data or []), interval, end - timedelta(days=d - 1), end):
            lo, d = d, d * 2
        else:
            hi = d
            break
    if hi is None:
        return {"max_days": f">= {lo}", "method": "doubling reached the probe limit"}
    while hi - lo > max(1, lo // 20):                  # bisection to ~5 % precision
        mid = (lo + hi) // 2
        ok, data, _ = p.history(sym, ex, interval, end - timedelta(days=mid - 1), end)
        if ok and complete(len(data or []), interval, end - timedelta(days=mid - 1), end):
            lo = mid
        else:
            hi = mid
    return {"max_days": lo, "first_failing_days": hi}


def probe_depth(p: Probe, sym, ex, interval, today: date, max_years=25):
    """Earliest month with any data: bisection over month offsets, one ~5-trading-day request per step."""
    def has(months_back):
        y, m = divmod((today.year * 12 + today.month - 1) - months_back, 12)
        a = date(y, m + 1, 1)
        ok, data, _ = p.history(sym, ex, interval, a, a + timedelta(days=8))
        return ok and bool(data)
    lo, hi = 0, max_years * 12
    if has(hi):
        return {"earliest_month": f"<= {max_years} years back"}
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if has(mid) else (lo, mid)
    y, m = divmod((today.year * 12 + today.month - 1) - lo, 12)
    return {"earliest_month": f"{y}-{m + 1:02d}", "years": round(lo / 12, 1)}


def to_rows(data):
    out = []
    for r in data or []:
        ts = r.get("timestamp", r.get("time"))
        if isinstance(ts, str):
            ts = int(datetime.fromisoformat(ts).timestamp())
        out.append({"ts": int(ts), "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
                    "close": float(r["close"]), "volume": int(float(r.get("volume") or 0)), "oi": r.get("oi")})
    return out


def conventions(p: Probe, sym, ex, day: date):
    ok_d, d, _ = p.history(sym, ex, "D", day - timedelta(days=10), day)
    ok_m, m, _ = p.history(sym, ex, "1m", day, day)
    out = {"day": day.isoformat()}
    if ok_d and d:
        dr = to_rows(d)
        out["daily_ts_convention"] = sorted({("ist_midnight" if (x["ts"] + 19800) % 86400 == 0 else
                                              "utc_midnight" if x["ts"] % 86400 == 0 else "other") for x in dr})
        dd = [x for x in dr if datetime.fromtimestamp(x["ts"], IST).date() == day]
    else:
        dd = []
    if ok_m and m:
        mr = sorted(to_rows(m), key=lambda x: x["ts"])
        out["first_1m_bar_ist"] = datetime.fromtimestamp(mr[0]["ts"], IST).strftime("%H:%M")
        out["last_1m_bar_ist"] = datetime.fromtimestamp(mr[-1]["ts"], IST).strftime("%H:%M")
        out["bars_1m"] = len(mr)
        if dd:
            D = dd[0]
            out["daily_close_vs_last_1m_close_bp"] = round((D["close"] / mr[-1]["close"] - 1) * 1e4, 2)
            out["daily_open_vs_first_1m_open_bp"] = round((D["open"] / mr[0]["open"] - 1) * 1e4, 2)
            out["sum_1m_volume_over_daily_volume"] = round(sum(x["volume"] for x in mr) / max(1, D["volume"]), 4)
        out["sample_1m"] = mr                           # kept for cross-broker comparison
    return out


def probe_finality(p: Probe, sym, ex, offsets, bars, settle_s, shift_s=0.0):
    """How long after a 1m bar ENDS does the broker stop changing it? For `bars` consecutive bars: fetch the bar at
    each offset after its end, then once more `settle_s` later (the reference). Earliest offset from which every
    observation equals the reference = the finality this broker needs (set `intraday_final_buffer_s` above it)."""
    def now():
        return time.time() + shift_s
    out = []
    for _ in range(bars):
        bar_end = (int(now()) // 60 + 1) * 60
        seen = {}
        for off in list(offsets) + [settle_s]:
            wait = bar_end + off - now()
            if wait > 0:
                time.sleep(wait)
            d = datetime.fromtimestamp(bar_end, IST).date()
            ok, data, _ = p.history(sym, ex, "1m", d, d)
            row = next((r for r in to_rows(data) if r["ts"] == bar_end - 60), None) if ok else None
            seen[off] = None if row is None else (row["open"], row["high"], row["low"], row["close"], row["volume"])
        final = seen.pop(settle_s)
        stable_from = None
        for off in sorted(seen):
            if all(seen[o] == final for o in sorted(seen) if o >= off):
                stable_from = off
                break
        out.append({"bar_end": datetime.fromtimestamp(bar_end, IST).isoformat(), "stable_from_s": stable_from,
                    "observations": {str(k): v == final if v else None for k, v in seen.items()}})
    worst = [x["stable_from_s"] for x in out]
    rec = None if any(w is None for w in worst) else max(worst) + 10
    return {"bars": out, "recommended_intraday_final_buffer_s": rec,
            "note": "null = not stable within the probed offsets: probe longer offsets"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--host", default=os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000"))
    ap.add_argument("--symbol", default="RELIANCE:NSE", help="liquid equity SYMBOL:EXCHANGE")
    ap.add_argument("--intervals", default="1m,5m,15m,1h,D")
    ap.add_argument("--future", help="a LIVE future SYMBOL:EXCHANGE (OI check)")
    ap.add_argument("--expired", help="an EXPIRED contract SYMBOL:EXCHANGE (expired-history check)")
    ap.add_argument("--pace", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--today", default=None, help="override 'today' (YYYY-MM-DD), e.g. for the mock")
    ap.add_argument("--finality", type=int, default=0, metavar="BARS",
                    help="during market hours: measure candle finality over BARS consecutive 1m bars")
    ap.add_argument("--finality-offsets", default="5,15,30,60,90,120", help="seconds after bar end to observe")
    ap.add_argument("--finality-settle", type=float, default=300, help="reference observation, seconds after bar end")
    ap.add_argument("--clock-shift-s", type=float, default=0.0, help="testing only: shift the probe clock")
    a = ap.parse_args()
    key = os.getenv("OPENALGO_API_KEY")
    if not key:
        raise SystemExit("set OPENALGO_API_KEY")
    p = Probe(a.host, key, a.pace)
    today = date.fromisoformat(a.today) if a.today else datetime.now(IST).date()
    ref = today - timedelta(days=7)
    while ref.weekday() >= 5:
        ref -= timedelta(days=1)
    sym, ex = a.symbol.split(":")
    res = {"label": a.label, "host": a.host, "probed_at": datetime.now(IST).isoformat(), "symbol": a.symbol,
           "reference_day": ref.isoformat()}
    if a.finality:
        offs = [float(x) for x in a.finality_offsets.split(",")]
        res["finality"] = probe_finality(p, sym, ex, offs, a.finality, a.finality_settle, a.clock_shift_s)
        res["requests"] = len(p.log)
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w") as f:
            json.dump(res, f, indent=1, default=str)
        print(json.dumps(res["finality"], indent=1, default=str))
        return
    try:
        ok, data, _ = p.call("intervals")
        res["intervals_reported"] = data
        res["max_days"], res["depth"] = {}, {}
        for iv in a.intervals.split(","):
            res["max_days"][iv] = probe_max_days(p, sym, ex, iv, ref)
            res["depth"][iv] = probe_depth(p, sym, ex, iv, today)
        res["conventions"] = conventions(p, sym, ex, ref)
        if a.future:
            fs, fe = a.future.split(":")
            ok, data, _ = p.history(fs, fe, "5m", ref - timedelta(days=3), ref)
            rows = to_rows(data) if ok else []
            res["oi"] = {"symbol": a.future, "rows": len(rows), "oi_present": any(r["oi"] not in (None, 0) for r in rows)}
        if a.expired:
            es, ee = a.expired.split(":")
            ok, data, e = p.history(es, ee, "D", today - timedelta(days=400), today)
            res["expired"] = {"symbol": a.expired, "rows": len(data or []) if ok else 0, "message": e["message"]}
    except PermissionError as e:
        res["aborted"] = str(e)
    res["requests"] = len(p.log)
    res["rate_limited_responses"] = p.rate_limited
    res["latency_s"] = {"median": round(statistics.median(p.lat), 3), "p95": round(sorted(p.lat)[int(0.95 * (len(p.lat) - 1))], 3)} if p.lat else None
    res["request_log"] = p.log
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1, default=str)
    summary = {k: res.get(k) for k in ("label", "max_days", "depth", "oi", "expired", "requests", "rate_limited_responses", "latency_s", "aborted")}
    summary["conventions"] = {k: v for k, v in res.get("conventions", {}).items() if k != "sample_1m"}
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
