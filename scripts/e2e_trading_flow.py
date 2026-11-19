#!/usr/bin/env python3
"""
End-to-end trading flow (trading-engine Phases 2-5) on a mock OpenAlgo - research node + trader node:

    idea (signed IdeaSpec) -> validation -> evaluation (backtest, walk-forward, sweep, DSR / PBO gate) -> deployment
    -> signals -> shadow (research node, simulated) -> signed bundle with the research node's paper gate
    -> trader node: paper on the OpenAlgo Analyzer -> broker reconciliation -> drills (paper + live) + sign-off
    -> eligible_live (approval) -> live gate (switch, auth, limits, Analyzer OFF, NTP clock, tariffs, drills,
       reconciliation record) -> live dry-run session (journaled, never sent) -> live session (real route, Analyzer
       OFF, margin checks, stepped caps) -> end-of-day reconciliation + contract note (charges) -> pilot report
    -> TradeEvents relayed trader -> research (HMAC, exactly once, chain re-verified) -> audit chain verify + tamper
       detection -> secret rotation with grace -> automatic demotion on broker rejects (flatten) -> role guards

Everything runs in-process against tests/mock_provider (the OpenAlgo mock: orders, books, Analyzer toggle, funds,
margin) and a local SNTP responder; nothing touches the network. Exit code 0 only if every step passed.

    python scripts/e2e_trading_flow.py [--workdir DIR] [--report PATH.json] [--quiet]
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import traceback
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = ROOT                       # the project: `tests` (the mock OpenAlgo) is imported from here
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
os.environ.setdefault("HISTORIFY_SECRET_BACKEND", "file")
for _k in ("HISTORIFY_ENGINE", "HISTORIFY_INSTANCE_PATH", "HISTORIFY_META_DB", "HISTORIFY_BARS_DB", "HISTORIFY_HOST",
           "HISTORIFY_AUTH", "HISTORIFY_ROLE"):
    os.environ.pop(_k, None)

NOW = "2025-03-28T16:00:00+05:30"
SYMBOLS = ("AAA", "BBB", "CCC")
INTAKE_SECRET = "e2e-intake-secret-0001"
LINK_SECRET = "e2e-node-link-secret-0001"
IR = {
    "ir_version": 1, "name": "updays", "universe": {"interval": "D"}, "data": {"adjustment": "adjusted"},
    "params": {"lb": {"type": "int", "default": 3, "min": 2, "max": 10}},
    "signals": {"m": {"fn": "sma", "args": [{"col": "close"}, {"param": "lb"}]}},
    "rules": [
        {"id": "exit", "when": {"fn": "lt", "args": [{"col": "close"}, {"sig": "m"}]}, "do": {"exit_long": {}}},
        {"id": "entry", "when": {"fn": "and", "args": [
            {"fn": "gt", "args": [{"col": "close"}, {"sig": "m"}]},
            {"fn": "eq", "args": [{"fn": "position_qty"}, 0]}]},
         "do": {"enter_long": {"size": {"pct_equity": 0.3}, "bracket": {"stop": {"pct": 0.05}}}}},
    ],
    "product": "CNC",
}


# ------------------------------------------------------------------------------------------------ harness
class StepFailed(AssertionError):
    pass


class Report:
    def __init__(self, quiet: bool = False):
        self.steps: list[dict] = []
        self.quiet = quiet
        self.t0 = time.monotonic()

    def step(self, name: str):
        rep = self

        class _Ctx:
            def __enter__(self_):
                self_.t = time.monotonic()
                self_.detail = {}
                return self_

            def check(self_, cond, what: str):
                if not cond:
                    raise StepFailed(what)

            def __exit__(self_, et, e, tb):
                ok = et is None
                rec = {"step": name, "ok": ok, "seconds": round(time.monotonic() - self_.t, 2),
                       "detail": self_.detail}
                if not ok:
                    rec["error"] = f"{et.__name__}: {e}"
                    rec["trace"] = "".join(traceback.format_exception(et, e, tb))[-3000:]
                rep.steps.append(rec)
                if not rep.quiet:
                    mark = "PASS" if ok else "FAIL"
                    print(f"[{mark}] {name} ({rec['seconds']} s)" + ("" if ok else f"  -> {rec['error']}"), flush=True)
                    if ok and self_.detail:
                        print("       " + json.dumps(self_.detail, default=str)[:400], flush=True)
                return False                       # re-raise: the flow stops at the first failure
        return _Ctx()

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(s["ok"] for s in self.steps)

    def summary(self) -> dict:
        return {"ok": self.ok, "steps": len(self.steps), "passed": sum(s["ok"] for s in self.steps),
                "seconds": round(time.monotonic() - self.t0, 1), "results": self.steps}


class FakeNTP:
    """A local SNTP responder (RFC 4330 server mode) with a configurable offset."""

    def __init__(self, offset: float = 0.0):
        self.offset = offset
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self._stop = False
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        from tradealpha.execution.safety.clock_check import _to_ntp
        self.sock.settimeout(0.2)
        while not self._stop:
            try:
                data, addr = self.sock.recvfrom(512)
            except OSError:
                continue
            now = time.time() + self.offset
            out = bytearray(48)
            out[0], out[1] = (0 << 6) | (4 << 3) | 4, 2
            out[24:32] = data[40:48]                                   # originate = client's transmit
            struct.pack_into("!II", out, 32, *_to_ntp(now))
            struct.pack_into("!II", out, 40, *_to_ntp(now))
            self.sock.sendto(bytes(out), addr)

    def stop(self):
        self._stop = True


def _node(base: str, name: str, role: str, mock_url: str):
    from tradealpha.apps.container import Services
    from tradealpha.apps.research import create_app
    from tradealpha.platform.time.clock import FrozenClock
    path = os.path.join(base, name)
    sv = Services(path, clock=FrozenClock(datetime.fromisoformat(NOW)), role=role,
                  engine_options={"poll_s": 0.05, "backoff_base_s": 0.05, "auth_probe_s": 3600,
                                  "short_recheck_delay_s": 0.05})
    sv.settings.set("openalgo_api_host", mock_url)
    sv.settings.set("openalgo_api_key", "e2e-key-1234")
    sv.settings_changed()
    sv.engine.start()
    sv.runner.start()
    app = create_app({"HISTORIFY_SERVICES": sv, "TESTING": True}, instance_path=sv.instance_path)
    return sv, app, app.test_client()


_QUIET_LOGGERS = ("alembic", "werkzeug", "app", "historify")


def _seed(sv):
    sys.path.insert(0, APP_DIR)
    from tests.support.lab.bt_synth import seed_universe
    return seed_universe(sv, list(SYMBOLS), date(2021, 1, 4), date(2025, 3, 28))


def _next_days(sv, n: int) -> list[date]:
    cal = sv.calendar()
    out, d = [], date(2025, 3, 29)
    while len(out) < n:
        if cal.session_on("NSE", d) is not None:
            out.append(d)
        d += timedelta(days=1)
    return out


def _day_bars(sv, days):
    """Deterministic daily bars after the seeded history, the same on both nodes: {symbol: DataFrame}."""
    from tests.support.lab.bt_synth import daily_bars
    from tradealpha.platform.db.models import Instrument
    from tradealpha.platform.time.intervals import INTERVALS
    out = {}
    with sv.meta.session() as s:
        ids = {i.symbol: i.id for i in s.query(Instrument).filter(Instrument.symbol.in_(SYMBOLS))}
    for n, sym in enumerate(SYMBOLS):
        last = sv.store.read_range(ids[sym], INTERVALS["D"].id, limit=1, tail=True)
        out[sym] = daily_bars(days, seed=500 + n, start_px=float(last["close"].iloc[-1]), vol=0.02) \
            .drop(columns=["oi"])
    return out


def _play_day(sv, sid: str, bars: dict, day_idx: int, mock_url: str | None = None, sync: bool = False):
    """One daily bar per member through the host (the feed would deliver it); the mock's quotes follow the close."""
    import httpx
    g = sv.lab_paper.get_session(sid)
    by_member = {}
    for m in g["members"]:
        df = bars[m["symbol"]].iloc[day_idx:day_idx + 1].reset_index(drop=True)
        by_member[m["id"]] = df
        if mock_url:
            px = float(df["close"].iloc[0])
            httpx.post(mock_url + "/__admin/quote", json={"symbol": m["symbol"], "ltp": px, "bid": round(px - 0.05, 2),
                                                          "ask": round(px + 0.05, 2)})
    sv.lab_paper.replay(sid, by_member)
    if sync:
        sv.lab_paper.sync(sid)


