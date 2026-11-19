"""ExecSpec v1 / OrderPlan / Instrument builders with the decision-D-09 defaults, validation and content hashes."""
from __future__ import annotations

import hashlib
import json

# D-09 defaults: next-open fills, bracket live on the entry bar, pessimistic intrabar, engine square-off 15:10 (before
# the broker's auto square-off window), no new entries after 14:55, participation cap 10 % of bar volume on intraday
# bars and none on daily bars. "auto" values are resolved by resolve_spec() once the bar size is known:
#   product "auto"            -> MIS on intraday bars, CNC on daily bars (audit 2 A2-H1)
#   participation_cap "auto"  -> 0.05 intraday (ADR-001), none daily
# Session times apply to intraday bars only (they are cleared for daily bars).
AUTO_PARTICIPATION_INTRADAY = 0.05     # ADR-001 (unified defaults, was 0.10): capacity honesty
DEFAULT_EXEC = {
    "exec_spec_version": 1, "fill_timing": "next_open", "decision_latency_s": 0, "intrabar_policy": "pessimistic",
    "bracket_live_on_entry_bar": True, "same_bar_priority": "chronological", "slippage_model": "none",
    "slippage_value": 0.0, "participation_cap": "auto", "participation_remainder": "carry", "zero_volume_fills": False,
    "no_new_entries_after": "14:55", "squareoff_at": "15:10", "product": "auto", "allow_short": None, "leverage": 1.0,
    "price_band_pct": None, "dividends": "credit",
}
# ADR-001 flagfix2 slippage refinements: OPTIONAL keys (absent = off; a spec without them hashes as before)
OPTIONAL_EXEC = {"slippage_min_ticks": 0.0, "slippage_edge_minutes": 0, "slippage_edge_widen": 1.0,
                 "slippage_clip": False, "session_open": "09:15", "session_close": "15:30"}
DEFAULT_PLAN = {"entry_type": "market", "entry_offset_pct": 0.0, "entry_expiry_bars": 0, "stop_pct": None,
                "target_pct": None, "trail_pct": None, "time_stop_bars": None, "sizing": "all_in", "sizing_value": 0.0}
DEFAULT_INSTRUMENT = {"lot": 1, "tick": 5, "price_scale": 100}        # NSE equity: Rs 0.05 tick, paise
ENUMS = {"fill_timing": ("next_open", "close"), "intrabar_policy": ("pessimistic", "ohlc_direction", "magnifier_1m"),
         "same_bar_priority": ("chronological", "stop_first"), "slippage_model": ("none", "ticks", "pct"),
         "participation_remainder": ("carry", "cancel", "cancel_entries"), "product": ("auto", "MIS", "CNC", "NRML"),
         "dividends": ("credit", "ignore"), "entry_type": ("market", "limit", "stop"),
         "sizing": ("all_in", "fixed_qty", "fixed_value", "pct_equity", "risk_pct")}


def _minutes(v):
    if v is None or isinstance(v, int):
        return v
    try:
        hh, mm = str(v).split(":")
        h, m = int(hh), int(mm)
    except ValueError:
        raise ValueError(f"time {v!r} must be 'HH:MM'") from None
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"time {v!r} is not a time of day")
    return h * 60 + m


