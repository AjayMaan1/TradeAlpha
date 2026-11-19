"""Trading-engine Phase 4 (live readiness) - unit and service-level tests:

L1 live routes (LiveRoute refuses the Analyzer, DryRunRoute never sends), G-PRE-8 / margin-unknown, stepped caps and
   their validation, manual + automatic demotion rules;
L2 WebSocket decision source (1m -> interval aggregation, partial buckets left to REST, parity class data_source),
   latency histograms on /metrics;
L3 roles (research refuses order stages / broker routes, trader refuses ideas), signed bundles (tamper, wrong secret,
   stage cap), trade-event ingest (HMAC, exactly once, chain break, own origin);
L4 SNTP clock check (offset, unreachable, unset) wired to G-SYS-3;
L5 margin / funds on the live route;
L6 contract-note parsing + import idempotency, reconciliation differences and charges warnings, live fees from the note;
L7 audit hash chain (tamper, delete, anchor truncation), secret rotation grace (intake + relay), API-token and
   order-route token rotation;
S1 trade-event retention with a chain anchor; S3 idea notifications.
The whole flow end to end is tests/e2e/test_e2e_trading_flow.py (scripts/e2e_trading_flow.py)."""
from __future__ import annotations

import json
import socket
import struct
import threading
import time
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from tradealpha.execution.oms.core import BrokerError
from tradealpha.execution.reconciliation import eod as reconcile
from tradealpha.execution.risk import guardrails as G
from tradealpha.execution.routes.fake_broker import FakeOpenAlgo
from tradealpha.execution.routes.live import DryRunRoute, LiveRoute
from tradealpha.lifecycle.gates.drills import DRILL_IR, scenario, store_bars, synthetic_bars
from tradealpha.lifecycle.signals.common import SignalError
from tradealpha.platform.audit import security
from tradealpha.platform.db.lab_models import LabDecision, LabFill, LabOrder, LabPaperBar
from tradealpha.platform.db.models import AuditLog, BrokerReconciliation, ContractNoteTrade, DataIssue, TradeEvent
from tradealpha.platform.events import outbox as ev

pytestmark = pytest.mark.trading_mock_matching     # the mock OpenAlgo's matching calibration



@pytest.fixture
def sv(make_services):
    return make_services(now="2025-01-01T10:00:00+05:30", name="p4")


# ------------------------------------------------------------------------------------------------ L1 / L5 routes
def test_live_route_refuses_the_analyzer_and_asks_margin_and_funds():
    acct = FakeOpenAlgo(lambda sym: 100.0, analyze=True, cash=50_000.0)
    r = LiveRoute(acct, "MIS", "historify-x")
    order = {"id": 7, "symbol": "SBIN", "exchange": "NSE", "side": "BUY", "qty": 10, "order_type": "MARKET",
             "price": None, "reason": "entry"}
    with pytest.raises(BrokerError, match="ANALYZER") as e:
        r.send(order)
    assert e.value.hard and not acct.orders                  # never sent
    acct.analyze = False
    assert r.send(order)["broker_order_id"].startswith("LV")
    assert r.margin(order) == pytest.approx(200.0)            # MIS 20 % of 10 x 100
    assert r.funds() == 50_000.0
    acct.down = "refused"
    assert r.margin(order) is None and r.funds() is None      # unknown, not zero


def test_dry_run_route_journals_and_never_sends():
    r = DryRunRoute("historify-x")
    o = {"id": 3, "symbol": "SBIN", "side": "BUY", "qty": 1}
    assert r.send(o)["broker_order_id"] == "DRY-3" and r.sent == [o]
    assert r.lookup(o)["broker_order_id"] == "DRY-3" and r.cancel(o) and r.modify(o) and r.quote("S", "E") is None
    assert r.fills_by_engine is False                          # nothing ever fills: no position on dry run


def test_margin_unknown_blocks_a_live_entry_but_not_an_exit():
    p = G.params({"require_margin": True}, 1_000_000)
    base = {"symbol": "SBIN", "session_symbols": ["SBIN"], "qty": 1, "notional": 100, "order_type": "MARKET"}
    hit = G.evaluate({**base, "margin_unknown": True}, p, entry=True)
    assert [b.guardrail.id for b in hit] == ["G-PRE-6"] and "could not tell" in hit[0].message
    assert G.evaluate({**base, "margin_unknown": True}, p, entry=False) == []


