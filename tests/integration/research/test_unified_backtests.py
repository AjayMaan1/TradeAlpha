"""Unification U2 - ONE backtest process: IR strategies (hbt Lab, Rust kernel) run through the same pipeline as the
Python strategies (request, lab snapshot, compute budget, run registry, sweeps, evaluation, API)."""
import json
import os
import time
from datetime import date

import pytest

from tests.support.lab.bt_synth import seed_universe
from tests.support.paths import IR_FIXTURES

pytestmark = pytest.mark.trading_mock_matching     # the mock OpenAlgo's matching calibration

FIX = IR_FIXTURES
START, END = date(2022, 1, 3), date(2025, 3, 28)


def _seed(sv):
    seed_universe(sv, ["AAA", "BBB"], START, END)
    sv.backtests.update_config({"workers": 2, "holdout_months": 0})


@pytest.fixture
def usv(make_services):
    sv = make_services(seed=("unified-bt", _seed))
    sv.backtests.start()                                    # the pool is started explicitly in tests
    ir = json.load(open(os.path.join(FIX, "donchian_breakout_daily.json")))
    sv.lab.create_strategy("donchian", ir)
    return sv


def _wait(sv, run_id, timeout=180):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = sv.backtests.run(run_id)
        if r["status"] not in ("queued", "running"):
            return r
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_ir_strategies_are_listed_with_the_python_ones(usv):
    names = {s["name"]: s for s in usv.backtests.strategies()}
    assert names["ir:donchian"]["kind"] == "ir" and names["ir:donchian"]["engine"] == "hbt"
    assert names["rsi2"]["kind"] == "python" and names["rsi2"]["engine"] == "sim"
    assert names["ir:donchian"]["code_sha"] == usv.lab.list_strategies()[0]["latest_sha256"]


def test_an_ir_run_through_the_unified_pipeline_equals_the_kernel(usv):
    """One symbol: the P3 pipeline's result (registry, artefacts, metrics) is the hbt kernel's, trade for trade."""
    from tradealpha.research.kernel import ir_engine, load_hbt
    from tradealpha.research.kernel.rules import ExecConfig
    from tradealpha.research.snapshots.portal import DataPortal, PortalConfig
    hbt = load_hbt()
    sid = usv.backtests.snapshots.build({"kind": "symbols", "symbols": ["NSE:AAA"]}, date(2023, 1, 2), END, ("D",))[
        "snapshot_id"]
    r = usv.backtests.create_backtest({"strategy": "ir:donchian", "snapshot_id": sid, "start": "2023-01-02",
                                       "end": "2025-03-28", "extras": []})
    run = _wait(usv, r["run_id"])
    assert run["status"] == "succeeded", run
    m = run["metrics"]
    assert m["engine"] == "hbt" and m["trades"] > 0 and run["code_sha"] == usv.lab.list_strategies()[0]["latest_sha256"]
    trades = usv.backtests.artefact(r["run_id"], "trades")
    # the same data, spec and costs straight into the kernel
    portal = DataPortal(usv.backtests.root, sid, PortalConfig(view="raw"))
    ir = json.load(open(os.path.join(FIX, "donchian_breakout_daily.json")))
    prog = hbt.ir.compile(json.dumps(ir))
    ds, frame, meta = ir_engine.dataset_from_portal(hbt, portal, "NSE:AAA", "D", "adjusted", "2023-01-02",
                                                    "2025-03-28", (), ir_engine.session_map(portal))
    ex = ExecConfig(product="CNC")
    slip, _ = ir_engine._slippage_fraction(frame, "2023-01-02", ex)
    # the ADR-001/003 execution model, exactly as the pipeline maps it
    spec = hbt.exec_spec(**ir_engine.unified_spec(ex), **ir_engine.slippage_spec(ex, slip))
    direct = hbt.run(prog, ds, spec=spec, inst=hbt.instrument(tick=5),
                     costs=hbt.cost_model("EQ_DELIVERY", broker="angelone"), cash=1_000_000.0)
    assert len(trades) == len(direct.trades)
    assert [round(t["net_pnl"], 2) for t in trades] == [round(p / 100, 2) for p in direct.trades["pnl"]]
    assert m["final"] == pytest.approx(float(direct.equity.iloc[-1]), abs=0.01)
    rerun = usv.backtests.rerun(r["run_id"])                # byte-identical re-run verification, P3 machinery
    again = _wait(usv, rerun["run_id"])
    assert again["metrics"]["verify"]["identical"] is True


