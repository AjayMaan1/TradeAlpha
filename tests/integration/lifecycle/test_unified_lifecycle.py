"""Unification U4: ONE paper lifecycle. An IR strategy (hbt Lab) goes through the deployment lifecycle (draft ->
signals -> shadow -> paper -> eligible_live; live refused until the Execution Gateway's live route) and runs on the
hbt per-bar host at every stage: as-seen journal, OMS, guardrails, nightly parity, broker-side protective stops.
The host's decisions are the deployment's signals (one delivery pipeline), the promotion gate reads its evidence
from the host's journal, and the flagfix1 phase-exit properties (parity 100 %, no duplicate orders, completeness)
hold inside the lifecycle."""
from __future__ import annotations

from datetime import datetime

import httpx
import numpy as np
import pytest
from sqlalchemy import func, select

from tradealpha.lifecycle.gates.drills import DRILL_IR, store_bars, synthetic_bars, trading_days
from tradealpha.lifecycle.signals.common import SignalError
from tradealpha.platform.db.lab_models import LabDecision, LabOrder, LabPaperMember, LabPaperSession
from tradealpha.platform.db.models import Deployment, Instrument, Signal, SignalDelivery
from tradealpha.platform.time.timeutil import IST

pytestmark = pytest.mark.trading_mock_matching     # the mock OpenAlgo's matching calibration


@pytest.fixture
def world(make_v2, mock_server):
    client, sv, _ = make_v2(now="2025-01-01T10:00:00+05:30")
    days = trading_days(sv, datetime(2025, 1, 6).date(), 9)
    warm, play = days[:4], days[4:]
    sv.lab.create_strategy("hostema", DRILL_IR)
    with sv.meta.session() as s:
        inst = Instrument(symbol="DRILLA", exchange="NSE", name="DRILLA")
        s.add(inst)
        s.commit()
        iid = inst.id
    full = synthetic_bars(warm + play, seed=11)
    lo = int(datetime.combine(play[0], datetime.min.time(), IST).timestamp())
    store_bars(sv, iid, full[full["ts"] < lo].reset_index(drop=True))
    sv.clock.set(datetime.combine(warm[-1], datetime.min.time().replace(hour=16), IST))
    by_day = {d: full[(full["ts"] >= int(datetime.combine(d, datetime.min.time(), IST).timestamp())) &
                      (full["ts"] < int(datetime.combine(d, datetime.min.time(), IST).timestamp()) + 86_400)]
              .reset_index(drop=True) for d in play}
    httpx.post(mock_server.url + "/__admin/trading", json={"reset": True, "analyze": True})
    return client, sv, mock_server, by_day


def _member(sv, sid):
    with sv.meta.session() as s:
        return s.scalar(select(LabPaperMember.id).where(LabPaperMember.session_id == sid))


def _play(sv, mock, sid, bars, sync=False):
    mid = _member(sv, sid)
    for k in range(len(bars)):
        px = float(bars["close"].iloc[k])
        httpx.post(mock.url + "/__admin/quote", json={"symbol": "DRILLA", "ltp": px, "bid": px - 0.05, "ask": px + 0.05})
        sv.lab_paper.replay(sid, {mid: bars.iloc[k:k + 1]})
        if sync:
            sv.lab_paper.sync(sid)


