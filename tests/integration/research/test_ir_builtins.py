"""The flagfix2 built-in strategies ported to the Strategy IR (app/lab/strategies/ir): every port compiles, reads
only catalogued point-in-time data columns, is listed with the Python strategies and registered on first use, and
DECIDES LIKE THE PYTHON ORIGINAL: the same entries (instrument, day / minute, side) through the one backtest
pipeline on the same snapshot, with the same index / delivery / event data - and, with the ADR-001 execution
mapping (participation remainder, MIS leverage, flagfix2 slippage model, finality latency), fills that agree to a
fraction of a percent."""
from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from tests.support.lab.bt_synth import seed_universe
from tests.support.paths import BUILTIN_IR

pytestmark = pytest.mark.trading_mock_matching     # the mock OpenAlgo's matching calibration


IR_DIR = BUILTIN_IR
DAILY = ("buy_hold", "trend_allocation", "rsi2", "breakout_delivery", "event_risk", "results_drift")
INTRADAY = ("mtf_pullback", "orb_cpr")
END = date(2025, 3, 28)


def _wait(sv, rid, timeout=240):
    t0 = time.monotonic()
    while sv.backtests.run(rid)["status"] in ("queued", "running"):
        if time.monotonic() - t0 > timeout:
            raise AssertionError("run did not finish")
        time.sleep(0.2)
    return sv.backtests.run(rid)


def _seed_daily(sv):
    from tradealpha.platform.db.models import MarketEvent
    seed_universe(sv, ["AAA", "BBB", "CCC"], date(2021, 1, 4), END)
    days = sv.calendar().trading_days("NSE", date(2023, 6, 1), END)
    rng = np.random.default_rng(7)
    with sv.meta.session() as s:                                    # RBI policy days (known 30 days ahead)
        for d in days[20::45]:
            s.add(MarketEvent(event_key=f"rbi:{d}", event_date=d, kind="rbi_policy", scope="market", title="RBI",
                              importance=3, status="confirmed", source="test",
                              available_at=datetime(d.year, d.month, d.day) - timedelta(days=30)))
        for sym in ("AAA", "BBB"):                                   # results days (intimation 10 days ahead)
            for d in days[5::9]:
                s.add(MarketEvent(event_key=f"r:{sym}:{d}", event_date=d, kind="results", scope="symbol", symbol=sym,
                                  title="Results", importance=3, status="confirmed", source="test",
                                  available_at=datetime(d.year, d.month, d.day) - timedelta(days=10)))
        s.commit()
    for d in days:                                                  # bhavcopy delivery % (known at 20:00)
        sv.refdata.store.replace_day("cm_eod", d, pd.DataFrame({
            "trade_date": [d] * 3, "symbol": ["AAA", "BBB", "CCC"], "series": ["EQ"] * 3, "close": [1.0] * 3,
            "deliv_pct": rng.uniform(20, 80, 3).round(2)}))
    sv.backtests.update_config({"workers": 2, "holdout_months": 0})
    sid = sv.backtests.snapshots.build({"kind": "symbols", "symbols": ["NSE:AAA", "NSE:BBB", "NSE:CCC"]},
                                       date(2022, 1, 3), END, ("D",))["snapshot_id"]
    return {"sid": sid}


@pytest.fixture
def dsv(make_services):
    sv = make_services(seed=("ir-builtins-daily", _seed_daily))
    sv.backtests.start()
    return sv


def _entries(sv, rid, minute=False):
    out = set()
    for t in sv.backtests.artefact(rid, "fills"):
        if t["reason"] != "entry":
            continue
        when = (t["t"] + 19_800) // (60 if minute else 86_400)
        out.add((t["key"], t["side"], when))
    return out


def _pair(sv, sid, name, params, start, minute=False, execution=None):
    runs = {}
    for strat in (name, "ir:" + name):
        r = sv.backtests.create_backtest({"strategy": strat, "snapshot_id": sid, "start": start, "end": END.isoformat(),
                                          "extras": [], "params": params, "execution": execution or {}})
        run = _wait(sv, r["run_id"])
        assert run["status"] == "succeeded", run
        runs[strat] = (run, _entries(sv, r["run_id"], minute))
    return runs[name], runs["ir:" + name]


# ------------------------------------------------------------------------------------------------ catalogue
def test_every_port_compiles_and_reads_only_catalogued_data():
    import hbt
    from tradealpha.features import ir_columns as ir_extras
    from tradealpha.research.strategies.python_tier import load
    names = sorted(f[:-5] for f in os.listdir(IR_DIR) if f.endswith(".json"))
    assert names == sorted(DAILY + INTRADAY)
    for n in names:
        doc = json.load(open(os.path.join(IR_DIR, f"{n}.json")))
        prog = hbt.ir.compile(doc)
        plan = prog.plan(doc["universe"]["interval"], None)
        assert ir_extras.unknown(plan["extras"]) == [], (n, plan["extras"])
        py = load(doc["meta"]["port_of"])
        assert py.product == doc["product"]                        # same product as the original
        assert set(doc["params"]) == set(py.params), n             # same parameters (choices numbered)


