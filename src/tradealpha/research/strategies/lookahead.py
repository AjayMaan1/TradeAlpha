"""
Look-ahead gate for Python strategies (unification U0 / defect D2).

flagfix2's Python strategies get the whole frame of an instrument; nothing stops ``bars.close.shift(-1)``. The
corrupt-the-future check lived only in the test suite (for the built-in strategies), so a peeking user strategy could
be frozen and deployed (measured: +2,204 %, Sharpe 4.6, no warning). This module makes the check part of the product:

    every value that became known after a cut time is scrambled (``CorruptPortal``); the strategy's decisions up to
    the cut must not change. Two cuts (55 % and 80 % through the data). Any difference -> look-ahead.

``BacktestService.freeze`` runs it once per code version (the result is stored on the strategy version) and refuses
to freeze a strategy that fails. A strategy that decides nothing before a cut is "inconclusive" (allowed, recorded).
IR strategies (flagfix1) need no such check: the compiler refuses every future reference (E-CAUSAL).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

import numpy as np

from tradealpha.platform.time.intervals import INTERVALS
from tradealpha.platform.time.timeutil import IST

log = logging.getLogger(__name__)
CUTS = (0.55, 0.80)
# IR strategies (flagfix1): the compiler refuses every reference to a current-or-future bar (E-CAUSAL) and the
# operator catalogue is look-ahead fuzzed in CI - verified by construction, no data check needed
IR_CAUSAL = {"verified": True, "method": "ir_causal_by_construction",
             "note": "Strategy IR: future references are refused at compile time (E-CAUSAL)"}
MAX_INSTRUMENTS = 5


class CorruptPortal:
    """Wraps a DataPortal: every value that became known after ``cut`` (epoch s) is scrambled."""

    def __init__(self, portal, cut: int, seed: int = 5):
        self.p, self.cut, self.rng = portal, cut, np.random.default_rng(seed)
        self._frames = {}

    def __getattr__(self, name):
        return getattr(self.p, name)

    def frame(self, key, tf):
        k = (key, tf)
        if k not in self._frames:
            f = self.p.frame(key, tf).copy()
            late = f.index > self.cut
            m = int(late.sum())
            if m:
                mult = self.rng.uniform(0.5, 2.0, m)
                for c in ("open", "high", "low", "close"):
                    f.loc[late, c] = f.loc[late, c] * mult * self.rng.uniform(0.97, 1.03, m)
                f.loc[late, "high"] = f.loc[late, ["open", "high", "low", "close"]].max(axis=1)
                f.loc[late, "low"] = f.loc[late, ["open", "high", "low", "close"]].min(axis=1)
                f.loc[late, "volume"] = self.rng.uniform(0, 1e7, m)
            self._frames[k] = f
        return self._frames[k]

    def eod(self, key, field):
        s = self.p.eod(key, field).copy()
        s[s.index > self.cut] = self.rng.uniform(0, 100, int((s.index > self.cut).sum()))
        return s

    def ref(self, name):
        s = self.p.ref(name).copy()
        s[s.index > self.cut] = 1e6
        return s

    def events(self, kinds=None, symbol=None, min_importance=1):
        ev = self.p.events(kinds, symbol, min_importance)
        return ev[~(ev["available_at"].fillna(-1) > self.cut)] if len(ev) else ev

    def event_flag(self, to, kinds, symbol=None, days_ahead=0, min_importance=1, scope=None):
        return type(self.p).event_flag(self, to, kinds, symbol, days_ahead, min_importance, scope)

    def align(self, x, to):
        return self.p.align(x, to)

    def forming(self, key, to):
        return type(self.p).forming(self, key, to)

    def raw(self, frame):
        return self.p.raw(frame)


def _native_intervals(cls) -> tuple[str, ...]:
    codes = {"D"}
    for tf in (cls.timeframes or {}).values():
        tf = str(tf)
        if tf.startswith("schedule:") or (tf in INTERVALS and INTERVALS[tf].is_intraday):
            codes.add("1m")
    return tuple(sorted(codes))


def _decisions(inp, cut: int) -> list:
    out = [("enter", i.key, int(i.decision_t), i.side, i.stop, i.target, i.limit)
           for i in inp.intents if i.decision_t <= cut]
    for (key, side), ts in sorted(inp.exit_signals.items()):
        out += [("exit", key, int(t), side) for t in ts if t <= cut]
    return out


def _check_snapshot(sv, cls) -> tuple[str, date, date]:
    """A small snapshot of data the strategy can run on: up to MAX_INSTRUMENTS equities that have every native
    interval it needs (content-addressed, so a repeated check stores nothing new)."""
    from tradealpha.platform.db.models import Instrument
    codes = _native_intervals(cls)
    cov = sv.store.coverage()
    if not len(cov):
        raise LookaheadUnverifiable("no bars in the store - download data (or build a snapshot) first")
    have = None
    for code in codes:
        ids = set(cov[cov["interval_id"] == INTERVALS[code].id]["instrument_id"].astype(int))
        have = ids if have is None else have & ids
    with sv.meta.session() as s:
        insts = [i for i in s.query(Instrument).filter(Instrument.id.in_(sorted(have or set())))
                 .order_by(Instrument.id) if i.exchange in ("NSE", "BSE") and (i.instrument_type or "EQ") == "EQ"]
    if not insts:
        raise LookaheadUnverifiable(f"no equity in the store has {'+'.join(codes)} bars")
    insts = insts[:MAX_INSTRUMENTS]
    sel = cov[cov["instrument_id"].isin([i.id for i in insts])]
    base = sel[sel["interval_id"] == INTERVALS["1m" if "1m" in codes else "D"].id]
    last = min(int(x) for x in base["last_ts"])
    end = (datetime.fromtimestamp(last, IST)).date()
    start = end - timedelta(days=30 if "1m" in codes else 540)
    snap = sv.backtests.snapshots.build({"kind": "symbols", "symbols": [f"{i.exchange}:{i.symbol}" for i in insts]},
                                        start, end, intervals=codes, name=f"look-ahead check {cls.name}"[:100],
                                        warmup_days=400 if "1m" not in codes else 60,
                                        refs=_refs(cls))
    return snap["snapshot_id"], start, end


def _refs(cls) -> tuple:
    from tradealpha.research.snapshots.build import DEFAULT_REFS
    return tuple(dict.fromkeys(tuple(DEFAULT_REFS) + tuple(getattr(cls, "refs", ()) or ())))


class LookaheadUnverifiable(RuntimeError):
    pass


def check_strategy(sv, cls, params: dict | None = None, snapshot_id: str | None = None,
                   start: date | None = None, end: date | None = None) -> dict:
    """-> {"verified": True | False | None (inconclusive), "violations": [...], ...}. Raises LookaheadUnverifiable
    when there is no data to run the strategy on."""
    from tradealpha.research.kernel.pysim.backtest import BacktestConfig, build_inputs
    from tradealpha.research.kernel.rules import ExecConfig
    from tradealpha.research.snapshots.portal import DataPortal, PortalConfig

    own = snapshot_id is None
    if own:
        snapshot_id, start, end = _check_snapshot(sv, cls)
    root = sv.backtests.root
    try:
        base = DataPortal(root, snapshot_id, PortalConfig())
        days = base.calendar.trading_days("NSE")
        from tradealpha.research.snapshots.portal import day_date, day_number
        lo, hi = day_number(start), day_number(end)
        days = days[(days >= lo) & (days <= hi)]
        if len(days) < 10:
            raise LookaheadUnverifiable("fewer than 10 trading days of data to check on")
        cfg = BacktestConfig(strategy=cls.name, snapshot_id=snapshot_id, start=start.isoformat(), end=end.isoformat())
        ex = ExecConfig(product=getattr(cls, "product", "CNC") or "CNC", completeness="warn")
        inp_a, _ = build_inputs(base, cls(**(params or {})), cfg, ex)
        out = {"verified": True, "snapshot_id": snapshot_id, "range": [start.isoformat(), end.isoformat()],
               "instruments": len(inp_a.data), "cuts": [], "violations": []}
        compared = 0
        for frac in CUTS:
            d = day_date(int(days[int(len(days) * frac)]))
            cut = int(datetime.combine(d, time(12, 0), IST).timestamp())
            inp_b, _ = build_inputs(CorruptPortal(DataPortal(root, snapshot_id, PortalConfig()), cut),
                                    cls(**(params or {})), cfg, ex)
            a, b = _decisions(inp_a, cut), _decisions(inp_b, cut)
            compared += len(a)
            out["cuts"].append({"cut": d.isoformat(), "decisions": len(a)})
            if a != b:
                first = next(((x, y) for x, y in zip(a, b, strict=False) if x != y), (a[len(b):][:1], b[len(a):][:1]))
                out["verified"] = False
                out["violations"].append({"cut": d.isoformat(), "decisions_real": len(a), "decisions_scrambled": len(b),
                                          "first_difference": [str(first[0]), str(first[1])]})
        if out["verified"] and compared == 0:
            out["verified"] = None
            out["note"] = "inconclusive: the strategy made no decision before the cut-offs on the check data"
        return out
    finally:
        if own:
            try:
                sv.backtests.snapshots.delete(snapshot_id)
            except Exception:                                    # pragma: no cover - best effort
                log.info("could not delete the look-ahead check snapshot %s", snapshot_id, exc_info=True)