def test_session_checks_live_only_through_the_lifecycle():
    p = G.params(None, 1e6)
    assert any("G-SYS-7" in e for e in G.session_checks({}, {}, True, p, "live"))
    assert any("G-SYS-7" in e for e in G.session_checks({}, {}, True, p, "dry_run"))
    assert G.session_checks({}, {}, True, p, "live", allow_live=True) == []
    assert any("unknown route" in e for e in G.session_checks({}, {}, True, p, "moon", allow_live=True))


def test_live_steps_validation_and_caps(sv):
    s = sv.signals
    with pytest.raises(SignalError, match="steps must"):
        s.update_live({"steps": [{"max_order_notional": 1}]})
    with pytest.raises(SignalError, match="must not shrink"):
        s.update_live({"steps": [{"max_order_notional": 10, "max_gross": 100, "max_orders_per_day": 1},
                                 {"max_order_notional": 10, "max_gross": 50, "max_orders_per_day": 1}]})
    cfg = s.update_live({"steps": [{"max_order_notional": 10, "max_gross": 100, "max_orders_per_day": 1},
                                   {"max_order_notional": 20, "max_gross": 200, "max_orders_per_day": 2}],
                         "step_min_sessions": 3, "max_rejects_per_day": 2, "demote_flatten": False})
    assert cfg["step_min_sessions"] == 3 and cfg["demote_flatten"] is False
    assert s.step_caps(1)["max_gross"] == 100 and s.step_caps(9)["max_gross"] == 200   # clamped to the last step
    with pytest.raises(KeyError):
        s.live_step("nope", 2, "x")


# ------------------------------------------------------------------------------------------------ L2 ws source
def test_ws_decision_source_aggregates_minutes_and_leaves_partial_buckets_to_rest(sv):
    sid, replay, member_iid, play = scenario(sv, warm_days=5, replay_days=1, decision_source="ws",
                                             session_name="ws1")
    assert sv.lab_paper.get_session(sid)["decision_source"] == "ws"
    mid = next(iter(replay))
    day = play[0]
    one = synthetic_bars([day], seed=5, bar_s=60)
    first = int(one["ts"].iloc[0])
    key = "NSE:DRILLA"
    # bucket 1: five minutes -> decided on the 5th (ws)
    for r in one.iloc[:5].itertuples(index=False):
        sv.clock.set(datetime.fromtimestamp(int(r.ts) + 62, UTC))
        sv.lab_paper.on_stream_bar(key, {"ts": int(r.ts), "open": r.open, "high": r.high, "low": r.low,
                                         "close": r.close, "volume": int(r.volume)})
    with sv.meta.session() as s:
        b = s.get(LabPaperBar, (sid, member_iid[mid], first))
        assert b is not None and b.source == "ws"
        want = one.iloc[:5]
        assert (b.open, b.high, b.low, b.close) == (want["open"].iloc[0], want["high"].max(), want["low"].min(),
                                                    want["close"].iloc[-1])
        assert b.volume == int(want["volume"].sum())
    # bucket 2 misses its last minute; bucket 3 starts: bucket 2 is NOT decided from the stream
    for r in one.iloc[5:9].itertuples(index=False):
        sv.lab_paper.on_stream_bar(key, {"ts": int(r.ts), "open": r.open, "high": r.high, "low": r.low,
                                         "close": r.close, "volume": int(r.volume)})
    r = one.iloc[10]
    sv.lab_paper.on_stream_bar(key, {"ts": int(r.ts), "open": r.open, "high": r.high, "low": r.low,
                                     "close": r.close, "volume": int(r.volume)})
    assert sv.lab_paper.ws_incomplete == 1
    with sv.meta.session() as s:
        assert s.get(LabPaperBar, (sid, member_iid[mid], first + 300)) is None
    # the REST bar decides it; a WS repeat of an already decided bucket is a no-op
    five = one.iloc[5:10]
    rest = {"ts": first + 300, "open": five["open"].iloc[0], "high": five["high"].max(), "low": five["low"].min(),
            "close": five["close"].iloc[-1], "volume": int(five["volume"].sum())}
    import pandas as pd
    store_bars(sv, member_iid[mid], pd.DataFrame([rest]), 3)
    sv.clock.set(datetime.fromtimestamp(first + 600 + 20, UTC))
    sv.lab_paper.poll()
    with sv.meta.session() as s:
        assert s.get(LabPaperBar, (sid, member_iid[mid], first + 300)).source == "rest"
    snap = sv.lab_paper.latency_book.snapshot()
    assert snap["decide_s"]["ws"]["n"] >= 1 and snap["decide_s"]["rest"]["n"] >= 1
    assert snap["feed_lag_s"]["rest"]["n"] >= 1


