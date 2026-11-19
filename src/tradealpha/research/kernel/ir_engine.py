"""
IR strategies inside the ONE backtest process (unification U2).

Before: an IR strategy (flagfix1, Rust ``hbt-core``) could only be backtested through the hbt Lab's own flow
(LabService -> LabDispatcher -> lab_runs, core snapshots), a Python strategy (flagfix2) only through the P3 flow
(BacktestService -> LabRunner -> backtest_runs, lab snapshots) - two flows with different data, defaults, costs,
registries and UIs. Now the P3 pipeline runs both, and IR strategies are an ENGINE KIND of it:

    request (``strategy: "ir:<name>"``)  -> the same validation, hold-out lock, snapshot (lab snapshot v2), compute
    budget, run registry (``backtest_runs``), sweeps / walk-forward, evaluation library, API and Backtests page
    worker task ``hbt_backtest``        -> this module: every universe instrument runs on the Rust kernel with the
                                           unified ExecSpec defaults (ADR-001), the unified cost tables and the
                                           flagfix2 slippage model; the result is written in the P3 format

Portfolio: the universe runs on ONE account (hbt portfolio mode, Rust ``portfolio.rs``): shared cash, sizing
relative to the portfolio's equity, at most ``ExecConfig.max_positions`` open positions; an IR plan left on all_in
becomes equal-weight slots (pct_equity = 1 / max_positions). With one instrument the run equals ``hbt.run``.
IR strategies are causal by construction (the compiler refuses any future reference, E-CAUSAL), so their look-ahead
verdict is ``verified`` without a data check.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date

import numpy as np
import pandas as pd

from tradealpha.research.strategies.python_tier import Bool, Choice, Float, Int, PythonStrategy, StrategyError

IR_PREFIX = "ir:"
VIEW_TO_ADJUSTMENT = {"split": "adjusted", "raw": "raw", "total": "total_return"}


# ---------------------------------------------------------------------------------------------- strategy adapter
class IrStrategy(PythonStrategy):
    """An IR strategy version seen through the P3 strategy interface (parameters, schema, product, timeframes)."""
    kind = "ir"
    ir_json = ""
    ir_sha = ""
    ir_version = 0
    lab_strategy_id: int | None = None

    def features(self, d):                                   # pragma: no cover - never called: the kernel decides
        raise StrategyError("IR strategies run on the hbt kernel (task hbt_backtest), not the Python signal pass")

    def signals(self, f, d):                                 # pragma: no cover
        raise StrategyError("IR strategies run on the hbt kernel")

    @classmethod
    def schema(cls) -> dict:
        return {"name": cls.name, "description": cls.description, "timeframes": cls.timeframes,
                "product": cls.product, "params": {k: p.to_dict() for k, p in cls.params.items()},
                "refs": list(getattr(cls, "refs", ()) or ()),
                "universe": None, "code_sha": cls.ir_sha, "kind": "ir", "engine": "hbt", "version": cls.ir_version,
                "lab_strategy_id": cls.lab_strategy_id}


def _param(p: dict):
    t = p.get("type")
    if t == "int":
        return Int(int(p["default"]), p.get("min"), p.get("max"), int(p.get("step") or 1))
    if t == "float":
        return Float(float(p["default"]), p.get("min"), p.get("max"), p.get("step"))
    if t == "bool":
        return Bool(bool(p["default"]))
    if t == "choice":
        return Choice(p["default"], tuple(p.get("choices") or [p["default"]]))
    raise StrategyError(f"unsupported IR parameter type {t!r}")


def make_ir_class(name: str, ir_json: str, ir_sha: str, version: int, description: str | None = None,
                  lab_strategy_id: int | None = None) -> type:
    doc = json.loads(ir_json)
    interval = str((doc.get("universe") or {}).get("interval") or "D")
    product = doc.get("product") or ("CNC" if interval in ("D", "W", "M") else "MIS")
    if product == "NRML":
        raise StrategyError("NRML (F&O) IR strategies are not supported by the P3 pipeline yet")
    attrs = {"name": f"{IR_PREFIX}{name}", "description": description or doc.get("description") or doc.get("name", ""),
             "timeframes": {"signal": interval, "exec": interval}, "params": {k: _param(v) for k, v in
                                                                            (doc.get("params") or {}).items()},
             "product": product, "ir_json": ir_json, "ir_sha": ir_sha, "ir_version": int(version),
             "lab_strategy_id": lab_strategy_id, "__module__": __name__,
             "refs": tuple(_ir_refs(doc))}                     # N3: cross-asset series its data columns read
    return type(f"Ir_{hashlib.sha1(name.encode()).hexdigest()[:8]}", (IrStrategy,), attrs)


def _ir_refs(doc: dict) -> list[str]:
    from tradealpha.features.api import ir_columns
    return ir_columns.refs_for(ir_columns.extras_in(doc))


def is_ir(name: str | None) -> bool:
    return bool(name) and str(name).startswith(IR_PREFIX)


# ---------------------------------------------------------------------------------------------- data bridge
def _hhmm(ts: int) -> str:
    t = (int(ts) + 19_800) % 86_400
    return f"{t // 3600:02d}:{(t % 3600) // 60:02d}"


def session_map(portal, exchange: str = "NSE") -> dict:
    """Calendar of the snapshot bundle as {date: ("HH:MM", "HH:MM")} for hbt.Dataset (special sessions included)."""
    from tradealpha.research.snapshots.portal import day_date
    cal = portal.calendar
    k = exchange if exchange in cal.days else "NSE"
    return {day_date(int(d)): (_hhmm(o), _hhmm(c))
            for d, o, c in zip(cal.days.get(k, []), cal.opens.get(k, []), cal.closes.get(k, []), strict=False)}


def events_for_symbol(portal, symbol: str) -> list[dict]:
    """The snapshot's (merged, point-in-time frozen) corporate-action factors as hbt events."""
    from tradealpha.marketdata.api import PRICE_EVENTS
    try:
        ca = portal._table("corporate_actions")
    except Exception:                                         # pragma: no cover - old bundles without the table
        return []
    if ca is None or not len(ca):
        return []
    rows = ca[(ca["symbol"] == symbol) & (ca["mode"] == "split")]
    out = []
    for r in rows.itertuples(index=False):
        kinds = [k for k in str(r.kind).split(",") if k]
        kind = kinds[0] if len(kinds) == 1 and kinds[0] in PRICE_EVENTS else "implied"
        ex = r.ex_date if isinstance(r.ex_date, date) else date.fromisoformat(str(r.ex_date)[:10])
        out.append({"ex_date": ex, "kind": kind, "price_factor": float(r.factor)})
    return out