def test_u4_an_ir_idea_goes_through_the_one_lifecycle_on_the_hbt_host(world):
    client, sv, mock, by_day = world
    days = list(by_day)
    sv.backtests.freeze("ir:hostema")
    sv.signals.update_config({"promotion": {"shadow_sessions": 2, "paper_after_shadow_sessions": 1}})
    dep = sv.signals.create({"strategy": "ir:hostema", "name": "host ema",
                             "universe": {"kind": "symbols", "symbols": ["NSE:DRILLA"]},
                             "book": {"capital": 1_000_000, "warmup_days": 4},
                             "delivery": {"webhook_url": "https://hooks.example/x"}})
    assert dep["runtime"] == "bar_host" and dep["stage"] == "draft" and dep["host"] is None
    # draft -> signals: the P3 evidence criteria apply unchanged (no evidence here -> forced with a reason)
    gate = client.get(f"/api/v2/signals/deployments/{dep['id']}/gate").get_json()
    assert {c["criterion"] for c in gate["criteria"]} >= {"walk-forward group", "backtest run", "PBO"}
    sv.signals.promote(dep["id"], {"force": True, "reason": "test: evidence checked elsewhere"})
    d = sv.signals.get(dep["id"])
    host = d["host"]
    assert d["stage"] == "signals" and host["mode"] == "paper" and host["status"] == "running"
    sid1 = host["id"]

    def feed():
        return next(f for f in sv.bar_feeds.list() if f["name"] == f"deployment {dep['id'][:8]}")
    assert feed()["active"] and feed()["instruments"] == 1 and feed()["intervals"] == ["5m"]   # one follow service

    # two sessions of signals: every decision is journaled by the host AND is a signal of the deployment
    for day in days[:2]:
        _play(sv, mock, sid1, by_day[day])
        par = sv.lab_paper.parity(sid1, day)
        assert par["status"] == "ok" and par["parity_pct"] == 100.0, par
        rep = sv.lab_paper.daily_report(sid1, day)
        assert rep["completeness_pct"] == 100.0 and rep["duplicate_orders"] == 0
    with sv.meta.session() as s:
        sigs = list(s.scalars(select(Signal).where(Signal.deployment_id == dep["id"])))
        shas = {x.bars_sha256 for x in s.scalars(select(LabDecision))}
        deliveries = s.scalar(select(func.count()).select_from(SignalDelivery)) or 0
    assert sigs and all(x.fingerprint in shas for x in sigs)          # fingerprint = the as-seen bars hash
    assert {x.action for x in sigs} >= {"ENTER_LONG", "EXIT"} and deliveries == len(sigs)
    assert all(x.reason_json and '"bar_host"' in x.reason_json for x in sigs)
    n_sigs = len(sigs)
    # the host's nightly parity is the deployment's parity (one view, P4 classes)
    checks = sv.signals.parity_list(dep["id"])
    assert {c["session"] for c in checks} == {d.isoformat() for d in days[:2]}
    assert all(c["status"] == "ok" and c["compared"] > 0 and c["compared"] == c["matched"] for c in checks)
    _play(sv, mock, sid1, by_day[days[0]])                              # replayed bars: idempotent, no new signals
    with sv.meta.session() as s:
        assert s.scalar(select(func.count()).select_from(Signal)) == n_sigs

    # signals -> shadow: evidence from the host's journal; passes without force
    gate = client.get(f"/api/v2/signals/deployments/{dep['id']}/gate").get_json()
    assert gate["passed"], gate
    assert gate["host_evidence"]["sessions"] == 2 and gate["host_evidence"]["logic"] == 0
    sv.signals.promote(dep["id"], {})
    d = sv.signals.get(dep["id"])
    assert d["stage"] == "shadow" and d["host"]["id"] == sid1           # same route: the session is re-tagged
    _play(sv, mock, sid1, by_day[days[2]])

    # shadow -> paper: no band evidence (no backtest run attached) -> forced; a NEW analyzer session (G2 route)
    with pytest.raises(SignalError, match="did not pass"):
        sv.signals.promote(dep["id"], {})
    sv.signals.promote(dep["id"], {"force": True, "reason": "test"})
    d = sv.signals.get(dep["id"])
    sid2 = d["host"]["id"]
    assert d["stage"] == "paper" and d["host"]["mode"] == "analyzer" and sid2 != sid1
    with sv.meta.session() as s:
        assert s.get(LabPaperSession, sid1).status == "stopped"
        assert s.get(LabPaperSession, sid2).deployment_id == dep["id"]
    _play(sv, mock, sid2, by_day[days[3]], sync=True)
    orders = client.get("/api/v2/trading/orders?limit=2000").get_json()
    mine = [o for o in orders if o["path"] == "lab_paper" and o["owner"] == sid2]
    assert mine and all(o["deployment_id"] == dep["id"] and o["mode"] == "analyzer" for o in mine)
    assert any(o["role"] == "protective_stop" for o in mine)            # U3 / D3 inside the lifecycle
    with sv.meta.session() as s:
        dups = s.execute(select(LabOrder.idem_key).group_by(LabOrder.idem_key).having(func.count() > 1)).all()
    assert dups == []
    eq = sv.signals.journal(dep["id"])["execution_quality"]             # one execution-quality report (the OMS)
    assert eq["model"] == "quote_mid" and isinstance(eq["rejections"], int) and "fills_compared" in eq

    # the Lab page cannot start / delete a session the lifecycle manages (stopping stays possible)
    r = client.post(f"/api/v2/lab/paper/sessions/{sid1}/start")
    assert r.status_code == 409 and "Signals" in r.get_json()["error"]["message"]
    assert client.delete(f"/api/v2/lab/paper/sessions/{sid2}").status_code == 409

    # pause / resume follow the deployment; the route refuses to restart while the kill switch is on
    sv.signals.pause(dep["id"])
    assert sv.signals.get(dep["id"])["host"]["status"] == "stopped" and not feed()["active"]
    sv.signals.resume(dep["id"])
    assert sv.signals.get(dep["id"])["host"]["status"] == "running" and feed()["active"]

    # paper -> eligible_live: the hbt Lab's drills and readiness sign-off are gate inputs now
    gate = client.get(f"/api/v2/signals/deployments/{dep['id']}/gate").get_json()
    names = {c["criterion"]: c for c in gate["criteria"]}
    assert not names["operational drills passed (30 days)"]["pass"]
    assert not names["readiness sign-off on record"]["pass"]
    assert "explicit approval" in names
    sv.signals.promote(dep["id"], {"force": True, "reason": "test"})
    assert sv.signals.get(dep["id"])["host"]["id"] == sid2              # same analyzer route

    # live (Phase 4): the Execution Gateway's live route - the live gate + the hbt Lab's live readiness, never forced
    with pytest.raises(SignalError, match="live drills passed.*clock offset measured.*reconciliation record"):
        sv.signals.promote(dep["id"], {"to": "live", "confirm": "host ema"})
    with pytest.raises(SignalError, match="cannot be forced"):
        sv.signals.promote(dep["id"], {"to": "live", "confirm": "host ema", "force": True, "reason": "x"})

    # one kill switch (ADR-006, a latch): the hosted session keeps running with its feed - exits, protective stops and
    # square-off keep working - while entries are refused at the OMS; a paused deployment cannot resume
    sv.signals.kill_switch(True, "test")
    with sv.meta.session() as s:
        assert s.get(LabPaperSession, sid2).status == "running"
    sv.signals.tick_runs()
    d = sv.signals.get(dep["id"])
    assert d["status"] == "active" and feed()["active"]
    sv.signals.pause(dep["id"])
    with pytest.raises(SignalError, match="kill switch"):
        sv.signals.resume(dep["id"])
    sv.signals.kill_switch(False, "test")
    sv.signals.resume(dep["id"])
    assert sv.signals.get(dep["id"])["host"]["status"] == "running"