def test_parity_classes_a_ws_vs_download_difference_as_data_source(sv):
    sid, replay, member_iid, play = scenario(sv, warm_days=5, replay_days=1, decision_source="ws",
                                             session_name="ws2")
    mid = next(iter(replay))
    bars = replay[mid]
    # decide every bucket from "ws" bars that are slightly off the downloaded record
    import pandas as pd
    for k in range(len(bars)):
        row = bars.iloc[k]
        ws = {"ts": int(row.ts), "open": float(row.open), "high": float(row.high) + 0.5, "low": float(row.low),
              "close": float(row.close) + (0.35 if k % 3 == 0 else 0.0), "volume": int(row.volume) + 7}
        sv.clock.set(datetime.fromtimestamp(int(row.ts) + 302, UTC))
        sv.lab_paper.on_bar(mid, {**ws, "bar_s": 300}, source="ws")
    store_bars(sv, member_iid[mid], pd.DataFrame(bars), 3)                       # the download (the record)
    rep = sv.lab_paper.parity(sid, play[0])
    rows = [r for m in rep["members"] for r in m["differences"]]
    assert rep["divergences"] == 0, rows[:3]
    if rows:                                     # every explained difference is the tick-built bar, not a revision
        assert {r["status"] for r in rows} == {"data_source"} and rep["data_source"] == len(rows)


def test_metrics_expose_latency_histograms_and_role(make_v2):
    c, sv, _ = make_v2()
    sv.lab_paper.latency_book.observe("ws", decide_s=0.02, feed_lag_s=1.5)
    txt = c.get("/metrics").get_data(as_text=True)
    assert 'historify_decision_seconds_bucket{source="ws",le="0.025"} 1' in txt
    assert 'historify_bar_feed_lag_seconds_count{source="ws"} 1' in txt
    assert 'historify_role{role="all"} 1' in txt


# ------------------------------------------------------------------------------------------------ L4 clock
class _NTP:
    def __init__(self, offset=0.0, stratum=2):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.offset, self.stratum, self.stop = offset, stratum, False
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        from tradealpha.execution.safety.clock_check import _to_ntp
        self.sock.settimeout(0.2)
        while not self.stop:
            try:
                data, addr = self.sock.recvfrom(512)
            except OSError:
                continue
            out = bytearray(48)
            out[0], out[1] = (4 << 3) | 4, self.stratum
            now = time.time() + self.offset
            struct.pack_into("!II", out, 32, *_to_ntp(now))
            struct.pack_into("!II", out, 40, *_to_ntp(now))
            self.sock.sendto(bytes(out), addr)


def test_clock_check_measures_the_offset_and_feeds_g_sys_3(sv):
    cc = sv.clock_check
    assert cc.refresh()["error"] == "ntp_server not set" and cc.offset() is None
    srv = _NTP(offset=3.0)
    try:
        sv.settings.set("ntp_server", f"127.0.0.1:{srv.port}")
        st = cc.refresh()
        assert st["offset_s"] == pytest.approx(3.0, abs=0.2) and st["error"] is None
        assert sv.lab_paper.clock_offset_fn() == pytest.approx(3.0, abs=0.2)          # G-SYS-3 input
        hit = G.evaluate({"clock_offset_s": sv.lab_paper.clock_offset_fn()}, G.params(None, 1e6), entry=True)
        assert [b.guardrail.id for b in hit] == ["G-SYS-3"]
        srv.stratum = 0                                                               # kiss-of-death
        assert "refused" in cc.refresh()["error"]
    finally:
        srv.stop = True
    sv.settings.set("ntp_server", "127.0.0.1:9")                                     # nobody answers
    assert cc.refresh()["error"]


