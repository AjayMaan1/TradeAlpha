"""
Research protocol (phase 1.6, design §15): walk-forward optimisation, the deflated Sharpe ratio, and a robustness
subset. Library functions (no Historify imports): the app's research projects call them in a worker process and
store every evaluated variant in the append-only trial registry.

* ``wfo_windows``   rolling in-sample / out-of-sample windows by calendar month (IS 6 m, OOS 1 m by default); OOS
                    windows are disjoint and contiguous, every IS window ends where its OOS window starts
* ``walk_forward``  sweep the grid on each IS window, pick the best variant by the objective (with a minimum number
                    of trades), run it on the following OOS window; the OOS pieces are concatenated
* ``deflated_sharpe`` Bailey & Lopez de Prado (2014): probability that the true Sharpe ratio is above the expected
                    maximum of N unskilled trials, correcting for skew / kurtosis and track-record length
* ``robustness``    neighbours of the chosen parameters (+-1 / +-2 grid steps), costs x 1.5, one bar more latency
"""
from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from hbt.costs import cost_model  # noqa: F401  (re-exported for callers building costs)
from hbt.engine import run, sweep

EULER_GAMMA = 0.5772156649015329
IST = timedelta(hours=5, minutes=30)


# ------------------------------------------------------------------------------------------------ statistics
def _norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def _norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation, |error| < 1.2e-9 after one Newton step)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00, 3.754408661907416e+00)
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    elif p > 1 - lo:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    else:
        q = p - 0.5
        r = q * q
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    e = _norm_cdf(x) - p                                            # one Newton step
    return x - e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)


def expected_max_sharpe(n_trials: int, var_sharpe: float) -> float:
    """E[max SR] of N unskilled trials whose Sharpe ratios have variance ``var_sharpe`` (the DSR's SR0)."""
    if n_trials < 2 or var_sharpe <= 0:
        return 0.0
    return math.sqrt(var_sharpe) * ((1 - EULER_GAMMA) * _norm_ppf(1 - 1 / n_trials)
                                     + EULER_GAMMA * _norm_ppf(1 - 1 / (n_trials * math.e)))


def probabilistic_sharpe(sr: float, sr0: float, t: int, skew: float = 0.0, kurt: float = 3.0) -> float:
    """PSR: P[true SR > sr0] for an observed per-period SR over ``t`` returns (kurt = non-excess kurtosis)."""
    if t < 2:
        return float("nan")
    den = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if den <= 0:
        return float("nan")
    return _norm_cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(den))


def deflated_sharpe(sr: float, t: int, n_trials: int, var_sharpe: float, skew: float = 0.0,
                    kurt: float = 3.0) -> dict:
    """DSR (all Sharpe ratios PER PERIOD, not annualised). Returns {dsr, sr0, psr0}."""
    sr0 = expected_max_sharpe(n_trials, var_sharpe)
    return {"dsr": probabilistic_sharpe(sr, sr0, t, skew, kurt), "sr0": sr0,
            "psr0": probabilistic_sharpe(sr, 0.0, t, skew, kurt)}


def return_moments(returns) -> dict:
    r = np.asarray(returns, dtype="float64")
    r = r[np.isfinite(r)]
    if len(r) < 3 or r.std(ddof=1) == 0:
        return {"t": int(len(r)), "sr": 0.0, "skew": 0.0, "kurt": 3.0}
    m, s = r.mean(), r.std(ddof=1)
    z = (r - m) / r.std(ddof=0)
    return {"t": int(len(r)), "sr": float(m / s), "skew": float((z ** 3).mean()), "kurt": float((z ** 4).mean())}


# ------------------------------------------------------------------------------------------------ windows
def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def _ts_of(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() - IST.total_seconds())


def _date_of(ts: int) -> date:
    return (datetime.fromtimestamp(int(ts), UTC) + IST).date()


def wfo_windows(first: date, last: date, is_months: int = 6, oos_months: int = 1,
                step_months: int | None = None) -> list[dict]:
    """Rolling windows on month boundaries inside [first, last] (IST dates). Each: is_start < is_end == oos_start <
    oos_end (end exclusive). The first IS window starts on the first month boundary on or after ``first``."""
    if is_months < 1 or oos_months < 1:
        raise ValueError("is_months and oos_months must be >= 1")
    step = step_months or oos_months
    start = first if first.day == 1 else _add_months(first, 1)
    out, k = [], 0
    while True:
        is_s = _add_months(start, k * step)
        is_e = _add_months(is_s, is_months)
        oos_e = _add_months(is_e, oos_months)
        if oos_e > last + timedelta(days=1):
            break
        out.append({"fold": len(out), "is_start": is_s, "is_end": is_e, "oos_start": is_e, "oos_end": oos_e})
        k += 1
    return out