def test_ports_are_listed_and_registered_on_first_use(make_services):
    from sqlalchemy import select

    from tradealpha.platform.db.lab_models import LabStrategy
    sv = make_services()
    listed = {s["name"]: s for s in sv.backtests.strategies()}
    assert all(listed[f"ir:{n}"]["builtin"] for n in DAILY + INTRADAY)
    with sv.meta.session() as s:
        assert s.scalar(select(LabStrategy).where(LabStrategy.name == "rsi2")) is None   # not registered yet
    cls = sv.backtests.load_strategy("ir:rsi2")
    with sv.meta.session() as s:
        row = s.scalar(select(LabStrategy).where(LabStrategy.name == "rsi2"))
    assert row is not None and row.tags == "builtin" and cls.schema()["kind"] == "ir"
    assert sv.backtests.install_builtin_ir("rsi2") == ["rsi2"]       # idempotent (same content -> same version)
    assert len(sv.lab.get_strategy(row.id)["versions"]) == 1
    # a user's own strategy of that name wins over the built-in
    sv.lab.create_strategy("buy_hold", json.load(open(os.path.join(IR_DIR, "rsi2.json"))))
    assert sv.backtests.install_builtin_ir("buy_hold") == []


# ------------------------------------------------------------------------------------------------ parity
@pytest.mark.parametrize("name,params", [
    ("buy_hold", {"pct": 30.0}),
    ("rsi2", {"entry": 20.0}),
    # with a stop: an entry signalled WHILE in a position that is stopped out at the next open is filled at that open
    # by the Python engine (flat at the FILL) and dropped by hbt (flat at the DECISION: the only rule a live route
    # can follow without risking a doubled position) - ADR-003
    pytest.param("rsi2", {"entry": 20.0, "stop_atr": 2.0}, id="rsi2-stop"),
    ("trend_allocation", {"n": 50, "pct": 30.0}),
    ("trend_allocation", {"n": 50, "source": 1, "pct": 30.0, "ma": 2}),
    ("breakout_delivery", {"use_delivery": False}),
    ("breakout_delivery", {"z_min": 0.0, "vol_k": 1.0}),
    ("event_risk", {"n": 50, "pct": 30.0, "days_ahead": 2}),
    ("results_drift", {"min_ret": 0.0, "close_pos": 0.5}),
])
def test_daily_ports_decide_like_the_python_originals(dsv, name, params):
    sid = dsv.seed_result["sid"]
    py_params = dict(params)
    if name == "trend_allocation":                               # IR choices are numbered
        py_params["source"] = ("index", "self")[params.get("source", 0)]
        py_params["ma"] = ("sma", "wma", "ema")[params.get("ma", 0)]
    runs = {}
    for strat, p in ((name, py_params), ("ir:" + name, params)):
        r = dsv.backtests.create_backtest({"strategy": strat, "snapshot_id": sid, "start": "2023-06-01",
                                           "end": END.isoformat(), "extras": [], "params": p})
        run = _wait(dsv, r["run_id"])
        assert run["status"] == "succeeded", run
        runs[strat] = (run, _entries(dsv, r["run_id"]))
    (py, a), (ir, b) = runs[name], runs["ir:" + name]
    if "stop_atr" in params:
        # every difference is that pattern: the Python entry at day d, the IR entry at the next session(s)
        later = {(k, s_, d + j) for k, s_, d in a - b for j in range(1, 5)}
        assert a and b - a <= later, (sorted(a - b)[:5], sorted(b - a)[:5])
        assert len(a & b) / len(a | b) >= 0.7
    else:
        assert a and a == b, (sorted(a - b)[:5], sorted(b - a)[:5])   # the same entries: instrument, side, day
    assert ir["metrics"]["final"] == pytest.approx(py["metrics"]["final"], rel=0.03)


def _seed_intraday(sv):
    seed_universe(sv, ["AAA", "BBB"], date(2024, 1, 1), END, minute_from=date(2025, 1, 1))
    sv.backtests.update_config({"workers": 2, "holdout_months": 0})
    sid = sv.backtests.snapshots.build({"kind": "symbols", "symbols": ["NSE:AAA", "NSE:BBB"]}, date(2025, 1, 2), END,
                                       ("D", "1m"), warmup_days=400)["snapshot_id"]
    return {"sid": sid}


def test_intraday_ports_decide_like_the_python_originals(make_services):
    sv = make_services(seed=("ir-builtins-intraday", _seed_intraday))
    sv.backtests.start()
    sid = sv.seed_result["sid"]
    ex = {"capital": 100_000}
    # orb_cpr: identical entries (instrument, side, MINUTE), fills within 0.1 %
    (py, a), (ir, b) = _pair(sv, sid, "orb_cpr", {"cpr_max_pct": 2.0}, "2025-01-06", minute=True, execution=ex)
    assert a and a == b, (sorted(a - b)[:5], sorted(b - a)[:5])
    assert ir["metrics"]["final"] == pytest.approx(py["metrics"]["final"], rel=0.001)
    # mtf_pullback: the same entries, except that the Python engine carries an MIS entry decided on the session's
    # last bar into the next morning; hbt cancels every MIS order at the session end (documented difference)
    (py, a), (ir, b) = _pair(sv, sid, "mtf_pullback", {}, "2025-01-06", minute=True, execution=ex)
    assert b and b <= a
    assert all((m % 1440) == 9 * 60 + 15 for _, _, m in a - b)   # only next-open fills of last-bar decisions