# ------------------------------------------------------------------------------------------------ L6 reconciliation
NOTE = """Trade Date,Order No,Trade No,Scrip,Buy/Sell,Quantity,Rate,Brokerage,STT,Exchange Txn,SEBI Fees,Stamp Duty,GST
28/03/2025,{oid},T1,SBIN,B,10,100.00,20,0,3,0.1,1,4.1
"""


def test_contract_note_parsing_aliases_and_errors():
    rows = reconcile.parse_contract_note(NOTE.format(oid="X1"))
    assert rows[0]["side"] == "BUY" and rows[0]["trade_date"] == date(2025, 3, 28) and rows[0]["qty"] == 10
    assert rows[0]["charges"]["brokerage"] == 20 and rows[0]["total"] == pytest.approx(28.2)
    two = "date,order_id,symbol,side,qty,price\n2025-03-28,A,X,S,1,10\n2025-03-28,A,X,S,2,10\n"
    assert [r["trade_no"] for r in reconcile.parse_contract_note(two)] == ["#1", "#2"]
    with pytest.raises(reconcile.ReconError, match="columns missing"):
        reconcile.parse_contract_note("a,b\n1,2\n")
    with pytest.raises(reconcile.ReconError, match="side"):
        reconcile.parse_contract_note("date,order_id,symbol,side,qty,price\n2025-03-28,A,X,Z,1,10\n")


def _live_session(sv, acct, name="rec"):
    from tradealpha.lifecycle.gates.drills import _routed
    _routed(sv, acct, live=True)
    sid, replay, _, play = scenario(sv, warm_days=5, replay_days=1, session_name=name, mode="analyzer")
    from tradealpha.platform.db.lab_models import LabPaperSession
    with sv.meta.session() as s:                    # a live-route session (the lifecycle's path is the e2e test's)
        s.get(LabPaperSession, sid).mode = "live"
        s.commit()
    from tradealpha.execution.api import LIVE_ENABLED
    sv.safety.set(LIVE_ENABLED, True, "test", "live route session")    # ADR-010: no live risk with the switch off
    return sid, replay, play