# ------------------------------------------------------------------------------------------------ protocol
def _pick(table: pd.DataFrame, params: list[str], objective: str, min_trades: int,
          variants: list[dict] | None = None) -> dict | None:
    """Best row by the objective (ties: smallest parameters) among rows with enough trades; returned as the original
    variant dict (parameter types preserved)."""
    ok = table[(table["trades"] >= min_trades) & table[objective].notna()]
    if ok.empty:
        return None
    best = ok.sort_values([objective] + params, ascending=[False] + [True] * len(params)).iloc[0]
    got = {p: _py(best[p]) for p in params}
    for v in variants or []:
        if all(v.get(p) == got[p] for p in params):
            return dict(v)
    return got


def _py(x):
    return x.item() if hasattr(x, "item") else x


def _bar_returns(equity: pd.Series, start_ts: int, end_ts: int) -> pd.Series:
    ts = np.asarray(equity.index.as_unit("s").asi8)
    e = equity.to_numpy("float64")
    m = (ts >= start_ts) & (ts < end_ts)
    idx = np.nonzero(m)[0]
    if len(idx) == 0:
        return pd.Series(dtype="float64")
    prev = e[idx[0] - 1] if idx[0] > 0 else e[idx[0]]
    seg = np.concatenate([[prev], e[idx]])
    return pd.Series(seg[1:] / seg[:-1] - 1.0, index=equity.index[idx])


def walk_forward(program, dataset, variants, *, is_months: int = 6, oos_months: int = 1, step_months=None,
                 objective: str = "sharpe", min_trades: int = 5, warmup_bars: int = 0, spec=None, plan=None,
                 inst=None, costs=None, cash: float = 1_000_000.0, on_trial=None) -> dict:
    """Walk-forward optimisation. ``on_trial(phase, fold, params, metrics)`` sees every evaluated variant (the trial
    registry). Returns folds, the concatenated OOS returns and their metrics."""
    from hbt.ir import compile as ir_compile
    prog = ir_compile(program) if isinstance(program, dict | str) else program
    variants = [dict(v) for v in variants]
    params = sorted({k for v in variants for k in v})
    ts = dataset.exec["ts"]
    first = _date_of(int(ts[max(dataset.trade_start, 0)]))
    last = _date_of(int(ts[-1]))
    folds = []
    oos_parts = []
    kw = dict(spec=spec, plan=plan, inst=inst, costs=costs, cash=cash)
    for w in wfo_windows(first, last, is_months, oos_months, step_months):
        ds_is = dataset.window(_ts_of(w["is_start"]), _ts_of(w["is_end"]), warmup_bars)
        if len(ds_is) - ds_is.trade_start < 2:
            continue
        table = sweep(prog, ds_is, variants, **kw)
        for row in table.to_dict("records"):
            if on_trial:
                on_trial("is", w["fold"], {p: _py(row[p]) for p in params if p in row},
                         {k: _py(v) for k, v in row.items() if k not in params})
        best = _pick(table, params, objective, min_trades, variants)
        fold = {**{k: v.isoformat() if isinstance(v, date) else v for k, v in w.items()},
                "variants": int(len(table)), "best": best,
                "is_best_metrics": None if best is None else
                {k: _py(v) for k, v in table.loc[(table[params] == pd.Series(best)).all(axis=1)].iloc[0].items()
                 if k not in params}}
        if best is not None:
            ds_oos = dataset.window(_ts_of(w["oos_start"]), _ts_of(w["oos_end"]), warmup_bars)
            res = run(prog, ds_oos, best, **kw)
            r = _bar_returns(res.equity, _ts_of(w["oos_start"]), _ts_of(w["oos_end"]))
            trades_oos = res.trades[res.trades["entry_ts"] >= _ts_of(w["oos_start"])]
            om = {"return": float((1 + r).prod() - 1) if len(r) else 0.0, "bars": int(len(r)),
                  "trades": int(len(trades_oos)), "pnl": float(trades_oos["pnl"].sum() / 100) if len(trades_oos)
                  else 0.0}
            fold["oos"] = om
            if on_trial:
                on_trial("oos", w["fold"], best, {**om, "sharpe": _ann_sharpe(r, dataset)})
            oos_parts.append(r)
        folds.append(fold)
    oos = pd.concat(oos_parts) if oos_parts else pd.Series(dtype="float64")
    mom = return_moments(oos.to_numpy())
    ppy = dataset.periods_per_year()
    return {"folds": folds, "params": params, "oos_returns": oos,
            "oos": {"bars": int(len(oos)), "total_return": float((1 + oos).prod() - 1) if len(oos) else 0.0,
                    "sharpe": mom["sr"] * math.sqrt(ppy), "sr_per_bar": mom["sr"], "skew": mom["skew"],
                    "kurt": mom["kurt"], "periods_per_year": ppy},
            "selected": next((f["best"] for f in reversed(folds) if f.get("best")), None)}