def test_u4_a_session_refused_by_the_guardrails_fails_the_promotion_and_changes_nothing(world):
    _, sv, _, _ = world
    no_stop = {**DRILL_IR, "name": "nostop", "rules": [DRILL_IR["rules"][0],
                                                       {"id": "entry", "when": DRILL_IR["rules"][1]["when"],
                                                        "do": {"enter_long": {}}}]}
    sv.lab.create_strategy("nostop", no_stop)
    sv.backtests.freeze("ir:nostop")
    dep = sv.signals.create({"strategy": "ir:nostop", "universe": {"kind": "symbols", "symbols": ["NSE:DRILLA"]}})
    with pytest.raises(SignalError, match="refused"):
        sv.signals.promote(dep["id"], {"force": True, "reason": "test"})
    d = sv.signals.get(dep["id"])
    assert d["stage"] == "draft" and d["host"] is None


def test_u4_deployments_are_pinned_to_their_ir_version_and_never_replayed(world):
    _, sv, _, _ = world
    sv.backtests.freeze("ir:hostema")
    dep = sv.signals.create({"strategy": "ir:hostema", "universe": {"kind": "symbols", "symbols": ["NSE:DRILLA"]}})
    v2 = {**DRILL_IR, "params": {**DRILL_IR["params"], "fast": {**DRILL_IR["params"]["fast"], "default": 4}}}
    sv.lab.add_version(sv.lab.resolve_version("hostema").strategy_id, v2)
    d = sv.signals.get(dep["id"])
    assert d["code_current"] is False                                  # a newer version exists ...
    cls = sv.backtests.load_strategy("ir:hostema", d["code_sha"])
    assert cls.schema()["code_sha"] == d["code_sha"] and cls.ir_version == 1   # ... but the deployment runs v1
    with pytest.raises(SignalError, match="no manual run"):
        sv.signals.run_now(dep["id"])
    with sv.meta.session() as s:
        assert s.get(Deployment, dep["id"]).runtime == "bar_host"