def test_reconciliation_ok_then_differences_then_contract_note(sv):
    acct = FakeOpenAlgo(lambda sym: 500.0, analyze=False)
    sid, replay, play = _live_session(sv, acct)
    sv.lab_paper.replay(sid, replay)
    sv.lab_paper.sync(sid)
    from tradealpha.platform.time.timeutil import IST
    today = sv.clock.now().astimezone(IST).date()
    with sv.meta.session() as s:
        orders = [o for o in s.scalars(select(LabOrder).where(LabOrder.session_id == sid,
                                                              LabOrder.broker_order_id.is_not(None)))]
    assert orders, "the scenario must trade"
    rep = reconcile.reconcile_session(sv, sid, today)
    assert rep["status"] == "ok" and rep["broker_checked"] and not rep["differences"], rep["differences"]
    # broker-resting protective stops are host records, not engine decisions: the nightly parity stays clean
    par = sv.lab_paper.parity(sid, play[0])
    assert par["divergences"] == 0 and par["status"] in ("ok", "revision"), par
    with sv.meta.session() as s:
        assert any("protective_stop" in d.intents_json for d in s.scalars(select(LabDecision)))
    # an order of an earlier day still in the broker's book is known, not "unknown"
    with sv.meta.session() as s:
        o = s.get(LabOrder, orders[0].id)
        o.sent_at = o.sent_at - timedelta(days=1)
        s.commit()
    rep = reconcile.reconcile_session(sv, sid, today)
    assert not [d for d in rep["differences"] if d["kind"] == "unknown_order"], rep["differences"]
    with sv.meta.session() as s:
        o = s.get(LabOrder, orders[0].id)
        o.sent_at = o.sent_at + timedelta(days=1)
        s.commit()
    # the broker shows a different quantity and an order the OMS never linked
    o0 = orders[0]
    acct.trades.append({"orderid": o0.broker_order_id, "symbol": o0.symbol, "exchange": "NSE", "action": o0.side,
                        "quantity": 1, "average_price": 500.0, "product": "MIS", "strategy": "x"})
    acct.orders["GHOST1"] = {"orderid": "GHOST1", "symbol": "DRILLA", "exchange": "NSE", "action": "BUY",
                             "quantity": 1, "pricetype": "MARKET", "product": "MIS", "price": 0, "trigger_price": 0,
                             "strategy": f"historify-{sid[:8]}-999999", "order_status": "complete",
                             "average_price": 500.0, "filled": 1}
    rep = reconcile.reconcile_session(sv, sid, today)
    kinds = {d["kind"] for d in rep["differences"]}
    assert rep["status"] == "diverged" and {"qty_mismatch", "unknown_order", "position_mismatch"} <= kinds
    with sv.meta.session() as s:
        row = s.scalar(select(BrokerReconciliation).where(BrokerReconciliation.session_id == sid))
        assert row.status == "diverged" and row.differences == len(rep["differences"])
        assert s.scalar(select(DataIssue).where(DataIssue.kind == "broker_reconciliation")) is not None
        assert s.scalar(select(TradeEvent).where(TradeEvent.kind == "reconciliation")) is not None
    # a past day without a contract note: no verdict, the stored one is kept
    assert reconcile.reconcile_session(sv, sid, today - timedelta(days=3))["status"] == "no_data"
    # contract note: imported once; charges differ from the model -> a warning (tariff), the fees become the note's
    acct.trades.pop()
    del acct.orders["GHOST1"]
    lines = ["trade_date,order_id,trade_no,symbol,side,qty,price,brokerage,stt,gst"]
    with sv.meta.session() as s:
        for o in s.scalars(select(LabOrder).where(LabOrder.session_id == sid, LabOrder.filled_qty > 0)):
            px = s.scalar(select(LabFill.price).where(LabFill.order_id == o.id))
            lines.append(f"{today},{o.broker_order_id},T{o.id},{o.symbol},{o.side},{o.filled_qty},{px},500,0,90")
    text = "\n".join(lines) + "\n"
    first = reconcile.import_contract_note(sv, text, "note.csv")
    assert first["imported"] == len(lines) - 1 and reconcile.import_contract_note(sv, text)["imported"] == 0
    rep = reconcile.reconcile_session(sv, sid, today)
    assert rep["status"] == "ok" and rep["contract_note"] and rep["warnings"][0]["kind"] == "charges_mismatch"
    with sv.meta.session() as s:
        fees = s.scalar(select(func.sum(LabFill.fees)).join(LabOrder, LabOrder.id == LabFill.order_id)
                        .where(LabOrder.session_id == sid))
        assert fees == pytest.approx(590.0 * (len(lines) - 1))           # the legal charges are the live fees
        assert s.scalar(select(func.count()).select_from(ContractNoteTrade)) == len(lines) - 1


def test_live_demotion_reasons(sv):
    """A diverged reconciliation, too many rejects, a loss stop, a killed session each demote (unit)."""
    from tradealpha.platform.db.lab_models import LabGuardrailEvent, LabPaperSession

    class R:
        id = "dep-x"
    acct = FakeOpenAlgo(lambda sym: 100.0, analyze=False)
    sid, _, _ = _live_session(sv, acct, "dem")
    with sv.meta.session() as s:
        s.get(LabPaperSession, sid).deployment_id, s.get(LabPaperSession, sid).deployment_stage = "dep-x", "live"
        s.commit()
    svc = sv.signals
    assert svc._live_demotion_reason(R, {"status": "running"}) is None
    assert "killed" in svc._live_demotion_reason(R, {"status": "killed", "kill_reason": "k"})
    with sv.meta.session() as s:
        s.add(LabGuardrailEvent(session_id=sid, guardrail="G-IN-2", severity="hard", action="kill",
                                message="daily loss", created_at=svc._utc()))
        s.commit()
    assert svc._live_demotion_reason(R, {"status": "running"}).startswith("G-IN-2")
    with sv.meta.session() as s:
        s.add(BrokerReconciliation(day=date(2025, 1, 1), session_id=sid, deployment_id="dep-x", status="diverged",
                                   differences=1, report_json="{}"))
        s.commit()
    assert "reconciliation" in svc._live_demotion_reason(R, {"status": "running"})