def _ann_sharpe(r: pd.Series, dataset) -> float:
    m = return_moments(r.to_numpy())
    return m["sr"] * math.sqrt(dataset.periods_per_year())


def _scaled_costs(costs: dict, factor: float) -> dict:
    """Every rate and fixed charge x factor (robustness: costs x 1.5)."""
    out = {**costs, "versions": [], "caveats": list(costs.get("caveats", [])) + [f"costs_x{factor:g}"]}
    for v in costs.get("versions", []):
        out["versions"].append({k: (round(x * factor) if k.endswith("_e8") and k != "gst_e8" else x)
                                for k, x in v.items()})
    b = dict(costs.get("brokerage") or {})
    for k in ("pct_e8", "cap_paise", "flat_paise", "min_paise"):
        if b.get(k) is not None:
            b[k] = round(b[k] * factor)
    out["brokerage"] = b
    out["dp_paise"] = round((costs.get("dp_paise") or 0) * factor)
    return out


def neighbours(selected: dict, variants: list[dict], steps=(1, 2)) -> list[dict]:
    """Variants of the grid that differ from ``selected`` in ONE parameter by 1 or 2 grid steps."""
    axes = {k: sorted({v[k] for v in variants if k in v}) for k in selected}
    have = {tuple(sorted(v.items())) for v in variants}
    out = []
    for k, vals in axes.items():
        if selected[k] not in vals:
            continue
        i = vals.index(selected[k])
        for d in steps:
            for j in (i - d, i + d):
                if 0 <= j < len(vals):
                    cand = {**selected, k: vals[j]}
                    if tuple(sorted(cand.items())) in have and cand not in out:
                        out.append(cand)
    return out


def robustness(program, dataset, selected: dict, variants: list[dict], *, spec, plan=None, inst=None, costs=None,
               cash: float = 1_000_000.0, cost_factor: float = 1.5, delay_bars: int = 1, on_trial=None) -> dict:
    """The robustness subset (design §15.4): neighbours, cost stress, one bar more latency. Each check passes when
    the stressed Sharpe stays positive (neighbours: median >= half the selected Sharpe)."""
    kw = dict(plan=plan, inst=inst, cash=cash)
    base = run(program, dataset, selected, spec=spec, costs=costs, **kw).metrics
    out = {"base_sharpe": base["sharpe"], "checks": {}}
    nb = neighbours(selected, variants)
    if nb:
        t = sweep(program, dataset, nb, spec=spec, costs=costs, **kw)
        for row in t.to_dict("records"):
            if on_trial:
                on_trial("robust", 0, {k: _py(row[k]) for k in selected}, {"check": "neighbour",
                                                                           **{k: _py(v) for k, v in row.items()
                                                                              if k not in selected}})
        med = float(t["sharpe"].median()) if len(t) else float("nan")
        out["checks"]["neighbours"] = {"n": int(len(t)), "median_sharpe": med,
                                       "positive_share": float((t["sharpe"] > 0).mean()) if len(t) else 0.0,
                                       "passed": bool(len(t) and med > 0 and med >= 0.5 * (base["sharpe"] or 0))}
    stressed = run(program, dataset, selected, spec=spec, costs=_scaled_costs(costs, cost_factor), **kw).metrics
    out["checks"]["costs"] = {"factor": cost_factor, "sharpe": stressed["sharpe"],
                              "passed": bool((stressed["sharpe"] or 0) > 0)}
    delayed = run(program, dataset.lagged(delay_bars), selected, spec=spec, costs=costs, **kw).metrics
    out["checks"]["delay"] = {"bars": delay_bars, "sharpe": delayed["sharpe"],
                              "passed": bool((delayed["sharpe"] or 0) > 0)}
    if on_trial:
        on_trial("robust", 0, selected, {"check": "costs", **stressed})
        on_trial("robust", 0, selected, {"check": "delay", **delayed})
    checks = out["checks"].values()
    out["score"] = sum(1 for c in checks if c["passed"]) / len(out["checks"])
    return out