def dataset_from_portal(hbt, portal, key: str, interval: str, adjustment: str, start: str, end: str,
                        tfs=(), sessions: dict | None = None, extras=()):
    """One instrument of a lab snapshot as an hbt Dataset: RAW prices (the portal converts a broker-adjusted history
    back to raw), the bundle's corporate actions, the bundle's calendar, and the point-in-time data columns the
    program reads (``extras``: ``app.lab.ir_extras``)."""
    meta = portal.meta(key)
    f = portal.frame(key, interval)
    hi = pd.Timestamp(end).tz_localize("Asia/Kolkata").normalize() + pd.Timedelta(days=1)
    f = f[f["ts"] < int(hi.timestamp())]
    df = pd.DataFrame({"ts": f["ts"].to_numpy("int64"),
                       **{c: np.round(f[c].to_numpy(float), 2) for c in ("open", "high", "low", "close")},
                       "volume": np.round(f["volume"].to_numpy(float)).astype("int64")})
    ds = hbt.Dataset.from_frame(df, interval, exchange=meta["exchange"], adjustment=adjustment,
                                events=events_for_symbol(portal, meta["symbol"]), calendar=sessions,
                                tfs=list(tfs or []), trade_from=start)
    if extras:
        from tradealpha.features.api import feature_store
        from tradealpha.features.api import ir_columns as ir_extras
        cols = feature_store.cached(getattr(portal, "root", None), portal, key, interval, f, list(extras),
                                    lambda: ir_extras.compute(portal, key, f, extras))     # N8: materialised
        ds = ds.with_extras(**cols)
    return ds, f, meta