# ------------------------------------------------------------------------------------------------ L7 security
def test_audit_chain_seal_verify_tamper_delete_and_anchor(sv):
    for i in range(5):
        sv.signals.audit("test", f"obj{i}", {"i": i}, "t")
    assert security.verify_audit(sv)["unsealed"] == 5
    assert security.seal_audit(sv) == 5 and security.seal_audit(sv) == 0
    v = security.verify_audit(sv)
    assert v["ok"] and v["checked"] == 5
    anchor = security.anchor_audit(sv)
    with sv.meta.session() as s:
        assert s.scalar(select(TradeEvent).where(TradeEvent.kind == "audit.anchor")).ref == str(anchor["id"])
        row = s.scalar(select(AuditLog).where(AuditLog.object == "obj2"))
        row.actor = "mallory"
        s.commit()
    bad = security.verify_audit(sv)
    assert not bad["ok"] and bad["broken_at"] == row.id
    with sv.meta.session() as s:
        s.get(AuditLog, row.id).actor = "t"
        s.commit()
    assert security.verify_audit(sv)["ok"]
    with sv.meta.session() as s:                                   # a deleted row breaks the link of the next one
        s.delete(s.scalar(select(AuditLog).where(AuditLog.object == "obj1")))
        s.commit()
    assert not security.verify_audit(sv)["ok"]


def test_audit_truncation_below_the_published_anchor_is_detected(sv):
    for i in range(3):
        sv.signals.audit("test", f"o{i}", None, "t")
    security.anchor_audit(sv)
    with sv.meta.session() as s:
        for a in s.scalars(select(AuditLog)):
            s.delete(a)
        s.commit()
    out = security.verify_audit(sv)
    assert not out["ok"] and "missing" in out["reason"]


def test_secret_rotation_grace_for_intake_and_relay(sv):
    from tradealpha.ideas import spec as sp
    sv.ideas.set_secret("ideas_intake_secret", "old-secret-value-1", "t")
    with pytest.raises(ValueError, match="16"):
        security.rotate_secret(sv, "ideas_intake_secret", "short")
    with pytest.raises(ValueError, match="unknown"):
        security.rotate_secret(sv, "openalgo_api_key", "x" * 20)
    out = security.rotate_secret(sv, "ideas_intake_secret", "new-secret-value-2", grace_h=2)
    assert out["previous_valid_until"]
    body = {"spec_version": 1, "name": "x"}
    old_sig = {**body, "signature": sp.sign(body, "old-secret-value-1")}
    assert security.previous_secret(sv, "ideas_intake_secret") == "old-secret-value-1"
    assert security.candidates(sv, "ideas_intake_secret") == ["new-secret-value-2", "old-secret-value-1"]
    sp.verify_signature(old_sig, security.previous_secret(sv, "ideas_intake_secret"))
    sv.clock.set(sv.clock.now() + timedelta(hours=3))                  # the grace window is over
    assert security.previous_secret(sv, "ideas_intake_secret") is None
    assert security.rotation_status(sv)["ideas_intake_secret"]["previous_valid_until"] is None


def test_api_token_and_order_token_rotation(sv):
    sv.auth.create_user("ops", "password-1234")
    t = sv.auth.create_token("ops", "gen", "ideas:write")
    new = sv.auth.rotate_token(t["id"], grace_h=1)
    assert new["scopes"] == "ideas:write" and sv.auth.token_user(new["token"])
    assert sv.auth.token_user(t["token"]) is not None                  # grace: the old one still works
    from tradealpha.platform.auth.service import AuthError
    with pytest.raises(AuthError, match="already rotated"):
        sv.auth.rotate_token(t["id"])
    from tradealpha.platform.db.models import ApiToken
    with sv.meta.session() as s:
        s.get(ApiToken, t["id"]).expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        s.commit()
    assert sv.auth.token_user(t["token"]) is None                      # expired
    old = sv.signals.token()
    r = sv.signals.rotate_token(grace_h=1)
    assert r["token"] != old and sv.signals.check_token(r["token"]) and sv.signals.check_token(old)
    r2 = sv.signals.rotate_token(grace_h=0)
    assert not sv.signals.check_token(old) and not sv.signals.check_token(r["token"])
    assert sv.signals.check_token(r2["token"])