def _wait_jobs(sv, kinds=("idea_validate", "idea_evaluate"), timeout=900):
    from sqlalchemy import func, select

    from tradealpha.platform.db.models import Job
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        with sv.meta.session() as s:
            left = s.scalar(select(func.count()).select_from(Job).where(
                Job.kind.in_(kinds), Job.status.in_(("queued", "running"))))
        if not left:
            return
        time.sleep(0.3)
    raise StepFailed("idea jobs did not finish in time")


def _counts(sv, sid):
    from sqlalchemy import func, select

    from tradealpha.platform.db.lab_models import LabOrder
    with sv.meta.session() as s:
        return dict(s.execute(select(LabOrder.status, func.count()).where(LabOrder.session_id == sid)
                              .group_by(LabOrder.status)).all())


# ------------------------------------------------------------------------------------------------ the flow
def run(workdir: str | None = None, quiet: bool = False) -> dict:
    import logging

    import httpx
    from sqlalchemy import func, select

    from tests.support.mock_provider.server import MockServer
    from tradealpha.ideas import spec as sp
    from tradealpha.platform.db.lab_models import LabOrder, LabPaperSession, LabSignoff
    from tradealpha.platform.db.models import AuditLog, BrokerReconciliation, TradeEvent
    rep = Report(quiet)
    levels = {n: logging.getLogger(n).level for n in _QUIET_LOGGERS}
    for n in _QUIET_LOGGERS:
        logging.getLogger(n).setLevel(logging.WARNING)
    # the mock's matching calibration has no order / book rate limits: pace the order route for the mock
    from tests.support.mock_provider import profile as mock_profile
    old_profile = os.environ.get("HISTORIFY_PROVIDER_PROFILE")
    os.environ["HISTORIFY_PROVIDER_PROFILE"] = mock_profile.register()
    own_tmp = workdir is None
    base = workdir or tempfile.mkdtemp(prefix="hfy-e2e-")
    mock = MockServer().start()
    mock.configure(now=NOW)
    ntp = FakeNTP(offset=0.0)
    nodes = []
    try:
        with rep.step("0. environment: mock OpenAlgo, SNTP responder, research + trader nodes") as st:
            research, r_app, rc = _node(base, "research", "research", mock.url)
            trader, t_app, tc = _node(base, "trader", "trader", mock.url)
            nodes += [research, trader]
            st.check(research.role == "research" and trader.role == "trader", "roles")
            for sv in (research, trader):
                _seed(sv)
                sv.ideas.set_secret("trade_events_webhook_secret", LINK_SECRET, "e2e")   # the node-link secret
            days = _next_days(research, 6)
            bars = _day_bars(research, days)
            bars_t = _day_bars(trader, days)
            st.check(all(bars[s].equals(bars_t[s]) for s in SYMBOLS), "both nodes see the same market")
            httpx.post(mock.url + "/__admin/trading", json={"reset": True, "analyze": True})
            st.detail = {"mock": mock.url, "ntp": f"127.0.0.1:{ntp.port}", "play_days": [d.isoformat() for d in days],
                         "research_node": research.role, "trader_node": trader.role}

        # ============================================================ research node
        with rep.step("1. idea intake: signed IdeaSpec accepted (HMAC, schema) - the trader node refuses ideas") as st:
            research.backtests.start()
            research.ideas.set_secret("ideas_intake_secret", INTAKE_SECRET, "e2e")
            research.ideas.update_config({"eval_days": 540, "notify": True})
            research.backtests.update_config({"workers": 2, "holdout_months": 0, "holdout_from": "2024-01-01"})
            research.signals.update_config({"promotion": {
                "wf_oos_sharpe": -10.0, "dsr": -1.0, "pbo": 1.1, "pf_2x": 0.0, "trades_swing": 1, "latency_s": 0,
                "shadow_sessions": 1, "gate_pass_pct": 0.0, "paper_after_shadow_sessions": 1, "band_pct": 99.9}})
            body = {"spec_version": 1, "name": "updays", "ir": IR, "params": {"lb": 3},
                    "universe": {"kind": "symbols", "symbols": [f"NSE:{s}" for s in SYMBOLS]},
                    "capital_hint": 500_000.0, "claimed": {"trades": 50}, "trials_tested": 40,
                    "source": {"generator": "idea-engine", "run_id": "e2e-1", "data_end": "2023-12-29"}}
            body["signature"] = sp.sign(body, INTAKE_SECRET)
            r = rc.post("/api/v2/ideas", json=body)
            st.check(r.status_code == 202, f"intake status {r.status_code}: {r.get_data(as_text=True)[:300]}")
            vid = r.get_json()["id"]
            bad = rc.post("/api/v2/ideas", json={**body, "trials_tested": 1})
            st.check(bad.status_code == 401, "a tampered spec must be refused")
            tr = tc.post("/api/v2/ideas", json=body)
            st.check(tr.status_code == 409, f"trader node must refuse ideas ({tr.status_code})")
            st.detail = {"version_id": vid, "tampered": bad.status_code, "on_trader_node": tr.status_code}

        with rep.step("2. validation + evaluation (backtest, walk-forward, sweep) -> accepted -> deployed (signals)") as st:
            _wait_jobs(research)
            v = research.ideas.version(vid, full=True)
            st.check(v["state"] == "deployed", f"idea state {v['state']}: {v.get('state_reason')}")
            ev_bundle = rc.get(f"/api/v2/ideas/versions/{vid}/evidence").get_json()
            st.check(all(c["pass"] for c in ev_bundle["gate"]["criteria"]), "evaluation gate")
            dep_id = v["deployment_id"]
            d = research.signals.get(dep_id)
            st.check(d["stage"] == "signals" and d["runtime"] == "bar_host", f"deployment {d['stage']}")
            st.check(d["host"]["mode"] == "paper" and d["host"]["status"] == "running", "simulated session runs")
            st.detail = {"deployment": dep_id, "strategy": v["strategy"],
                         "gate": {c["criterion"]: c["value"] for c in ev_bundle["gate"]["criteria"]}}

        with rep.step("3. signals -> shadow on the research node's gates (orchestrator); paper refused there") as st:
            sid_sig = research.signals.get(dep_id)["host"]["id"]
            _play_day(research, sid_sig, bars, 0)
            research.ideas._promote_at.clear()
            research.ideas.tick()
            d = research.signals.get(dep_id)
            st.check(d["stage"] == "shadow", f"stage after the orchestrator tick: {d['stage']}")
            _play_day(research, d["host"]["id"], bars, 1)
            research.ideas._promote_at.clear()
            research.ideas.tick()                                      # shadow -> paper is NOT auto on research
            st.check(research.signals.get(dep_id)["stage"] == "shadow", "research node never reaches paper")
            from tradealpha.lifecycle.signals.common import SignalError
            try:
                research.signals.promote(dep_id, {"to": "paper"})
                st.check(False, "paper promotion must be refused on the research node")
            except SignalError as e:
                st.check("research node" in str(e), str(e))
            st.detail = {"stage": "shadow", "signals_session": sid_sig}

        with rep.step("4. signed bundle: the research node runs the shadow -> paper gate and exports") as st:
            tok_r = research.signals.token()
            r = rc.get(f"/api/v2/signals/deployments/{dep_id}/bundle?promote_to=paper",
                       headers={"X-Historify-Token": tok_r})
            st.check(r.status_code == 200, f"export {r.status_code}: {r.get_data(as_text=True)[:300]}")
            bundle = r.get_json()
            st.check(bundle["deployment"]["stage"] == "paper" and bundle["gate"] and bundle["signature"], "bundle")
            st.detail = {"sha256": bundle["sha256"][:16], "gate": [c["criterion"] for c in bundle["gate"]["criteria"]]}

        # ============================================================ trader node
        tok = trader.signals.token()
        H = {"X-Historify-Token": tok}
        with rep.step("5. trader node imports the bundle (hash, signature, IR hash) -> paper on the Analyzer") as st:
            forged = json.loads(json.dumps(bundle))
            forged["deployment"]["params"] = {"lb": 9}
            bad = tc.post("/api/v2/signals/bundles/import", json={"bundle": forged}, headers=H)
            st.check(bad.status_code == 400, f"a changed bundle must be refused ({bad.status_code})")
            r = tc.post("/api/v2/signals/bundles/import", json={"bundle": bundle}, headers=H)
            st.check(r.status_code == 200, f"import {r.status_code}: {r.get_data(as_text=True)[:300]}")
            out = r.get_json()
            d = trader.signals.get(dep_id)
            st.check(d["stage"] == "paper" and d["host"]["mode"] == "analyzer", f"{d['stage']} / {d['host']}")
            again = tc.post("/api/v2/signals/bundles/import", json={"bundle": bundle}, headers=H).get_json()
            st.check(again["imported"] is False, "re-import is a no-op")
            sid_paper = d["host"]["id"]
            st.detail = {"import": {k: out[k] for k in ("stage", "strategy", "created_instruments")},
                         "session": sid_paper}

        with rep.step("6. three paper days on the OpenAlgo Analyzer: orders, fills, nightly parity, broker "
                      "reconciliation each day") as st:
            from tradealpha.execution.reconciliation import eod as reconcile
            from tradealpha.platform.time.timeutil import IST
            # a scripted 4-session flow cannot show a statistically meaningful record: the eligibility statistics
            # (MinTRL, drawdown band, correlation - ADR-010) are switched off explicitly, like the session counts
            trader.signals.update_config({"promotion": {"paper_sessions": 1, "slippage_mult": 100.0,
                                                        "eligibility": {"enabled": False}}})
            # the feed catches up first: the last seeded daily bar becomes final at midnight (the poll delivers it)
            for sym in SYMBOLS:
                from tradealpha.platform.db.models import Instrument
                from tradealpha.platform.time.intervals import INTERVALS
                with trader.meta.session() as s:
                    iid = s.scalar(select(Instrument.id).where(Instrument.symbol == sym))
                px = float(trader.store.read_range(iid, INTERVALS["D"].id, limit=1, tail=True)["close"].iloc[-1])
                httpx.post(mock.url + "/__admin/quote", json={"symbol": sym, "ltp": px, "bid": px - 0.05,
                                                              "ask": px + 0.05})
            trader.clock.set(datetime(2025, 3, 29, 0, 0, 2, tzinfo=IST))
            trader.lab_paper.poll()
            trader.lab_paper.sync(sid_paper)
            recs, pars = [], {}
            for i in (0, 1, 2):
                _play_day(trader, sid_paper, bars, i, mock.url, sync=True)
                recs += reconcile.run_nightly(trader, _today(trader))       # the nightly true-up of the day
                pars[days[i].isoformat()] = trader.lab_paper.parity(sid_paper, days[i])["status"]
            c = _counts(trader, sid_paper)
            st.check(sum(c.values()) > 0, "the paper days produced orders")
            state = httpx.get(mock.url + "/__admin/trading/state").json()
            st.check(state["orders"], "orders reached the Analyzer")
            st.check(recs and all(x["status"] == "ok" for x in recs), f"reconciliation {recs}")
            st.check(all(v_ == "ok" for v_ in pars.values()), f"parity {pars}")
            st.detail = {"orders": c, "analyzer_orders": len(state["orders"]),
                         "reconciliation": [(x["day"], x["status"], len(x["differences"])) for x in recs],
                         "parity": pars}

        with rep.step("7. drills on the trader node: paper (kill-9, kill switch, feed stop, auth) + live "
                      "(broker down, ws drop, disk full, clock skew)") as st:
            from tradealpha.lifecycle.gates.drills import LIVE_DRILLS, PAPER_DRILLS, run_drills
            res = run_drills(trader, list(PAPER_DRILLS + LIVE_DRILLS))
            st.detail = {r_["name"]: r_["passed"] for r_ in res}
            st.check(all(r_["passed"] for r_ in res), f"drills {st.detail}")
            with trader.meta.session() as s:                  # the T0 checklist sign-off (operator act, recorded)
                s.add(LabSignoff(kind="t0", signed_by="ops (e2e)", checklist_json=json.dumps({"e2e": True})))
                s.commit()

        with rep.step("8. paper -> eligible_live (gate from the trader's journal + explicit approval)") as st:
            g = tc.get(f"/api/v2/signals/deployments/{dep_id}/gate").get_json()
            r = tc.post(f"/api/v2/signals/deployments/{dep_id}/promote", json={"approve": True}, headers=H)
            st.check(r.status_code == 200, f"promote {r.status_code}: {r.get_data(as_text=True)[:500]}")
            st.check(trader.signals.get(dep_id)["stage"] == "eligible_live", "stage")
            st.detail = {c_["criterion"]: c_["pass"] for c_ in g["criteria"]}

        with rep.step("9. live gate refuses until every live requirement is met (never forceable)") as st:
            g = tc.get(f"/api/v2/signals/deployments/{dep_id}/gate").get_json()
            failing = sorted(c_["criterion"] for c_ in g["criteria"] if not c_["pass"])
            st.check(len(failing) >= 5, f"live gate should fail first: {failing}")
            r = tc.post(f"/api/v2/signals/deployments/{dep_id}/promote",
                        json={"force": True, "reason": "x", "confirm": "nope"}, headers=H)
            st.check(r.status_code == 409 and "cannot be forced" in r.get_json()["error"]["message"], "unforceable")
            st.detail = {"failing_before_setup": failing}

        with rep.step("10. live readiness: auth + user, live switch (phrase, caps, steps), limits, Analyzer OFF, "
                      "NTP, verified tariffs") as st:
            trader.auth.create_user("ops", "e2e-password-123")
            trader.auth.set_enabled(True)
            api_tok = trader.auth.create_token("ops", "e2e", "admin")["token"]
            H.update({"Authorization": f"Bearer {api_tok}"})
            st.check(tc.get("/api/v2/signals/live").status_code == 401, "auth on: anonymous API refused")
            steps = [{"max_order_notional": 250_000.0, "max_gross": 750_000.0, "max_orders_per_day": 10},
                     {"max_order_notional": 500_000.0, "max_gross": 1_500_000.0, "max_orders_per_day": 20}]
            r = tc.put("/api/v2/signals/live", json={"enabled": True, "confirm": "ENABLE LIVE TRADING",
                                                     "max_total_notional": 2_000_000, "steps": steps,
                                                     "dry_run_sessions": 1, "max_rejects_per_day": 1},
                       headers=H)
            if r.status_code != 200:                        # the cap must exist before the switch
                tc.put("/api/v2/signals/live", json={"max_total_notional": 2_000_000, "steps": steps,
                                                     "dry_run_sessions": 1, "max_rejects_per_day": 1}, headers=H)
                r = tc.put("/api/v2/signals/live", json={"enabled": True, "confirm": "ENABLE LIVE TRADING"},
                           headers=H)
            st.check(r.status_code == 200 and r.get_json()["enabled"], f"live switch {r.get_data(as_text=True)}")
            r = tc.patch(f"/api/v2/signals/deployments/{dep_id}", json={"limits": {
                "max_notional_per_position": 250_000, "max_total_notional": 750_000, "max_orders_per_day": 10,
                "daily_loss_stop_pct": 2.0}}, headers=H)
            st.check(r.status_code == 200, f"limits {r.get_data(as_text=True)[:300]}")
            httpx.post(mock.url + "/api/v1/analyzer/toggle", json={"apikey": "x", "mode": False})
            trader.settings.set("ntp_server", f"127.0.0.1:{ntp.port}")
            clk = tc.post("/api/v2/system/clock/check", headers=H).get_json()
            st.check(clk["offset_s"] is not None and abs(clk["offset_s"]) < 0.5, f"clock {clk}")
            trader.settings.set("lab_broker", "zerodha")
            trader.lab.update_broker_profile("zerodha", {"verified": True})
            from tradealpha.platform.db.lab_models import LabCostSchedule
            with trader.meta.session() as s:
                for c_ in s.scalars(select(LabCostSchedule)):
                    c_.verified = True
                s.commit()
            st.detail = {"clock": clk, "analyzer": False, "steps": steps}

        with rep.step("11. eligible_live -> live: the gate passes -> DRY-RUN session (first live session)") as st:
            dep_row = trader.signals.get(dep_id)
            # Phase 9 (ADR-014): the exchange algo registration of exactly this strategy version is a live criterion
            reg = tc.post("/api/v2/compliance/registrations", json={
                "strategy": dep_row["strategy"], "ir_sha256": dep_row["code_sha"], "broker": "zerodha",
                "exchange_algo_id": "E2E-ALGO-0001", "category": "white_box",
                "evidence": "e2e: broker confirmation reference"}, headers=H)
            st.check(reg.status_code == 201, f"algo registration {reg.get_data(as_text=True)[:300]}")
            g = tc.get(f"/api/v2/signals/deployments/{dep_id}/gate?to=live", headers=H).get_json()
            failing = [c_ for c_ in g["criteria"] if not c_["pass"]]
            name = trader.signals.get(dep_id)["name"]
            r = tc.post(f"/api/v2/signals/deployments/{dep_id}/promote", json={"confirm": name}, headers=H)
            st.check(r.status_code == 200, f"live promotion {r.get_data(as_text=True)[:800]} / {failing}")
            d = trader.signals.get(dep_id)
            st.check(d["stage"] == "live" and d["host"]["mode"] == "dry_run", f"{d['stage']} {d['host']}")
            sid_dry = d["host"]["id"]
            st.detail = {"criteria": {c_["criterion"]: c_["value"] for c_ in g["criteria"]}, "session": sid_dry}

        with rep.step("12. dry-run day: decisions + orders journaled (DRY- ids), NOTHING sent to the broker") as st:
            before = len(httpx.get(mock.url + "/__admin/trading/state").json()["orders"])
            _play_day(trader, sid_dry, bars, 3, mock.url)
            after = len(httpx.get(mock.url + "/__admin/trading/state").json()["orders"])
            with trader.meta.session() as s:
                ids = [o.broker_order_id for o in s.scalars(select(LabOrder).where(LabOrder.session_id == sid_dry))
                       if o.broker_order_id]
            st.check(after == before, f"dry run sent {after - before} orders")
            st.check(all(x.startswith("DRY-") for x in ids), f"dry-run ids {ids}")
            st.detail = {"journaled_orders": _counts(trader, sid_dry), "dry_ids": ids[:5], "broker_orders_delta": 0}

        with rep.step("13. the dry-run window is over: the host watch moves the deployment to the LIVE route") as st:
            trader.signals.tick_runs()
            d = trader.signals.get(dep_id)
            st.check(d["host"]["mode"] == "live" and d["host"]["status"] == "running", f"{d['host']}")
            sid_live = d["host"]["id"]
            g = trader.lab_paper.get_session(sid_live)["guardrails"]
            st.check(g["require_margin"] and g["max_orders_per_day"] == 10 and
                     g["max_order_notional"] == 250_000.0, f"live guardrails {g}")
            st.detail = {"session": sid_live, "guardrails": {k: g[k] for k in ("max_order_notional",
                                                                              "max_gross_exposure",
                                                                              "max_orders_per_day",
                                                                              "require_margin")}}

        with rep.step("14. live day: real route (Analyzer OFF), margin + funds asked per entry, fills") as st:
            calls0 = httpx.get(mock.url + "/__admin/trading/state").json()["calls"]
            _play_day(trader, sid_live, bars, 4, mock.url, sync=True)
            state = httpx.get(mock.url + "/__admin/trading/state").json()
            with trader.meta.session() as s:
                live_orders = list(s.scalars(select(LabOrder).where(LabOrder.session_id == sid_live)))
                linked = [o.broker_order_id for o in live_orders if o.broker_order_id]
            st.check(live_orders, "the live day produced orders")
            st.check(linked and all(not x.startswith("DRY-") for x in linked), f"broker ids {linked}")
            st.check(state["calls"].get("margin", 0) > calls0.get("margin", 0), "margin asked before entries")
            st.check(state["calls"].get("funds", 0) > calls0.get("funds", 0), "funds asked before entries")
            st.detail = {"orders": _counts(trader, sid_live), "broker_ids": linked[:5],
                         "margin_calls": state["calls"].get("margin", 0)}

        with rep.step("15. end-of-day reconciliation vs the broker's books + contract-note import (charges)") as st:
            from tradealpha.execution.reconciliation import eod as reconcile
            from tradealpha.research.kernel.costs import CostModel, schedule_from_tables
            recs = reconcile.run_nightly(trader, _today(trader))
            mine = [x for x in recs if x["session"] == sid_live]
            st.check(mine and mine[0]["status"] == "ok", f"live reconciliation {mine}")
            cm = CostModel(schedule_from_tables("zerodha"))
            state = httpx.get(mock.url + "/__admin/trading/state").json()
            rows = ["trade_date,order_id,trade_no,symbol,side,qty,price,brokerage,stt,exchange,sebi,stamp,gst"]
            k = 0
            for o in state["orders"]:
                if o["orderid"] not in set(linked) or not o.get("filled_quantity"):
                    continue
                k += 1
                q, px = int(o["filled_quantity"]), float(o["average_price"])
                ch = cm.charges("CNC", "buy" if o["action"] == "BUY" else "sell", q * px, _today(trader))
                rows.append(f"{_today(trader).isoformat()},{o['orderid']},T{k},{o['symbol']},{o['action']},{q},"
                            f"{px},{ch.brokerage},{ch.stt},{ch.exchange},{ch.sebi},{ch.stamp},{ch.gst}")
            csv_text = "\n".join(rows) + "\n"
            r = tc.post("/api/v2/trading/contract-notes?source=e2e-note.csv", data=csv_text,
                        content_type="text/csv", headers=H)
            st.check(r.status_code == 200, f"contract note {r.get_data(as_text=True)[:300]}")
            out = r.get_json()
            again = tc.post("/api/v2/trading/contract-notes?reconcile=0", data=csv_text, content_type="text/csv",
                            headers=H).get_json()
            st.check(again["imported"] == 0 and again["duplicates"] == out["imported"], "idempotent import")
            live_rec = [x for x in out["reconciled"] if x["session"] == sid_live]
            st.check(live_rec and live_rec[0]["status"] == "ok" and live_rec[0]["contract_note"], f"{live_rec}")
            st.detail = {"broker_check": mine[0]["status"], "note_trades": out["imported"],
                         "charges": live_rec[0].get("charges"), "warnings": live_rec[0].get("warnings")}

        with rep.step("16. pilot report (Phase 5) + stepped caps: up needs clean sessions (refused now)") as st:
            r = tc.get(f"/api/v2/signals/deployments/{dep_id}/pilot-report", headers=H)
            st.check(r.status_code == 200, "pilot report")
            pr = r.get_json()
            st.check(pr["routes"].get("live", {}).get("days") == 1 and pr["reconciliation"]["ok"] >= 2, f"{pr}")
            name = trader.signals.get(dep_id)["name"]
            up = tc.post(f"/api/v2/signals/deployments/{dep_id}/live-step", json={"step": 2, "confirm": name},
                         headers=H)
            st.check(up.status_code == 409 and "clean live sessions" in up.get_json()["error"]["message"], "step up")
            st.detail = {"verdict": pr["verdict"], "routes": {k_: {x: v_[x] for x in ("days", "orders", "fills",
                                                                                     "realised_net")}
                                                             for k_, v_ in pr["routes"].items()},
                         "live_step_up": up.status_code}

        with rep.step("17. trade events relayed trader -> research (HMAC, exactly once, chain re-verified)") as st:
            trader.ideas.update_config({"webhook_url": "http://localhost/api/v2/trade-events/ingest",
                                        "relay_batch": 500})
            tport = httpx.WSGITransport(app=r_app)
            total = 0
            for _ in range(50):
                got = httpx_relay(trader, tport)
                total += got.get("delivered", 0)
                if not got.get("delivered"):
                    st.check("error" not in got, f"relay {got}")
                    break
            from tradealpha.platform.events import outbox as ev
            origin = ev.node_id(trader)
            with research.meta.session() as s:
                n_in = s.scalar(select(func.count()).select_from(TradeEvent).where(TradeEvent.origin == origin))
                kinds = sorted({k_ for (k_,) in s.execute(select(TradeEvent.kind).where(TradeEvent.origin == origin))})
            st.check(n_in == total and total > 0, f"ingested {n_in} of {total}")
            st.check({"order.created", "fill", "reconciliation", "lifecycle.stage"} <= set(kinds), f"{kinds}")
            trader.settings.set(ev.CURSOR_KEY, 0, "integer")           # a re-delivery from scratch: no duplicates
            got = httpx_relay(trader, tport)
            with research.meta.session() as s:
                n_again = s.scalar(select(func.count()).select_from(TradeEvent).where(TradeEvent.origin == origin))
            st.check(n_again == n_in, "re-delivered events are deduplicated")
            v1 = rc.get("/api/v2/trade-events/verify").get_json()
            st.check(v1["ok"], f"research chain {v1}")
            st.detail = {"relayed": total, "kinds": kinds, "redelivered_all_deduplicated": got.get("delivered"),
                         "research_chain": v1}

        with rep.step("18. audit log hash chain: sealed, verified, anchored; tampering is detected") as st:
            from tradealpha.platform.audit import security
            security.anchor_audit(trader)
            v = tc.get("/api/v2/security/audit/verify", headers=H).get_json()
            st.check(v["ok"] and v["checked"] > 5, f"audit verify {v}")
            with trader.meta.session() as s:
                row = s.scalar(select(AuditLog).where(AuditLog.action == "promoted").limit(1))
                orig = row.detail_json
                row.detail_json = (orig or "") + " "
                s.commit()
                rid = row.id
            bad = security.verify_audit(trader)
            st.check(not bad["ok"] and bad["broken_at"] == rid, f"tamper not detected {bad}")
            with trader.meta.session() as s:
                s.get(AuditLog, rid).detail_json = orig
                s.commit()
            st.check(security.verify_audit(trader)["ok"], "restored")
            st.detail = {"verified_rows": v["checked"], "tampered_row_detected": rid}

        with rep.step("19. rotation with a grace window: the relay signs with the new AND the previous secret; the "
                      "order-route token rotates (old valid during the grace)") as st:
            r = tc.post("/api/v2/security/secrets/rotate", json={"name": "trade_events_webhook_secret",
                                                                 "value": LINK_SECRET + "-v2", "grace_h": 1},
                        headers=H)
            st.check(r.status_code == 200 and r.get_json()["previous_valid_until"], f"{r.get_data(as_text=True)}")
            from tradealpha.platform.events import outbox as ev
            seen = {}

            def sink(req):
                seen.update({k_: req.headers.get(k_) for k_ in ("X-Historify-Signature", "X-Historify-Signature-Prev")})
                return httpx.Response(200)
            with trader.meta.session() as s:
                ev.emit(s, "kill_switch", payload={"on": False, "actor": "e2e-rotation-probe"})
                s.commit()
            trader.ideas.update_config({"webhook_url": "http://sink.local/x"})
            ev.relay(trader, trader.ideas.config(), transport=httpx.MockTransport(sink))
            st.check(seen.get("X-Historify-Signature") and seen.get("X-Historify-Signature-Prev"), f"{seen}")
            rot = tc.post("/api/v2/signals/token/rotate", json={"grace_h": 1}, headers=H).get_json()
            st.check(trader.signals.check_token(rot["token"]) and trader.signals.check_token(tok), "token grace")
            H["X-Historify-Token"] = rot["token"]
            st.detail = {"relay_headers": sorted(k_ for k_, v_ in seen.items() if v_), "token_rotated": True}

        with rep.step("20. automatic demotion: broker rejects beyond the limit -> eligible_live, paused, "
                      "flattened, notified") as st:
            httpx.post(mock.url + "/__admin/trading", json={"reject_rate": 1.0})
            rejected = 0
            for day_idx in (5,):
                _play_day(trader, sid_live, bars, day_idx, mock.url, sync=True)
                rejected = _counts(trader, sid_live).get("rejected", 0)
            if rejected == 0:                                   # no entry that day: force one through the OMS
                from tradealpha.execution.oms.core import OMS
                g = trader.lab_paper.get_session(sid_live)
                with trader.meta.session() as s:
                    o = OMS.journal(s, session_id=sid_live, member_id=g["members"][0]["id"],
                                    idem_key=f"{sid_live}:e2e:reject", symbol=g["members"][0]["symbol"],
                                    exchange="NSE", side="BUY", qty=1, order_type="MARKET", price=100.0,
                                    reason="entry", bar_ts=int(trader.clock.now().timestamp()))
                    s.commit()
                    oid = o.id
                trader.lab_paper.oms_for(sid_live).progress(oid, trader.lab_paper._ctx_fn(sid_live),
                                                            trader.lab_paper._params(sid_live))
                trader.lab_paper.sync(sid_live)
                rejected = _counts(trader, sid_live).get("rejected", 0)
            st.check(rejected >= 1, "a broker reject happened")
            httpx.post(mock.url + "/__admin/trading", json={"reject_rate": 0.0})     # the broker accepts the flatten
            trader.signals.tick_runs()
            d = trader.signals.get(dep_id)
            st.check(d["stage"] == "eligible_live" and d["status"] == "paused" and "demoted" in
                     (d["paused_reason"] or ""), f"{d['stage']} {d['status']} {d['paused_reason']}")
            with trader.meta.session() as s:
                x = s.get(LabPaperSession, sid_live)
                st.check(x.status == "killed", f"live session {x.status}")
                dem = s.scalar(select(TradeEvent).where(TradeEvent.kind == "lifecycle.stage",
                                                        TradeEvent.ref == "eligible_live")
                               .order_by(TradeEvent.id.desc()).limit(1))
                st.check(dem is not None and '"demoted": true' in dem.payload_json, "demotion event")
            notes = [n_ for n_ in trader.notify.list(limit=50) if n_["kind"] == "live_demoted"]
            st.check(notes, "a live_demoted notification")
            st.detail = {"rejected": rejected, "paused_reason": d["paused_reason"], "notifications": len(notes)}

        with rep.step("21. after the demotion: the live book is flat at the broker and in the OMS; every broker "
                      "order maps to its journal row") as st:
            trader.lab_paper.sync(sid_live)
            state = httpx.get(mock.url + "/__admin/trading/state").json()
            tag = f"historify-{sid_live[:8]}-"
            theirs = [o for o in state["orders"] if str(o.get("strategy") or "").startswith(tag)]
            with trader.meta.session() as s:
                journal = {o.id: o for o in s.scalars(select(LabOrder).where(LabOrder.session_id == sid_live))}
                recs = s.scalar(select(func.count()).select_from(BrokerReconciliation)) or 0
            orphans = [o["orderid"] for o in theirs if int(o["strategy"][len(tag):]) not in journal]
            st.check(not orphans, f"broker orders without a journal row: {orphans}")
            st.check(not [o for o in theirs if o["order_status"] == "open"], "open live orders left at the broker")
            net: dict = {}
            for o in theirs:
                if o["order_status"] == "complete":
                    q = int(o["filled_quantity"])
                    net[o["symbol"]] = net.get(o["symbol"], 0) + (q if o["action"] == "BUY" else -q)
            st.check(all(v_ == 0 for v_ in net.values()), f"broker position not flat: {net}")
            book = trader.lab_paper.book(sid_live)
            st.check(all(int(p_.get("qty") or 0) == 0 for p_ in book.get("positions", [])), "OMS book not flat")
            st.detail = {"live_orders_at_broker": len(theirs), "statuses": sorted({o["order_status"] for o in theirs}),
                         "net_at_broker": net, "reconciliations": recs}
    except StepFailed:
        pass
    except Exception:                                            # a crash outside a step
        rep.steps.append({"step": "harness", "ok": False, "error": traceback.format_exc()[-3000:]})
    finally:
        ntp.stop()
        for sv in nodes:
            try:
                sv.close()
            except Exception:
                pass
        mock.stop()
        for n, lv in levels.items():
            logging.getLogger(n).setLevel(lv)
        if old_profile is None:
            os.environ.pop("HISTORIFY_PROVIDER_PROFILE", None)
        else:
            os.environ["HISTORIFY_PROVIDER_PROFILE"] = old_profile
        if own_tmp:
            import shutil
            shutil.rmtree(base, ignore_errors=True)
    return rep.summary()


def _today(sv):
    from tradealpha.platform.time.timeutil import IST
    return sv.clock.now().astimezone(IST).date()


def httpx_relay(sv, transport):
    from tradealpha.platform.events import outbox as ev
    return ev.relay(sv, sv.ideas.config(), transport=transport)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workdir", default=None, help="keep the two node instances here (default: a temp dir)")
    ap.add_argument("--report", default=None, help="write the JSON report here")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    import logging
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    out = run(a.workdir, a.quiet)
    if a.report:
        os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
        with open(a.report, "w") as f:
            json.dump(out, f, indent=2, default=str)
    print(f"\nE2E trading flow: {out['passed']}/{out['steps']} steps passed in {out['seconds']} s -> "
          f"{'OK' if out['ok'] else 'FAILED'}")
    if not out["ok"]:
        bad = next((s for s in out["results"] if not s["ok"]), None)
        if bad:
            print(f"first failure: {bad['step']}\n{bad.get('error')}\n{bad.get('trace', '')[-1500:]}")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
