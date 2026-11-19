"""
Broker cost-vs-data comparison calculator.

Inputs (edit them; every value carries a confidence code):
    broker_tariffs.toml      per-broker brokerage, subscriptions, DP, data limits, API capabilities
    statutory_charges.toml   STT, stamp, exchange, SEBI, GST: identical for all brokers, versioned by date
    scenarios.toml           your trading profiles + the data-backfill target

Outputs (markdown, in results/):
    cost_by_scenario.md      monthly broker-dependent cost per scenario (+ statutory cost shown once)
    data_backfill.md         requests and time to backfill the research universe, and to run the daily sync
    capability_matrix.md     the API capabilities Historify Lab needs, with confidence codes

    python broker_compare.py [--tariffs broker_tariffs.toml] [--out results]
"""
from __future__ import annotations

import argparse
import math
import os
import tomllib
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    with open(os.path.join(HERE, name), "rb") as f:
        return tomllib.load(f)


def brokerage(leg: dict, value: float) -> float:
    if "flat" in leg:
        b = float(leg["flat"])
        if "pct" in leg:
            b = min(b, leg["pct"] * value)
    else:
        b = leg.get("pct", 0.0) * value
        if "cap" in leg:
            b = min(b, float(leg["cap"]))
    if "min" in leg and b > 0:
        b = max(b, float(leg["min"]))
    return b


def statutory(stat: dict, segment: str, value: float, on: date) -> dict:
    versions = sorted(stat[segment], key=lambda v: v["effective_from"])
    v = [x for x in versions if date.fromisoformat(x["effective_from"]) <= on][-1]
    gst = stat["gst"]
    buy = value * (v["stt_buy"] + v["stamp_buy"] + (v["exch_txn"] + stat["sebi_fee"]) * (1 + gst))
    sell = value * (v["stt_sell"] + (v["exch_txn"] + stat["sebi_fee"]) * (1 + gst))
    return {"buy": buy, "sell": sell, "round_trip": buy + sell, "schedule": v["effective_from"]}


def conf(b: dict, key: str) -> str:
    return b.get("conf", {}).get(key, "H?" if key == "statutory" else "?")


def fmt_inr(x):
    return "?" if x is None else f"₹{x:,.0f}"


def cost_tables(brokers, stat, sc) -> str:
    days = sc["trading_days_per_month"]
    on = date.fromisoformat(sc["as_of"])
    out = ["# Monthly cost by trading scenario (broker-dependent part)", "",
           f"Statutory schedule in force on {on} (same for every broker). All amounts include GST. Confidence of each "
           "broker's tariff in brackets (V verified in SDK · H/M/L assistant knowledge · ? unknown). **Verify before "
           "deciding.** Regenerate after editing `broker_tariffs.toml`.", ""]
    for s in sc["scenario"]:
        rt = s.get("round_trips_per_month", s.get("round_trips_per_day", 0) * days)
        v = s["value_per_leg"]
        st = statutory(stat, s["segment"], v, on)
        out += [f"## {s['id']} - {s['name']}", "",
                f"{rt:,.0f} round trips/month ({2 * rt:,.0f} executed orders), turnover ₹{2 * rt * v / 1e7:,.2f} cr/month. "
                f"**Statutory + exchange charges (all brokers): {fmt_inr(st['round_trip'] * rt)}/month** "
                f"(₹{st['round_trip']:,.2f} per round trip, schedule {st['schedule']}).", "",
                "| Broker | Brokerage/order | Brokerage + GST /month | DP /month | Subscriptions /month | **Broker-dependent total** | Total incl. statutory | Per round trip | Tariff conf. |",
                "|---|---|---|---|---|---|---|---|---|"]
        rows = []
        for key, b in brokers.items():
            leg = b["brokerage"][s["leg"]]
            per_order = brokerage(leg, v)
            brk = per_order * 2 * rt * 1.18
            dp = b.get("dp_charge", 0) * s.get("dp_sell_days_per_month", 0) * 1.18
            subs = (b.get("api_fee_month", 0) + b.get("data_fee_month", 0)) * 1.18
            total = brk + dp + subs
            rows.append((total, f"| {b['name']} | ₹{per_order:,.2f} | {fmt_inr(brk)} | {fmt_inr(dp)} | {fmt_inr(subs)} | "
                                f"**{fmt_inr(total)}** | {fmt_inr(total + st['round_trip'] * rt)} | ₹{(total / rt if rt else 0):,.2f} | "
                                f"{conf(b, 'brokerage')} |"))
        out += [r for _, r in sorted(rows)] + [""]
    return "\n".join(out)


def summary_table(brokers, stat, sc) -> str:
    days, on = sc["trading_days_per_month"], date.fromisoformat(sc["as_of"])
    cols, stat_row, per = [], [], {}
    for s in sc["scenario"]:
        rt = s.get("round_trips_per_month", s.get("round_trips_per_day", 0) * days)
        v = s["value_per_leg"]
        cols.append(s["id"])
        stat_row.append(statutory(stat, s["segment"], v, on)["round_trip"] * rt)
        for key, b in brokers.items():
            brk = brokerage(b["brokerage"][s["leg"]], v) * 2 * rt * 1.18
            dp = b.get("dp_charge", 0) * s.get("dp_sell_days_per_month", 0) * 1.18
            subs = (b.get("api_fee_month", 0) + b.get("data_fee_month", 0)) * 1.18
            per.setdefault(key, []).append(brk + dp + subs)
    out = ["| Broker | " + " | ".join(cols) + " | Tariff conf. |", "|---|" + "---|" * (len(cols) + 1)]
    out.append("| *Statutory + exchange (all brokers)* | " + " | ".join(f"*{fmt_inr(x)}*" for x in stat_row) + " | H |")
    for key in sorted(per, key=lambda k: sum(per[k])):
        out.append(f"| {brokers[key]['name']} | " + " | ".join(fmt_inr(x) for x in per[key]) + f" | {conf(brokers[key], 'brokerage')} |")
    return "\n".join(out) + "\n"