# ------------------------------------------------------------------------------------------------ L3 nodes
def _batch(sv, events, origin="trader-1", secret="link-secret-0000001"):
    body = json.dumps({"origin": origin, "first": events[0]["id"], "last": events[-1]["id"], "events": events},
                      default=str).encode()
    return body, "sha256=" + ev.sign_batch(body, secret)


def test_trade_event_ingest_exactly_once_chain_checked(make_services):
    trader = make_services(now="2025-01-01T10:00:00+05:30", name="tr")
    research = make_services(now="2025-01-01T10:00:00+05:30", name="rs")
    for x in (trader, research):
        x.settings.set("trade_events_webhook_secret", "link-secret-0000001")
    with trader.meta.session() as s:
        for i in range(6):
            ev.emit(s, "fill", deployment_id="d1", ref=i, payload={"qty": i})
        s.commit()
    evs = ev.read(trader, 0, 100)["events"]
    body, sig = _batch(trader, evs[:3])
    with pytest.raises(ev.IngestError) as e:
        ev.ingest(research, body, "sha256=bad")
    assert e.value.status == 401
    out = ev.ingest(research, body, sig)
    assert out["ingested"] == 3 and out["head"]["id"] == evs[2]["id"]
    assert ev.ingest(research, body, sig)["duplicates"] == 3          # redelivery: no new rows
    gap_body, gap_sig = _batch(trader, evs[4:])                        # event 4 missing: the chain breaks
    with pytest.raises(ev.IngestError) as e:
        ev.ingest(research, gap_body, gap_sig)
    assert e.value.status == 409
    body2, sig2 = _batch(trader, evs[3:])
    assert ev.ingest(research, body2, sig2)["ingested"] == 3
    with research.meta.session() as s:
        rows = list(s.scalars(select(TradeEvent).where(TradeEvent.origin == "trader-1").order_by(TradeEvent.id)))
        assert [r.remote_id for r in rows] == [e_["id"] for e_ in evs]
        assert s.scalar(select(DataIssue).where(DataIssue.kind == "trade_ingest")) is not None
    assert ev.verify_chain(research)["ok"]
    own, own_sig = _batch(research, evs[:1], origin=ev.node_id(research))
    with pytest.raises(ev.IngestError) as e:
        ev.ingest(research, own, own_sig)
    assert e.value.status == 409


def test_trade_event_retention_keeps_the_chain_verifiable(sv):
    with sv.meta.session() as s:
        for i in range(5):
            ev.emit(s, "fill", ref=i, ts=datetime(2024, 1, 1) + timedelta(minutes=i))
        for i in range(3):
            ev.emit(s, "fill", ref=10 + i)
        s.commit()
    out = ev.apply_retention(sv, {"retention_days": 30})
    assert out["pruned"] == 5
    with sv.meta.session() as s:
        assert s.scalar(select(func.count()).select_from(TradeEvent)) == 3
    v = ev.verify_chain(sv)
    assert v["ok"] and v["checked"] == 3
    assert ev.apply_retention(sv, {"retention_days": None})["pruned"] == 0


def test_roles_research_never_routes_orders_trader_takes_no_ideas(make_services):
    from tradealpha.platform.runtime import roles
    assert not roles.runs("research", "stream") and roles.runs("research", "lab_paper")
    assert not roles.runs("trader", "backtests") and roles.runs("trader", "signals")
    sv = make_services(now="2025-01-01T10:00:00+05:30", name="rr")
    sv.role = "research"
    with pytest.raises(BrokerError, match="research node"):
        sv.lab_paper.broker_for({"id": "x", "mode": "analyzer", "exec_json": "{}"})
    sv.role = "trader"
    from tradealpha.ideas.service import IdeaError
    with pytest.raises(IdeaError, match="trader node"):
        sv.ideas.submit({"spec_version": 1})


