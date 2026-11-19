"""
Look-ahead fuzz harness (design §12.2). Perturb the FUTURE and require that nothing up to the cut changes.

    for cut points t_k (random + session and higher-timeframe boundaries):
        X' = X with every base bar after t_k replaced by a seeded random walk (OHLC-valid); higher frames that are
             roll-ups of the base bars are RE-DERIVED from X' (keeping their available_at), other higher-frame bars
             not yet available at t_k's decision time are replaced independently
        run the program on X and X' (streaming, every node traced)
        require every node value and every intent at bars <= t_k to be bit-identical
        on failure report the FIRST differing node (topological order) -> it names the guilty operator

Corporate-action events and the adjustment factors are kept (backward adjustment is a sanctioned, scale-only use of
later events; see hbt.dataset).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from hbt.costs import cost_model
from hbt.dataset import Dataset


@dataclass
class Violation:
    cut: int
    bar: int
    node: str
    base: float
    perturbed: float


@dataclass
class Report:
    cuts: list = field(default_factory=list)
    violations: list = field(default_factory=list)
    intent_violations: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations and not self.intent_violations

    def __str__(self):
        if self.ok:
            return f"causal: {len(self.cuts)} cuts, no differences"
        v = (self.violations or self.intent_violations)[0]
        return f"LOOK-AHEAD at cut {v.cut}: {v.node} at bar {v.bar} changed {v.base} -> {v.perturbed}"


def _walk(rng, start: float, n: int, tick: float) -> tuple:
    steps = rng.normal(0, 0.01, n)
    close = np.maximum(start * np.exp(np.cumsum(steps)), tick * 10)
    close = np.round(close / tick) * tick
    open_ = np.concatenate([[start], close[:-1]])
    open_ = np.round(open_ / tick) * tick
    spread = np.round(np.abs(rng.normal(0, 0.005, n)) * close / tick) * tick
    high = np.maximum(open_, close) + spread
    low = np.maximum(np.minimum(open_, close) - spread, tick)
    return open_, high, low, close


def perturb(ds: Dataset, cut: int, seed: int = 0) -> Dataset:
    """A copy of ``ds`` whose future after base bar ``cut`` (and every higher-frame bar not yet available at its
    decision time) is replaced by random data."""
    rng = np.random.default_rng(seed)
    n = len(ds)
    m = n - cut - 1
    ex = {k: v.copy() for k, v in ds.exec.items()}
    sig = {k: v.copy() for k, v in ds.sig.items()}
    if m > 0:
        tick = 1.0 / ds.price_scale
        start = float(ds.exec["close"][cut]) / ds.price_scale
        o, h, lo, c = _walk(rng, start * float(rng.uniform(0.7, 1.3)), m, tick * 5)
        for k, arr in (("open", o), ("high", h), ("low", lo), ("close", c)):
            ex[k][cut + 1:] = np.round(arr * ds.price_scale).astype("int64")
            sig[k][cut + 1:] = ex[k][cut + 1:] / ds.price_scale * ds.sig["factor"][cut + 1:]
        vol = rng.integers(0, 2_000_000, m)
        ex["volume"][cut + 1:] = vol
        sig["volume"][cut + 1:] = vol.astype("float64")
    decision = int(ds.sig["available_at"][cut])
    frames = {}
    base_df = pd.DataFrame({"ts": ex["ts"], **{k: sig[k] for k in ("open", "high", "low", "close", "volume")}})
    for code, fr in ds.frames.items():
        f = {k: v.copy() for k, v in fr.items()}
        # a frame that is a roll-up of the base bars is RE-DERIVED from the perturbed bars: that is what exposes a
        # frame whose availability is wrong (e.g. a daily bar joined by ts is visible before its close exists)
        derived = _rollup(base_df, ds, code)
        if derived is not None:
            pos = np.searchsorted(derived["ts"].to_numpy(), f["ts"])
            ok = (pos < len(derived)) & (derived["ts"].to_numpy()[np.minimum(pos, len(derived) - 1)] == f["ts"])
            for k in ("open", "high", "low", "close", "volume"):
                f[k][ok] = derived[k].to_numpy("float64")[pos[ok]]
        fut = np.flatnonzero(fr["available_at"] > decision)
        if derived is None and len(fut):
            base = float(fr["close"][max(fut[0] - 1, 0)])
            o, h, lo, c = _walk(rng, base * float(rng.uniform(0.7, 1.3)), len(fut), 0.05)
            f["open"][fut], f["high"][fut], f["low"][fut], f["close"][fut] = o, h, lo, c
            f["volume"][fut] = rng.integers(0, 2_000_000, len(fut)).astype("float64")
        frames[code] = f
    extras = {}
    for k, v in ds.extras.items():
        x = v.copy()
        x[cut + 1:] = rng.normal(0, 1, m) * (np.nanstd(v) or 1.0)
        extras[k] = x
    return replace(ds, exec=ex, sig=sig, frames=frames, extras=extras)


def _rollup(base_df, ds: Dataset, code: str):
    """The frame rebuilt from base bars (None when it cannot be: e.g. an intraday frame from daily bars)."""
    from hbt.dataset import ORDER, resample
    if ORDER.index(code) <= ORDER.index(ds.interval):
        return None
    try:
        if code in ("W", "M") and ds.bar_seconds < 86_400:
            return resample(resample(base_df, ds.interval, "D", ds.sessions), "D", code, ds.sessions)
        return resample(base_df, ds.interval, code, ds.sessions)
    except ValueError:
        return None


def _cuts(ds: Dataset, k: int, rng, lookback: int) -> list[int]:
    n = len(ds)
    lo = min(max(lookback, 1), n - 2)
    pool = set(rng.integers(lo, n - 1, size=max(k, 1)).tolist())
    days = ds.exec["day"]
    firsts = np.flatnonzero(np.diff(days) != 0)              # last bar of a session
    if len(firsts):
        pool |= set(rng.choice(firsts, size=min(3, len(firsts)), replace=False).tolist())
        pool |= set((rng.choice(firsts, size=min(2, len(firsts)), replace=False) + 1).tolist())
    return sorted(c for c in pool if lo <= c < n - 1)


def _same(a, b) -> np.ndarray:
    return (a == b) | (np.isnan(a) & np.isnan(b))


def check_ir(program, ds: Dataset, params: dict | None = None, cuts: int = 6, seed: int = 0, spec=None,
             costs=None, plan=None) -> Report:
    """Causality of an IR program on a dataset: node traces and intents up to each cut must not change."""
    from hbt.engine import run

    costs = costs or cost_model(zero=True)
    base = run(program, ds, params, spec=spec, plan=plan, costs=costs, trace=True)
    rng = np.random.default_rng(seed)
    rep = Report()
    for i, cut in enumerate(_cuts(ds, cuts, rng, 0)):
        rep.cuts.append(cut)
        other = run(program, perturb(ds, cut, seed * 1000 + i), params, spec=spec, plan=plan, costs=costs, trace=True)
        a, b = base.trace.to_numpy()[:cut + 1], other.trace.to_numpy()[:cut + 1]
        diff = ~_same(a, b)
        if diff.any():
            cols = np.flatnonzero(diff.any(axis=0))
            col = int(cols[0])                                 # slots are in topological order
            bar = int(np.flatnonzero(diff[:, col])[0])
            rep.violations.append(Violation(cut, bar, base.trace.columns[col], float(a[bar, col]),
                                            float(b[bar, col])))
        _compare_intents(base, other, cut, rep)
    return rep


def check_strategy(strategy, ds: Dataset, cuts: int = 6, seed: int = 0, spec=None, costs=None, plan=None) -> Report:
    """Causality of a Python-tier strategy: its intents up to each cut must not change."""
    costs = costs or cost_model(zero=True)
    base = strategy.run(ds, spec=spec, plan=plan, costs=costs)
    rng = np.random.default_rng(seed)
    rep = Report()
    for i, cut in enumerate(_cuts(ds, cuts, rng, 0)):
        rep.cuts.append(cut)
        other = type(strategy)(**strategy._params).run(perturb(ds, cut, seed * 1000 + i), spec=spec, plan=plan,
                                                        costs=costs)
        _compare_intents(base, other, cut, rep)
    return rep


def _compare_intents(base, other, cut: int, rep: Report) -> None:
    a = base.intents[base.intents["bar"] <= cut].reset_index(drop=True)
    b = other.intents[other.intents["bar"] <= cut].reset_index(drop=True)
    same = len(a) == len(b) and a["bar"].tolist() == b["bar"].tolist() and a["kind"].tolist() == b["kind"].tolist() \
        and a["order"].tolist() == b["order"].tolist() and \
        bool(_same(a["price"].to_numpy("float64"), b["price"].to_numpy("float64")).all())
    if not same:
        k = 0
        while k < min(len(a), len(b)) and a["bar"].iloc[k] == b["bar"].iloc[k] and \
                a["kind"].iloc[k] == b["kind"].iloc[k]:
            k += 1
        bar = int(a["bar"].iloc[k]) if k < len(a) else int(b["bar"].iloc[k]) if k < len(b) else cut
        rep.intent_violations.append(Violation(cut, bar, "intents", float(len(a)), float(len(b))))
