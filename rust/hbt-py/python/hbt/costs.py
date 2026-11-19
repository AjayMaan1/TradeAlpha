"""Cost models for the core: statutory schedule by trade date (bundled, NSE) + a broker tariff + the DP charge.

The bundled statutory rates mirror broker_evaluation/statutory_charges.toml and the broker tariffs mirror
broker_evaluation/broker_tariffs.toml. Both are marked UNVERIFIED: confirm them against the current NSE/SEBI circulars and
the broker's pricing page before a live stage (decision D-13 blocks live trading while they are unverified).

Audit 2 A2-M7: a backtest must say which brokerage it pays. ``cost_model()`` refuses to guess: pass ``broker="zerodha"``
(bundled tariff incl. the DP charge), an explicit tariff dict, or ``brokerage="none"`` (recorded as a caveat).
"""
from __future__ import annotations

import json
from datetime import date
from importlib import resources

E8 = 100_000_000
SEGMENTS = ("EQ_INTRADAY", "EQ_DELIVERY", "FUTURES", "OPTIONS")


def _day(iso: str) -> int:
    return (date.fromisoformat(iso) - date(1970, 1, 1)).days


def _bundled(name: str) -> dict:
    return json.loads(resources.files("hbt").joinpath(f"data/{name}").read_text())


def statutory(segment: str) -> list[dict]:
    raw = _bundled("statutory_nse.json")
    if segment not in raw["segments"]:
        raise ValueError(f"segment must be one of {sorted(raw['segments'])}")
    return [{"effective_day": _day(v["effective_from"]), "stt_buy_e8": round(float(v.get("stt_buy", 0)) * E8),
             "stt_sell_e8": round(float(v.get("stt_sell", 0)) * E8), "stamp_buy_e8": round(float(v.get("stamp_buy", 0)) * E8),
             "exch_e8": round(float(v.get("exch", 0)) * E8), "sebi_e8": round(float(raw["sebi"]) * E8),
             "gst_e8": round(float(raw["gst"]) * E8)} for v in raw["segments"][segment]]


def brokers() -> dict:
    """Bundled broker tariffs (unverified): {key: {name, brokerage: {segment: {...}}, dp_charge, confidence}}."""
    return _bundled("brokers.json")["brokers"]


def cost_model(segment: str = "EQ_INTRADAY", brokerage: dict | str | None = None, broker: str | None = None,
               dp_charge: float | None = None, zero: bool = False) -> dict:
    """segment: EQ_INTRADAY | EQ_DELIVERY | FUTURES | OPTIONS.
    brokerage: {"pct": 0.0003, "cap": 20} | {"flat": 20} | {"pct": 0.001, "cap": 20, "min": 5} (rupees) | "none".
    broker: a bundled tariff key (see brokers()); dp_charge: rupees per scrip per delivery-sell day (before GST).
    zero: no costs at all (comparisons with other engines only)."""
    if zero:
        return {"versions": [{"effective_day": -10**9, "gst_e8": 0}], "brokerage": {}, "dp_paise": 0,
                "caveats": ["zero_costs"], "source": "zero"}
    if segment not in SEGMENTS:
        raise ValueError(f"segment must be one of {SEGMENTS}")
    caveats = ["statutory_unverified"]
    if broker is not None:
        if brokerage is not None:
            raise ValueError("give broker= or brokerage=, not both")
        table = brokers()
        if broker not in table:
            raise ValueError(f"unknown broker '{broker}'; bundled: {', '.join(sorted(table))}")
        b = dict(table[broker]["brokerage"].get(segment, {}))
        dp = table[broker].get("dp_charge", 0.0) if dp_charge is None else dp_charge
        caveats.append("broker_tariff_unverified")
        source = f"broker:{broker}"
    elif brokerage == "none":
        b, dp, source = {}, dp_charge or 0.0, "brokerage:none"
        caveats.append("no_brokerage")
    elif isinstance(brokerage, dict):
        b, dp, source = dict(brokerage), dp_charge or 0.0, "brokerage:custom"
    else:
        raise ValueError("cost_model needs the brokerage you pay: broker='zerodha' (bundled tariffs: see "
                         "hbt.costs.brokers()), brokerage={'pct': 0.0003, 'cap': 20}, or brokerage='none' "
                         "(recorded as a caveat)")
    unknown = set(b) - {"pct", "cap", "flat", "min"}
    if unknown:
        raise ValueError(f"unknown brokerage keys {sorted(unknown)} (pct, cap, flat, min)")
    if segment != "EQ_DELIVERY":
        dp = 0.0                                                   # DP charges apply to delivery sells only
    rs = lambda v: None if v is None else round(float(v) * 100)        # noqa: E731  rupees -> paise
    return {"versions": statutory(segment),
            "brokerage": {"pct_e8": round(float(b.get("pct", 0)) * E8), "cap_paise": rs(b.get("cap")),
                          "flat_paise": rs(b.get("flat")), "min_paise": rs(b.get("min"))},
            "dp_paise": rs(dp) or 0, "caveats": caveats, "source": source}


def flat_rate(rate: float) -> dict:
    """A symmetric fee (e.g. 0.001 = 0.1 % per side, no GST) - only for comparisons with other engines."""
    r = round(rate * E8)
    return {"versions": [{"effective_day": -10**9, "stt_buy_e8": r, "stt_sell_e8": r, "gst_e8": 0}], "brokerage": {},
            "dp_paise": 0, "caveats": ["flat_rate_comparison"], "source": "flat"}


def core_costs(costs: dict) -> str:
    """The JSON the Rust core reads (caveats / source stay in the manifest)."""
    return json.dumps({k: v for k, v in costs.items() if k in ("versions", "brokerage", "dp_paise")})