def test_bundle_refusals(make_services):
    from tradealpha.lifecycle import bundle
    sv = make_services(now="2025-01-01T10:00:00+05:30", name="bb")
    with pytest.raises(SignalError, match="node-link secret"):
        bundle._secret(sv)
    sv.settings.set("trade_events_webhook_secret", "link-secret-0000001")
    b = {"format": bundle.FORMAT, "origin": "elsewhere", "deployment": {"code_sha": "a", "stage": "live"},
         "ir": {"name": "x", "ir_json": "{}", "sha256": "a"}}
    b["sha256"] = bundle._digest(b)
    import hashlib
    import hmac
    b["signature"] = hmac.new(b"link-secret-0000001", b["sha256"].encode(), hashlib.sha256).hexdigest()
    bundle.verify_bundle(sv, b)
    with pytest.raises(SignalError, match="changed after export"):
        bundle.verify_bundle(sv, {**b, "origin": "x"})
    with pytest.raises(SignalError, match="signature"):
        bundle.verify_bundle(sv, {**b, "signature": "0" * 64})
    with pytest.raises(SignalError, match="not a"):
        bundle.verify_bundle(sv, {"format": "x"})
    bad_ir = {**b, "ir": {**b["ir"], "sha256": "b"}}
    bad_ir["sha256"] = bundle._digest(bad_ir)
    bad_ir["signature"] = hmac.new(b"link-secret-0000001", bad_ir["sha256"].encode(), hashlib.sha256).hexdigest()
    with pytest.raises(SignalError, match="IR"):
        bundle.verify_bundle(sv, bad_ir)


# ------------------------------------------------------------------------------------------------ S3 + API
def test_idea_state_notifications(sv):
    from tradealpha.ideas.service import IdeaService
    got = []
    sv.notify.notify = lambda *a, **k: got.append((a, k)) or 1
    sv.ideas._notify_state("idea-1", 2, 9, "rejected", "gate failed")
    sv.ideas._notify_state("idea-1", 2, 9, "validating", None)            # not a verdict: silent
    assert len(got) == 1 and got[0][0][1] == "idea_state" and "rejected" in got[0][0][2]
    sv.ideas.update_config({"notify": False})
    sv.ideas._notify_state("idea-1", 2, 9, "accepted", None)
    assert len(got) == 1 and "accepted" in IdeaService.NOTIFY_STATES


def test_live_api_routes_need_the_order_token(make_v2):
    c, sv, _ = make_v2()
    tok = {"X-Historify-Token": sv.signals.token()}
    assert c.post("/api/v2/signals/deployments/x/live-step", json={"step": 2}).status_code == 403
    assert c.post("/api/v2/signals/deployments/x/demote", json={"reason": "r"}, headers=tok).status_code == 409
    assert c.post("/api/v2/signals/deployments/x/demote", json={}, headers=tok).status_code == 400
    assert c.post("/api/v2/trading/contract-notes", data="a,b\n1,2\n", content_type="text/csv",
                  headers=tok).status_code == 400
    assert c.post("/api/v2/security/secrets/rotate", json={"name": "x", "value": "y"}).status_code == 403
    assert c.get("/api/v2/security/audit/verify").get_json()["ok"] is True
    assert c.get("/api/v2/system/role").get_json()["role"] == "all"
    assert c.get("/api/v2/system/clock").get_json()["server"] is None
    assert c.get("/api/v2/trading/reconciliations").get_json() == []
    # ingest is HMAC-authenticated, open even with authentication on
    sv.auth.create_user("ops", "password-1234")
    sv.auth.set_enabled(True)
    r = c.post("/api/v2/trade-events/ingest", data=b"{}", headers={"X-Historify-Signature": "sha256=x"})
    assert r.status_code == 503                                        # no secret configured: refused, not 401
    sv.settings.set("trade_events_webhook_secret", "link-secret-0000001")
    assert c.post("/api/v2/trade-events/ingest", data=b"{}",
                  headers={"X-Historify-Signature": "sha256=x"}).status_code == 401
    assert c.get("/api/v2/trading/reconciliations").status_code == 401  # everything else needs a login / token
    sv.auth.set_enabled(False)
    assert DRILL_IR["name"]                                            # (drill strategy used across the suite)
    assert httpx is not None