def test_a_multi_symbol_ir_run_is_one_account_the_rust_portfolio_mode(usv):
    """Two symbols through the P3 pipeline: ONE account (shared cash, equal-weight slots, max positions), equal to
    hbt.run_portfolio on the same inputs; the kernel's portfolio equity is the run's equity."""
    from tradealpha.research.kernel import ir_engine, load_hbt
    from tradealpha.research.kernel.rules import ExecConfig
    from tradealpha.research.snapshots.portal import DataPortal, PortalConfig
    hbt = load_hbt()
    sid = usv.backtests.snapshots.build({"kind": "symbols", "symbols": ["NSE:AAA", "NSE:BBB"]}, date(2023, 1, 2),
                                        END, ("D",))["snapshot_id"]
    r = usv.backtests.create_backtest({"strategy": "ir:donchian", "snapshot_id": sid, "start": "2023-01-02",
                                       "end": "2025-03-28", "extras": [], "execution": {"max_positions": 1}})
    run = _wait(usv, r["run_id"])
    assert run["status"] == "succeeded", run
    u = run["metrics"]["universe"]
    assert u["mode"] == "portfolio" and u["instruments"] == 2 and u["max_positions"] == 1 and u["peak_open"] == 1
    assert not [w for w in run.get("warnings") or [] if "slices" in w]
    trades = usv.backtests.artefact(r["run_id"], "trades")
    # never two positions at once (max_positions 1 on one account)
    spans = sorted((t["entry_t"], t["exit_t"]) for t in trades)
    assert all(b[0] >= a[1] for a, b in zip(spans, spans[1:], strict=False))
    # the same inputs straight into the kernel's portfolio mode
    portal = DataPortal(usv.backtests.root, sid, PortalConfig(view="raw"))
    prog = hbt.ir.compile(json.dumps(json.load(open(os.path.join(FIX, "donchian_breakout_daily.json")))))
    ex = ExecConfig(product="CNC")
    dss, specs = {}, {}
    for k in ("NSE:AAA", "NSE:BBB"):
        ds, frame, _ = ir_engine.dataset_from_portal(hbt, portal, k, "D", "adjusted", "2023-01-02", "2025-03-28", (),
                                                     ir_engine.session_map(portal))
        slip, _ = ir_engine._slippage_fraction(frame, "2023-01-02", ex)
        dss[k], specs[k] = ds, ir_engine.slippage_spec(ex, slip)
    direct = hbt.run_portfolio(prog, dss, spec=hbt.exec_spec(**ir_engine.unified_spec(ex)),
                               specs=specs, plan=hbt.order_plan(sizing="pct_equity", sizing_value=1.0),
                               insts={k: hbt.instrument(tick=5) for k in dss},
                               costs=hbt.cost_model("EQ_DELIVERY", broker="angelone"), cash=1_000_000.0,
                               max_positions=1)
    assert len(trades) == len(direct.trades) and direct.peak_open == 1
    assert run["metrics"]["final"] == pytest.approx(float(direct.equity.iloc[-1]), abs=0.01)
    assert sum(x.rejects["max_positions"] for x in direct.legs.values()) > 0     # the cap bit