def unified_spec(ex) -> dict:
    """ADR-001: the flagfix2 ``ExecConfig`` (unified defaults) as hbt ExecSpec overrides - the same square-off,
    entry cut-off, participation cap (every bar size, as in the P3 simulator; an entry's remainder is cancelled,
    an exit's carried) and MIS leverage (``mis_leverage``, 5x) as the Python engine. Used by the unified backtest pipeline AND the paper host of IR deployments."""
    product = ex.product
    return {"product": product, "squareoff_at": ex.mis_squareoff if product == "MIS" else None,
            "no_new_entries_after": ex.entry_cutoff or (ex.mis_squareoff if product == "MIS" else None),
            "participation_cap": float(ex.participation) if ex.participation > 0 else None,
            "participation_remainder": "cancel_entries",
            "leverage": float(ex.mis_leverage) if product == "MIS" else 1.0}


def slippage_spec(ex, fraction: float) -> dict:
    """The flagfix2 slippage model (ADR-001) as hbt ExecSpec keys: the liquidity bucket's bps (``fraction``, incl.
    slippage_mult), at least ``slippage_ticks`` ticks, x ``edge_widen`` in the first / last ``edge_minutes`` of the
    session (daily bars: fills at the open), the price clipped to the executing bar's range."""
    mult = float(ex.slippage_mult)
    if fraction <= 0 and float(ex.slippage_ticks) * mult <= 0:
        return {"slippage_model": "none"}
    return {"slippage_model": "pct", "slippage_value": min(float(fraction), 0.05),
            "slippage_min_ticks": min(float(ex.slippage_ticks) * mult, 100.0),
            "slippage_edge_minutes": int(ex.edge_minutes), "slippage_edge_widen": float(ex.edge_widen),
            "slippage_clip": True}


def _mini_trades(pres) -> list[dict]:
    """Minimal trade rows (the columns eval.metrics.trade_stats reads) from a PortfolioResult's legs."""
    out = []
    for key, res in pres.legs.items():
        for t in res.trades.itertuples(index=False):
            net = t.pnl / 100
            out.append({"key": key, "side": "long" if t.dir > 0 else "short", "net_pnl": net,
                        "gross_pnl": t.dir * (t.exit_value - t.entry_value) / 100, "costs": t.fees / 100,
                        "return_pct": net / max(t.entry_value / 100, 1e-9) * 100, "r_multiple": None,
                        "bars": int(t.exit_bar - t.entry_bar + 1), "mfe_pct": 0.0, "mae_pct": 0.0,
                        "ambiguous": False, "exit_reason": str(t.exit_reason)})
    return out


def _m1_bars(hbt, portal, key: str, end: str) -> dict:
    """The instrument's stored 1m bars (raw, run window) as core bars: the segments a decision latency executes on."""
    from hbt.data import bars_from_frame
    f = portal.frame(key, "1m")
    hi = pd.Timestamp(end).tz_localize("Asia/Kolkata").normalize() + pd.Timedelta(days=1)
    f = f[f["ts"] < int(hi.timestamp())]
    df = pd.DataFrame({"ts": f["ts"].to_numpy("int64"),
                       **{c: np.round(f[c].to_numpy(float), 2) for c in ("open", "high", "low", "close")},
                       "volume": np.round(f["volume"].to_numpy(float)).astype("int64")})
    return bars_from_frame(df)