def canonical(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"))


def sha256(d: dict) -> str:
    return hashlib.sha256(canonical(d).encode()).hexdigest()


def _check_enums(d: dict) -> None:
    for k, allowed in ENUMS.items():
        if k in d and d[k] not in allowed:
            raise ValueError(f"{k} must be one of {allowed}, got {d[k]!r}")


def exec_spec(**overrides) -> dict:
    """ExecSpec v1 with D-09 defaults. Values are range-checked here (early, readable errors) and again in the core."""
    unknown = set(overrides) - set(DEFAULT_EXEC) - set(OPTIONAL_EXEC)
    if unknown:
        raise ValueError(f"unknown ExecSpec fields: {sorted(unknown)}")
    s = {**DEFAULT_EXEC, **overrides}
    if not 0 <= float(s.get("slippage_min_ticks", 0)) <= 100:
        raise ValueError("slippage_min_ticks must be within 0..100")
    if not 0 <= int(s.get("slippage_edge_minutes", 0)) <= 180 or not 1 <= float(s.get("slippage_edge_widen", 1)) <= 10:
        raise ValueError("slippage_edge_minutes must be within 0..180 and slippage_edge_widen within 1..10")
    for k in ("session_open", "session_close"):
        if k in s:
            _minutes(s[k])
    if s["product"] in ("CNC", "NRML") and "squareoff_at" not in overrides:
        s["squareoff_at"] = None                        # audit 2 A2-L1: the MIS square-off is not part of CNC / NRML
    _check_enums(s)
    for k in ("no_new_entries_after", "squareoff_at"):
        _minutes(s[k])
    if s["participation_cap"] not in ("auto", None) and not 0 < s["participation_cap"] <= 1:
        raise ValueError("participation_cap must be in (0, 1]")
    if not 1 <= s["leverage"] <= 20:
        raise ValueError("leverage must be within 1..20")
    if s["slippage_model"] == "pct" and not 0 <= s["slippage_value"] <= 0.05:
        raise ValueError("slippage_value (pct) must be within 0..0.05")
    if s["slippage_model"] == "ticks" and not 0 <= s["slippage_value"] <= 100:
        raise ValueError("slippage_value (ticks) must be within 0..100")
    if not 0 <= s["decision_latency_s"] <= 3600:
        raise ValueError("decision_latency_s must be within 0..3600")
    if s["price_band_pct"] is not None and not 0 < s["price_band_pct"] <= 0.5:
        raise ValueError("price_band_pct must be in (0, 0.5]")
    return s


def order_plan(**overrides) -> dict:
    unknown = set(overrides) - set(DEFAULT_PLAN)
    if unknown:
        raise ValueError(f"unknown OrderPlan fields: {sorted(unknown)}")
    p = {**DEFAULT_PLAN, **overrides}
    _check_enums(p)
    for k in ("stop_pct", "target_pct", "trail_pct"):
        if p[k] is not None and not 0 < p[k] < 1:
            raise ValueError(f"{k} must be in (0, 1) (a fraction: 0.01 = 1 %), got {p[k]}")
    if not 0 <= p["entry_offset_pct"] < 0.5:
        raise ValueError("entry_offset_pct must be in [0, 0.5)")
    if p["sizing"] == "risk_pct" and p["stop_pct"] is None:
        raise ValueError("risk_pct sizing needs stop_pct")
    return p


def instrument(**overrides) -> dict:
    unknown = set(overrides) - set(DEFAULT_INSTRUMENT)
    if unknown:
        raise ValueError(f"unknown Instrument fields: {sorted(unknown)}")
    return {**DEFAULT_INSTRUMENT, **overrides}


def resolve_spec(spec: dict, bar_seconds: int) -> dict:
    """Resolve the bar-size dependent values; the resolved spec is what runs and what the manifest hashes."""
    s = dict(spec)
    intraday = bar_seconds < 86_400
    if s.get("participation_cap") == "auto":
        s["participation_cap"] = AUTO_PARTICIPATION_INTRADAY if intraday else None
    if s.get("product") == "auto":
        s["product"] = "MIS" if intraday else "CNC"
    if not intraday:
        if s["product"] == "MIS":
            raise ValueError("product MIS (intraday) needs intraday bars; use CNC or NRML on daily bars")
        s["no_new_entries_after"] = None                 # audit 2 A2-H1: session rules are intraday-only
        s["squareoff_at"] = None
    return s


def core_json(spec: dict) -> str:
    """The JSON the Rust core reads (times as minutes after midnight IST)."""
    s = dict(spec)
    if "auto" in (s.get("participation_cap"), s.get("product")):
        raise ValueError("'auto' values must be resolved first (resolve_spec)")
    for k in ("no_new_entries_after", "squareoff_at"):
        s[k] = _minutes(s.get(k))
    for k, core in (("session_open", "session_open_min"), ("session_close", "session_close_min")):
        if k in s:
            s[core] = _minutes(s.pop(k))
    return json.dumps(s)