def backfill_seconds(requests: int, rate: dict, share: float) -> float:
    """Lower bound on wall time for `requests` at `share` of every published window:
    max(requests / per-second rate, (full windows forced by each window limit) x window length)."""
    if requests <= 0:
        return 0.0
    t = requests / (rate["per_s"] * share) if "per_s" in rate else 0.0
    for key, window in (("per_min", 60), ("per_hour", 3600), ("per_day", 86400)):
        if key in rate:
            cap = max(1.0, rate[key] * share)
            t = max(t, (math.ceil(requests / cap) - 1) * window)
    return t


def fmt_t(s):
    if s is None:
        return "?"
    return f"{s / 86400:.1f} d" if s >= 86400 else f"{s / 3600:.1f} h" if s >= 3600 else f"{s / 60:.0f} min"


def data_table(brokers, sc) -> str:
    tgt = sc["data_target"]
    n, share = tgt["symbols"], tgt["budget_share"]
    out = ["# Data: backfill and daily-sync cost for the research universe", "",
           f"Target: {n} symbols, D × {tgt['daily_years']} y + 1m × {tgt['minute_years']} y (hybrid roll-up: 3m…1h are "
           f"rolled up from 1m, so only 1m + D are downloaded). Historify runs at {share:.0%} of each broker's limits. "
           "Times are lower bounds from the published windows; real runs add latency and retries.", "",
           "| Broker | 1m depth (y) | 1m years obtainable | Requests D | Requests 1m | Backfill time | Daily sync (1m + D) | Data conf. |",
           "|---|---|---|---|---|---|---|---|"]
    rows = []
    for key, b in brokers.items():
        d = b.get("data", {})
        md, rate, depth = d.get("max_days"), d.get("rate"), d.get("depth_1m_years")
        if not isinstance(md, dict) or not isinstance(rate, dict) or "1m" not in md:
            rows.append((1e18, f"| {b['name']} | {depth} | ? | ? | ? | ? | ? | not published / unknown |"))
            continue
        yrs = min(tgt["minute_years"], depth) if isinstance(depth, (int, float)) else None
        rd = n * math.ceil(tgt["daily_years"] * 365 / md["D"])
        rm = n * math.ceil(yrs * 365 / md["1m"]) if yrs else None
        total = rd + (rm or 0)
        t = backfill_seconds(total, rate, share)
        sync = backfill_seconds(2 * n, rate, share)
        c = "/".join(sorted({conf(b, 'max_days'), conf(b, 'rate'), conf(b, 'depth_1m_years')}))
        rows.append((t, f"| {b['name']} | {depth} | {yrs if yrs is not None else '?'} | {rd:,} | "
                        f"{rm:,} | **{fmt_t(t)}** | {fmt_t(sync)} | {c} |" if rm is not None else
                        f"| {b['name']} | {depth} | ? | {rd:,} | ? | ? | {fmt_t(sync)} | {c} |"))
    out += [r for _, r in sorted(rows)]
    out += ["", "Depth matters more than speed: a broker with 4–5 years of 1m cannot serve a 10-year intraday "
            "backtest at any speed. Older history then has to come from a purchased/vendor dataset or another broker.", ""]
    return "\n".join(out)


CAPS = [("intervals", "data", "Native intervals"), ("oi", "data", "Open interest"),
        ("continuous_futures", "data", "Continuous futures"), ("expired_fo", "data", "Expired F&O history"),
        ("corporate_actions", "api", "Corporate actions API"), ("charges", "api", "Charges / contract-note API"),
        ("holidays", "api", "Holidays / timings API"), ("margin", "api", "Margin API"),
        ("sandbox", "api", "Broker sandbox"), ("auth", "api", "Session / auth")]


def capability_table(brokers) -> str:
    head = "| Capability | " + " | ".join(b["name"].split(" (")[0] for b in brokers.values()) + " |"
    out = ["# API capabilities relevant to Historify Lab", "",
           "Cell = value [confidence]. V = verified in the official SDK source; H/M/L = assistant knowledge; ? = unknown.",
           "", head, "|" + "---|" * (len(brokers) + 1)]
    for field, grp, label in CAPS:
        cells = []
        for b in brokers.values():
            v = b.get(grp, {}).get(field, "?")
            v = ", ".join(v) if isinstance(v, list) else ("yes" if v is True else "no" if v is False else str(v))
            cells.append(f"{v} [{conf(b, field)}]")
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tariffs", default="broker_tariffs.toml")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    a = ap.parse_args()
    t = load(a.tariffs)
    brokers = {k: v for k, v in t.items() if isinstance(v, dict) and "name" in v}
    stat, sc = load("statutory_charges.toml"), load("scenarios.toml")
    os.makedirs(a.out, exist_ok=True)
    for fn, text in (("cost_summary.md", summary_table(brokers, stat, sc)),
                     ("cost_by_scenario.md", cost_tables(brokers, stat, sc)),
                     ("data_backfill.md", data_table(brokers, sc)),
                     ("capability_matrix.md", capability_table(brokers))):
        with open(os.path.join(a.out, fn), "w") as f:
            f.write(text)
        print(f"wrote {os.path.join(a.out, fn)}")


if __name__ == "__main__":
    main()