def test_sweeps_holdout_and_freeze_work_for_ir_unchanged(usv):
    from tradealpha.research.runs.backtests import BacktestError
    sid = usv.backtests.snapshots.build({"kind": "symbols", "symbols": ["NSE:AAA", "NSE:BBB"]}, date(2023, 1, 2), END,
                                        ("D",))["snapshot_id"]
    ir = json.load(open(os.path.join(FIX, "donchian_breakout_daily.json")))
    grid = {k: [p["default"], p.get("max", p["default"])] for k, p in list((ir.get("params") or {}).items())[:1]}
    body = {"strategy": "ir:donchian", "snapshot_id": sid, "start": "2023-01-02", "end": "2025-03-28", "extras": []}
    g = usv.backtests.create_group("sweep", {**body, "grid": grid} if grid else {**body, "random": {"n": 2, "space": {}}})
    end = time.monotonic() + 240
    while time.monotonic() < end and usv.backtests.group(g["group_id"])["status"] in ("queued", "running"):
        time.sleep(0.3)
    grp = usv.backtests.group(g["group_id"])
    assert grp["status"] == "succeeded", grp
    usv.backtests.update_config({"holdout_months": 12})
    with pytest.raises(BacktestError):                      # unfrozen IR code cannot see the hold-out either
        usv.backtests.create_backtest({"strategy": "ir:donchian", "snapshot_id": sid, "start": "2023-01-02",
                                       "end": "2025-03-28"}, enqueue=False)
    fr = usv.backtests.freeze("ir:donchian")
    assert fr["lookahead"]["method"] == "ir_causal_by_construction"
    assert usv.backtests.create_backtest({"strategy": "ir:donchian", "snapshot_id": sid, "start": "2023-01-02",
                                          "end": "2025-03-28"}, enqueue=False)["run_id"]


def test_python_and_ir_runs_share_one_registry_and_one_result_format(usv):
    sid = usv.backtests.snapshots.build({"kind": "symbols", "symbols": ["NSE:AAA", "NSE:BBB"]}, date(2023, 1, 2), END,
                                        ("D",))["snapshot_id"]
    ids = [usv.backtests.create_backtest({"strategy": s, "snapshot_id": sid, "start": "2023-06-01",
                                          "end": "2025-03-28", "extras": []})["run_id"]
           for s in ("ir:donchian", "rsi2")]
    runs = [_wait(usv, i) for i in ids]
    assert all(r["status"] == "succeeded" for r in runs), runs
    listed = {r["id"]: r for r in usv.backtests.runs()}
    assert set(ids) <= set(listed)
    keys = [set(r["metrics"]) for r in runs]
    assert {"sharpe", "max_drawdown_pct", "trades", "by_year", "monthly"} <= keys[0] & keys[1]
    for i in ids:
        assert usv.backtests.artefact(i, "equity") and usv.backtests.chart(i, "NSE:AAA")["bars"]


def test_an_intraday_ir_sweep_mis_square_off_and_mtf(make_services):
    """5m IR strategy with a daily filter (MTF), MIS product: 5m bars rolled up from 1m inside the lab snapshot, the
    ADR-001 square-off (15:10), a parameter sweep through the P3 group machinery."""
    sv = make_services(start=False)
    seed_universe(sv, ["AAA"], date(2024, 10, 1), date(2025, 3, 28), minute_from=date(2025, 1, 1))
    sv.backtests.update_config({"workers": 2, "holdout_months": 0})
    sv.backtests.start()
    sv.lab.create_strategy("ema_x", json.load(open(os.path.join(FIX, "ema_cross_atr_bracket.json"))))
    sid = sv.backtests.snapshots.build({"kind": "symbols", "symbols": ["NSE:AAA"]}, date(2025, 1, 2), date(2025, 3, 28),
                                       ("D", "1m"), warmup_days=60)["snapshot_id"]
    # capital sized to the synthetic liquidity (~20k shares per 5m bar): with the 5 % participation cap a larger
    # position could not be squared off by the close (it would be force-closed at the next open)
    g = sv.backtests.create_group("sweep", {"strategy": "ir:ema_x", "snapshot_id": sid, "start": "2025-01-02",
                                            "end": "2025-03-28", "extras": [], "grid": {"fast": [5, 9]},
                                            "execution": {"capital": 50_000}})
    end = time.monotonic() + 300
    while time.monotonic() < end and sv.backtests.group(g["group_id"])["status"] in ("queued", "running"):
        time.sleep(0.3)
    grp = sv.backtests.group(g["group_id"])
    assert grp["status"] == "succeeded", grp
    members = [r for r in sv.backtests.runs(group_id=g["group_id"])]
    assert len(members) == 2 and all(r["status"] == "succeeded" for r in members)
    assert members[0]["metrics"]["trades"] != members[1]["metrics"]["trades"] or \
        members[0]["metrics"]["final"] != members[1]["metrics"]["final"]          # the parameter matters
    for r in members:
        for t in sv.backtests.artefact(r["id"], "trades"):
            hhmm = ((t["exit_t"] + 19_800) % 86_400) // 60
            # the square-off starts at 15:10 (ADR-001); an exit larger than 5 % of a 1m bar's volume is sliced over
            # the next minutes (ADR-003, as in the Python engine) - always flat before the broker's window (15:15)
            assert hhmm < 15 * 60 + 15, t
            assert t["exit_reason"] != "forced", t