def evaluate(program, dataset, variants, *, protocol: dict | None = None, spec=None, plan=None, inst=None,
             costs=None, cash: float = 1_000_000.0, n_trials_before: int = 0, on_trial=None) -> dict:
    """The full research protocol: full-period sweep, walk-forward, DSR (N = trials in the registry including this
    project's), robustness of the selected variant, verdict against the gates."""
    from hbt.ir import compile as ir_compile
    p = {"objective": "sharpe", "min_trades": 5, "is_months": 6, "oos_months": 1, "step_months": None,
         "cost_factor": 1.5, "delay_bars": 1, "dsr_min": 0.95, "oos_sharpe_min": 0.0, "robust_min": 2 / 3,
         **(protocol or {})}
    prog = ir_compile(program) if isinstance(program, dict | str) else program
    variants = [dict(v) for v in variants]
    params = sorted({k for v in variants for k in v})
    kw = dict(spec=spec, plan=plan, inst=inst, costs=costs, cash=cash)
    warm = max(prog.plan(dataset.interval, None)["lookback"], 1)
    count = {"n": 0}

    def trial(phase, fold, prm, metrics):
        count["n"] += 1
        if on_trial:
            on_trial(phase, fold, prm, metrics)

    full = sweep(prog, dataset, variants, **kw)
    for row in full.to_dict("records"):
        trial("full", 0, {k: _py(row[k]) for k in params if k in row},
              {k: _py(v) for k, v in row.items() if k not in params})
    wf = walk_forward(prog, dataset, variants, is_months=p["is_months"], oos_months=p["oos_months"],
                      step_months=p["step_months"], objective=p["objective"], min_trades=p["min_trades"],
                      warmup_bars=warm * 2, on_trial=trial, **kw)
    # DSR of the full-period in-sample winner, deflated by every trial of the registry (this project's included)
    ppy = dataset.periods_per_year()
    sr_bar = full["sharpe"].dropna().to_numpy() / math.sqrt(ppy)
    best = _pick(full, params, p["objective"], p["min_trades"], variants)
    dsr = {"dsr": float("nan"), "sr0": float("nan")}
    robust = None
    search_trials = count["n"]
    n_total = int(n_trials_before) + search_trials
    if best is not None:
        res = run(prog, dataset, best, **kw)
        r = _bar_returns(res.equity, int(dataset.exec["ts"][dataset.trade_start]), int(dataset.exec["ts"][-1]) + 1)
        mom = return_moments(r.to_numpy())
        dsr = deflated_sharpe(mom["sr"], mom["t"], n_total, float(np.var(sr_bar, ddof=1)) if len(sr_bar) > 1
                              else 0.0, mom["skew"], mom["kurt"])
        dsr.update(sr_is=mom["sr"], sr_is_annual=mom["sr"] * math.sqrt(ppy), t=mom["t"], n_trials=n_total)
        robust = robustness(prog, dataset, wf["selected"] or best, variants, spec=spec, plan=plan, inst=inst,
                            costs=costs, cash=cash, cost_factor=p["cost_factor"], delay_bars=p["delay_bars"],
                            on_trial=trial)
    gates = {"dsr": bool(best is not None and dsr["dsr"] >= p["dsr_min"]),
             "oos_sharpe": bool(wf["oos"]["bars"] and wf["oos"]["sharpe"] > p["oos_sharpe_min"]),
             "robustness": bool(robust and robust["score"] >= p["robust_min"])}
    return {"protocol": p, "params": params, "variants": len(variants), "full_best": best,
            "full_best_metrics": None if best is None else
            {k: _py(v) for k, v in full.loc[(full[params] == pd.Series(best)).all(axis=1)].iloc[0].items()
             if k not in params},
            "walk_forward": {k: v for k, v in wf.items() if k != "oos_returns"},
            "oos_returns": wf["oos_returns"], "dsr": dsr, "robustness": robust, "gates": gates,
            "verdict": "accepted" if all(gates.values()) else "rejected", "trials": count["n"],
            "search_trials": search_trials, "n_trials_before": int(n_trials_before),
            "selected": wf["selected"] or best}


__all__ = ["deflated_sharpe", "evaluate", "expected_max_sharpe", "neighbours", "probabilistic_sharpe",
           "robustness", "walk_forward", "wfo_windows"]