# ---------------------------------------------------------------------------------------------- worker entry point
def _segment(product: str) -> str:
    return {"CNC": "EQ_DELIVERY", "MIS": "EQ_INTRADAY"}.get(product, "EQ_INTRADAY")


def _slippage_fraction(frame: pd.DataFrame, start: str, ex) -> tuple[float, str]:
    """flagfix2 slippage model (ADR-001): liquidity bucket from the 60 sessions BEFORE the start (no look-ahead)."""
    from tradealpha.research.kernel import rules
    t0 = int(pd.Timestamp(start).tz_localize("Asia/Kolkata").timestamp())
    prior = frame[frame["ts"] < t0]
    if len(prior):
        by_day = prior.groupby("day").apply(lambda g: float((g["close"] * g["volume"]).sum()), include_groups=False)
        turnover = float(by_day.tail(60).median())
    else:
        turnover = float("nan")
    bucket = rules.liquidity_bucket(turnover)
    return float(ex.slippage_bps[bucket]) / 1e4 * float(ex.slippage_mult), bucket


def run_ir_backtest(lab_root: str, cfg: dict, progress=None, cancel=None) -> dict:
    from tradealpha.features.api import ir_columns as ir_extras
    from tradealpha.research.evaluation import metrics
    from tradealpha.research.kernel import load_hbt
    from tradealpha.research.kernel.pysim.backtest import Cancelled, benchmark_series
    from tradealpha.research.kernel.rules import ExecConfig
    from tradealpha.research.snapshots.portal import DataPortal, PortalConfig, day_number

    t_start = time.monotonic()
    hbt = load_hbt()
    prog = hbt.ir.compile(cfg["ir"])
    params = cfg.get("params") or {}
    interval = cfg["interval"]
    view = cfg.get("view", "split")
    warnings: list[str] = []
    adjustment = prog.adjustment or VIEW_TO_ADJUSTMENT[view]
    if adjustment == "total_return":
        adjustment = "adjusted"
        warnings.append("total-return view: the IR kernel uses split-adjusted signals in the unified pipeline "
                        "(dividend cash is not in the snapshot bundle's factor table)")
    plan_info = prog.plan(interval, json.dumps(params) if params else None)
    portal = DataPortal(lab_root, cfg["snapshot_id"], PortalConfig(view="raw",
                                                                   intraday_latency_s=int(cfg.get("latency_s", 0))))
    keys = portal.keys("universe")
    if not keys:
        raise StrategyError("the snapshot universe is empty")
    ex = ExecConfig(**{k: v for k, v in (cfg.get("execution") or {}).items() if k in ExecConfig.__dataclass_fields__})
    product = ex.product
    capital = float(ex.capital)
    sessions = session_map(portal)
    d0, d1 = day_number(date.fromisoformat(cfg["start"])), day_number(date.fromisoformat(cfg["end"]))
    cal_days = portal.calendar.trading_days("NSE")
    eq_days = cal_days[(cal_days >= d0) & (cal_days <= d1)]
    _, eq_close = portal.calendar.sessions("NSE", eq_days)
    costs = hbt.cost_model(_segment(product), broker=cfg.get("broker") or "angelone")
    spec_over = unified_spec(ex)
    # intraday: orders reach the market after the bar is FINAL (available_at: planner finality, the run's latency_s
    # - D4) or after the order latency, whichever is longer, on the stored 1m bars - as the Python engine does
    intraday = interval not in ("D", "W", "M")
    latency = max(int(ex.order_latency_s), int(cfg.get("latency_s") or 0) if intraday else 0)
    if latency:
        spec_over["decision_latency_s"] = latency
    m1 = {}
    # 1. every instrument's dataset, instrument and slippage (flagfix2 model, per liquidity bucket)
    datasets, frames, insts, specs, manifests = {}, {}, {}, {}, {}
    for n, key in enumerate(keys):
        if cancel and cancel():
            raise Cancelled()
        if progress:
            progress(n, len(keys), key)
        try:
            ds, frame, meta = dataset_from_portal(hbt, portal, key, interval, adjustment, cfg["start"], cfg["end"],
                                                  plan_info["frames"], sessions, plan_info.get("extras") or ())
        except ir_extras.ExtrasError as e:                   # a data column the program needs is missing: fail
            raise StrategyError(str(e)) from e
        except Exception as e:                               # no data for this instrument / interval
            warnings.append(f"{key}: skipped ({e})")
            continue
        slip, bucket = _slippage_fraction(frame, cfg["start"], ex)
        specs[key] = slippage_spec(ex, slip)
        tick = float(meta.get("tick_size") or ex.tick or 0.05)
        insts[key] = hbt.instrument(tick=max(1, round(tick * 100)), lot=int(meta.get("lot_size") or 1))
        datasets[key], frames[key] = ds, frame
        manifests[key] = {"liquidity_bucket": bucket, "slippage_fraction": slip}
        if latency and intraday and "1m" in portal.intervals_for(key):
            m1[key] = _m1_bars(hbt, portal, key, cfg["end"])
        portal.evict(key)
    if not datasets:
        raise StrategyError("no instrument of the universe has data for this interval")
    # 2. ONE account for the whole universe (hbt portfolio mode): shared cash, sizing relative to the portfolio,
    #    at most ExecConfig.max_positions open positions. A plan left on all_in becomes equal-weight slots.
    n_legs = len(datasets)
    max_pos = max(1, min(int(ex.max_positions or n_legs), n_legs))
    plan = hbt.order_plan(sizing="pct_equity", sizing_value=round(1.0 / max_pos, 10)) if n_legs > 1 else None
    pres = hbt.run_portfolio(prog, datasets, params or None, spec=hbt.exec_spec(**spec_over), specs=specs,
                             plan=plan, insts=insts, costs=costs, cash=capital,
                             max_positions=max_pos if n_legs > 1 else None, m1=m1 or None)
    trades, fills, values = [], [], []
    trade_id = 0
    for key, res in pres.legs.items():
        frame = frames[key]
        manifests[key] = {**res.manifest, **manifests[key]}
        warnings += [f"{key}: {w}" for w in res.warnings]
        open_at = dict(zip(frame["ts"].to_numpy("int64"), frame["open_at"].to_numpy("int64"), strict=False))
        hi, lo = frame["high"].to_numpy(float), frame["low"].to_numpy(float)
        ts_arr = frame["ts"].to_numpy("int64")
        first = {}
        for t in res.trades.itertuples(index=False):
            trade_id += 1
            qty = int(t.qty)
            entry_px = t.entry_value / qty / 100
            exit_px = t.exit_value / qty / 100
            i0, i1 = int(np.searchsorted(ts_arr, t.entry_ts)), int(np.searchsorted(ts_arr, t.exit_ts))
            seg_h, seg_l = hi[i0:i1 + 1], lo[i0:i1 + 1]
            if t.dir > 0:
                mfe, mae = (seg_h.max() / entry_px - 1) * 100, (seg_l.min() / entry_px - 1) * 100
            else:
                mfe, mae = (1 - seg_l.min() / entry_px) * 100, (1 - seg_h.max() / entry_px) * 100
            net = t.pnl / 100
            trades.append({"trade_id": trade_id, "key": key, "side": "long" if t.dir > 0 else "short",
                           "decision_t": int(open_at.get(int(t.entry_ts), t.entry_ts)),
                           "entry_t": int(open_at.get(int(t.entry_ts), t.entry_ts)), "entry_price": round(entry_px, 4),
                           "qty": qty, "exit_t": int(open_at.get(int(t.exit_ts), t.exit_ts)),
                           "exit_price": round(exit_px, 4), "exit_reason": str(t.exit_reason), "exits": 1,
                           "bars": int(t.exit_bar - t.entry_bar + 1),
                           "gross_pnl": round(t.dir * (t.exit_value - t.entry_value) / 100, 2),
                           "costs": round(t.fees / 100, 2), "net_pnl": round(net, 2),
                           "return_pct": round(net / (t.entry_value / 100) * 100, 4), "r_multiple": None,
                           "mfe_pct": round(float(mfe), 4), "mae_pct": round(float(mae), 4), "ambiguous": False,
                           "tag": cfg.get("strategy", "")})
            first.setdefault(int(t.entry_bar), trade_id)
        for f in res.fills.itertuples(index=False):
            buy = bool(f.buy)
            day = date(1970, 1, 1) + pd.Timedelta(days=int((int(f.ts) + 19_800) // 86_400))
            total = f.fees / 100
            fills.append({"t": int(open_at.get(int(f.ts), f.ts)), "date": day.isoformat(), "key": key,
                          "side": "buy" if buy else "sell", "qty": float(f.qty), "price": float(f.price),
                          "base_price": float(f.price), "reason": str(f.reason), "trade_id": first.get(int(f.bar)),
                          "cost_brokerage": f.brokerage / 100, "cost_stt": f.stt / 100, "cost_exchange": f.exch / 100,
                          "cost_sebi": f.sebi / 100, "cost_stamp": f.stamp / 100, "cost_gst": f.gst / 100,
                          "cost_dp": f.dp / 100, "cost_total": round(total, 2),
                          "cash_delta": float((-1 if buy else 1) * f.value / 100 - total),
                          "qty_adj_delta": float(f.qty if buy else -f.qty)})
        # value of this instrument's position at each trading day's close (for the cash column)
        cl = pd.Series(frame["close"].to_numpy(float), index=frame["day"].to_numpy("int64")).groupby(level=0).last()
        cl = cl.reindex(eq_days).ffill().fillna(0.0)
        kf = pd.DataFrame([x for x in fills if x["key"] == key])
        if len(kf):
            fd = ((kf["t"].to_numpy("int64") + 19_800) // 86_400)
            q = pd.Series(kf["qty_adj_delta"].to_numpy(float), index=fd).groupby(level=0).sum()
            qty_at = q.reindex(np.union1d(q.index, eq_days), fill_value=0.0).cumsum().reindex(eq_days).to_numpy()
        else:
            qty_at = np.zeros(len(eq_days))
        values.append(qty_at * cl.to_numpy(float))
    # daily portfolio equity on the trading days of the run (last value of each day)
    eq = pres.equity
    eq_day = ((eq.index.as_unit("s").asi8 + 19_800) // 86_400).astype("int64")     # unit-safe (pandas 3)
    eq_total = pd.Series(eq.to_numpy(float), index=eq_day).groupby(level=0).last().reindex(eq_days).ffill() \
        .fillna(capital).to_numpy(float)
    value = sum(values)
    equity = [(int(d), int(c), float(e), float(e - v)) for d, c, e, v in
              zip(eq_days, eq_close, eq_total, value, strict=True)]
    rf = cfg.get("rf_annual_pct")
    if rf is None:
        split_portal = DataPortal(lab_root, cfg["snapshot_id"], PortalConfig(view="split"))
        r = split_portal.ref("rf.tbill_91d")
        rf = float(r.iloc[-1]) if len(r) else 6.5
        if not len(r):
            warnings.append("risk-free rate: no rf.tbill_91d series in the snapshot - 6.5 % assumed")
    bench, bench_label = (None, None)
    if cfg.get("benchmark"):
        bench, bench_label = benchmark_series(DataPortal(lab_root, cfg["snapshot_id"], PortalConfig(view="split")),
                                              cfg.get("benchmark"), warnings)
    summary = metrics.summary(equity, trades, fills, capital, rf, bench)
    summary["benchmark_used"] = bench_label
    summary["engine"] = "hbt"
    if "slippage" in (cfg.get("extras") or []):
        # the promotion gate's "profit factor at 2x slippage" for IR strategies too (the Python engine's extra)
        keys = ("total_return_pct", "cagr_pct", "sharpe", "max_drawdown_pct", "trades", "profit_factor")
        sens = {"1x": {k: summary.get(k) for k in keys}}
        for mult in (2.0, 3.0):
            ex2 = ExecConfig(**{**ex.to_dict(), "slippage_mult": ex.slippage_mult * mult})
            specs2 = {k: slippage_spec(ex2, _slippage_fraction(frames[k], cfg["start"], ex2)[0]) for k in datasets}
            p2 = hbt.run_portfolio(prog, datasets, params or None, spec=hbt.exec_spec(**spec_over), specs=specs2,
                                   plan=plan, insts=insts, costs=costs, cash=capital,
                                   max_positions=max_pos if n_legs > 1 else None, m1=m1 or None)
            eq2 = pd.Series(p2.equity.to_numpy(float),
                            index=((p2.equity.index.as_unit("s").asi8 + 19_800) // 86_400).astype("int64"))                 .groupby(level=0).last().reindex(eq_days).ffill().fillna(capital).to_numpy(float)
            m2 = metrics.summary([(int(d), int(c), float(e), float(e)) for d, c, e in
                                  zip(eq_days, eq_close, eq2, strict=True)],
                                 _mini_trades(p2), [], capital, rf)
            sens[f"{mult:g}x"] = {k: m2.get(k) for k in keys}
        summary["slippage_sensitivity"] = sens
    summary["universe"] = {"instruments": n_legs, "mode": "portfolio" if n_legs > 1 else "single",
                           "max_positions": max_pos if n_legs > 1 else 1, "peak_open": pres.peak_open,
                           "sizing": f"pct_equity {1 / max_pos:.4f} per position" if plan else "strategy / all_in",
                           "missing_instruments": portal.manifest.get("missing_instruments", [])}
    h = hashlib.sha256(b"bt-results/hbt/v1")
    from tradealpha.research.snapshots import store as fs
    for name, rows in (("trades", trades), ("fills", fills)):
        h.update(name.encode())
        h.update(fs.canonical_json(rows))
    h.update(fs.canonical_json([[a, b, round(c, 6), round(d, 6)] for a, b, c, d in equity]))
    out = {"run_id": cfg.get("run_id", ""), "metrics": summary, "warnings": warnings, "artefact_sha": h.hexdigest(),
           "code_sha": cfg.get("ir_sha"), "params": params, "cost_schedule": f"hbt:{costs.get('source', 'tables')}",
           "cost_sha": hashlib.sha256(json.dumps(costs, sort_keys=True, default=str).encode()).hexdigest()[:16],
           "execution": ex.to_dict(), "manifest_sha": portal.manifest["content_sha"], "engine": "hbt",
           "runtime_s": round(time.monotonic() - t_start, 3)}
    if cfg.get("out_dir"):
        folder = cfg["out_dir"]
        os.makedirs(folder, exist_ok=True)
        for name, rows in (("trades", trades), ("fills", fills)):
            if rows:
                fs.to_parquet(pd.DataFrame(rows), os.path.join(folder, f"{name}.parquet"))
        fs.to_parquet(pd.DataFrame(equity, columns=["day", "close_ts", "equity", "cash"]),
                      os.path.join(folder, "equity.parquet"))
        with open(os.path.join(folder, "config.json"), "w") as f:
            json.dump({k: v for k, v in cfg.items() if k != "ir"}, f, indent=1, default=str)
        with open(os.path.join(folder, "strategy.ir.json"), "w") as f:
            f.write(cfg["ir"])
        with open(os.path.join(folder, "hbt_manifests.json"), "w") as f:
            json.dump(manifests, f, indent=1, sort_keys=True, default=str)
        with open(os.path.join(folder, "report.json"), "w") as f:
            json.dump(out, f, indent=1, default=str)
    out["daily_returns"] = [[e[0], e[2]] for e in equity]
    out["initial"] = capital
    return out