def test_one_append_only_trial_registry_counts_every_flow(usv):
    """P3 sweeps land in the same append-only registry as the hbt research trials; the next DSR deflates by all."""
    import sqlalchemy
    from sqlalchemy import text
    sid = usv.backtests.snapshots.build({"kind": "symbols", "symbols": ["NSE:AAA"]}, date(2023, 1, 2), END, ("D",))[
        "snapshot_id"]
    before = usv.backtests.registry_count()

    def sweep():
        g = usv.backtests.create_group("sweep", {"strategy": "rsi2", "snapshot_id": sid, "start": "2023-06-01",
                                                 "end": "2025-03-28", "extras": [], "grid": {"entry": [5, 10, 20]}})
        end = time.monotonic() + 240
        while time.monotonic() < end and usv.backtests.group(g["group_id"])["status"] in ("queued", "running"):
            time.sleep(0.3)
        return usv.backtests.group(g["group_id"])
    first = sweep()
    assert first["status"] == "succeeded" and usv.backtests.registry_count() == before + 3
    second = sweep()
    assert second["result"]["registry_trials_before"] == before + 3
    assert second["result"]["dsr"]["trials"] == before + 6          # deflated by every trial, not only its own 3
    with usv.meta.engine.begin() as c:
        n_p3 = c.execute(text("SELECT count(*) FROM lab_trials WHERE source = 'p3_sweep' AND strategy = 'rsi2'")).scalar()
        assert n_p3 == 6
    for stmt in ("UPDATE lab_trials SET sharpe = 9", "DELETE FROM lab_trials"):   # the triggers survived 0017
        with pytest.raises(sqlalchemy.exc.DatabaseError), usv.meta.engine.begin() as c:
            c.execute(text(stmt))


@pytest.mark.parametrize("sr,t,n,var,skew,kurt", [(0.1581, 1250, 100, 0.002, -3, 10), (0.05, 500, 1, 0.001, 0, 3),
                                                   (0.08, 252, 10_000, 0.004, 0.5, 4), (-0.02, 300, 20, 0.001, 0, 3)])
def test_one_statistics_library_both_dsr_implementations_agree(sr, t, n, var, skew, kurt):
    """The hbt research library (no app imports - it runs standalone in workers) and app.lab.eval must give the same
    deflated Sharpe: one set of statistics for every flow."""
    from tradealpha.research.evaluation import validation as v2
    from tradealpha.research.kernel import load_hbt
    load_hbt()
    from hbt import research
    a = research.deflated_sharpe(sr, t, n, var, skew=skew, kurt=kurt)
    b = v2.deflated_sharpe(sr, t, skew, kurt, n, var)
    assert a["dsr"] == pytest.approx(b["dsr"], abs=1e-9) and a["sr0"] == pytest.approx(b["sr0"], abs=1e-12)