def test_u4_a_frozen_cohort_becomes_draft_deployments(world):
    import json

    from tradealpha.platform.db.lab_models import LabCohort
    _, sv, _, _ = world
    v = sv.lab.resolve_version("hostema")
    with sv.meta.session() as s:
        c = LabCohort(name="jan", cutoff=datetime(2025, 1, 1).date(),
                      members_json=json.dumps([{"strategy_version_id": v.id, "params": {"fast": 5}}]))
        s.add(c)
        s.commit()
        cid = c.id
    out = sv.signals.deploy_cohort(cid, {"universe": {"kind": "symbols", "symbols": ["NSE:DRILLA"]}})
    assert len(out) == 1 and out[0]["strategy"] == "ir:hostema@1" and out[0]["params"]["fast"] == 5
    assert out[0]["runtime"] == "bar_host" and out[0]["code_sha"] == v.ir_sha256
    assert any(e["kind"] == "sourced" for e in sv.signals.get(out[0]["id"])["events"])


def test_u4_a_ported_builtin_with_live_index_data_runs_on_the_host(make_services):
    """ir:trend_allocation (a flagfix2 built-in ported to IR) reads $nifty_close: the paper host computes it from the
    LIVE store with the backtest's portal rules, and the hosted session uses the unified ExecSpec (ADR-001)."""
    from tests.support.lab.bt_synth import daily_bars
    from tradealpha.lifecycle.gates.drills import trading_days
    from tradealpha.platform.db.models import Instrument
    from tradealpha.platform.time.intervals import INTERVALS
    sv = make_services(now="2024-06-03T16:00:00+05:30")
    days = trading_days(sv, datetime(2023, 6, 1).date(), 260)
    warm, play = days[:-8], days[-8:]
    ids = {}
    with sv.meta.session() as s:
        for sym, ex in (("DRILLA", "NSE"), ("NIFTY", "NSE_INDEX")):
            inst = Instrument(symbol=sym, exchange=ex, name=sym, instrument_type="EQ" if ex == "NSE" else "IDX")
            s.add(inst)
            s.flush()
            ids[sym] = inst.id
        s.commit()
    stock = daily_bars(days, seed=3, start_px=500.0)
    index = daily_bars(days, seed=4, start_px=20000.0, drift=0.001)
    n_warm = len(warm)
    sv.store.upsert(ids["DRILLA"], INTERVALS["D"].id, stock.iloc[:n_warm].reset_index(drop=True))
    sv.store.upsert(ids["NIFTY"], INTERVALS["D"].id, index)                 # the index is current (live feed)
    sv.clock.set(datetime.combine(warm[-1], datetime.min.time().replace(hour=17), IST))
    sv.backtests.freeze("ir:trend_allocation")                              # the shipped port, registered on use
    sv.signals.update_config({"guardrails": {"require_stop": False}})      # E1 has no stop (G-IN-1 would refuse)
    dep = sv.signals.create({"strategy": "ir:trend_allocation", "params": {"n": 20, "pct": 50.0},
                             "universe": {"kind": "symbols", "symbols": ["NSE:DRILLA"]},
                             "book": {"capital": 1_000_000, "warmup_days": 200}})
    sv.signals.promote(dep["id"], {"force": True, "reason": "test"})
    sid = sv.signals.get(dep["id"])["host"]["id"]
    sess = sv.lab_paper.get_session(sid)
    spec = sess["exec"]["spec"]
    assert spec["participation_remainder"] == "cancel_entries" and spec["slippage_clip"] is True   # ADR-001 mapping
    mid = sess["members"][0]["id"]
    sv.lab_paper.replay(sid, {mid: stock.iloc[n_warm:].reset_index(drop=True)})
    with sv.meta.session() as s:
        decs = list(s.scalars(select(LabDecision).where(LabDecision.member_id == mid)))
    assert len(decs) == len(play) and all(d.bars_sha256 != "-" for d in decs)     # no host error
    res, ds = sv.lab_paper.run_member(sid, mid)
    nc = ds.extras["nifty_close"]
    assert np.isfinite(nc[-1]) and nc[-1] == pytest.approx(float(index["close"].iloc[-1]))   # same-day close (D rule)
    assert np.isfinite(nc[:-1]).sum() > 100
