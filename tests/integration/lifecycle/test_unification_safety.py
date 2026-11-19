"""Unification U0 - safety defects found when flagfix1 and flagfix2 were compared (docs/UNIFICATION_ANALYSIS.md).

D1  a lost acknowledgement + a lagging order book made the signals path send the same order twice
D2  a strategy reading future bars could be frozen (and so deployed)
"""
from datetime import UTC, datetime, timedelta

import httpx
import pytest


# ---------------------------------------------------------------------------------------------- D1
def _order_setup(sv, mock_server, mode="paper"):
    from tradealpha.platform.db.models import Deployment, PaperOrder, Signal
    admin = lambda **kw: httpx.post(mock_server.url + "/__admin/trading", json=kw).json()   # noqa: E731
    admin(reset=True, analyze=(mode != "live"), ack_lost_next=1, book_lag_s=10)   # flagfix1 fault-injecting mock
    now = datetime.now(UTC).replace(microsecond=0)
    t = int(now.timestamp())
    with sv.meta.session() as s:
        s.add(Deployment(id="dep-aaaaaaaa", name="p", strategy="rsi2", code_sha="x",
                         stage="live" if mode == "live" else "paper", status="active"))
        s.add(Signal(id=1, deployment_id="dep-aaaaaaaa", key="NSE:SBIN", decision_t=t - 60, action="ENTER_LONG",
                     qty=10, valid_until=t + 3600, product="MIS"))
        s.add(PaperOrder(client_id="hfy-1-1", deployment_id="dep-aaaaaaaa", signal_id=1, role="entry", key="NSE:SBIN",
                         action="BUY", pricetype="MARKET", product="MIS", qty=10, place_after=0, mode=mode))
        s.commit()
    sv.signals.market_open = lambda ex, n: True
    return now, admin


def _broker_orders(mock_server):
    st = httpx.get(mock_server.url + "/__admin/trading/state").json()["orders"]
    return list(st.values()) if isinstance(st, dict) else st


def _row(sv):
    from tradealpha.platform.db.models import PaperOrder
    with sv.meta.session() as s:
        o = s.query(PaperOrder).one()
        return {"status": o.status, "oid": o.broker_order_id, "attempts": o.attempts, "error": o.error}


def test_d1_lost_ack_with_a_lagging_book_never_duplicates_a_paper_order(make_services, mock_server):
    sv = make_services(start=False)
    now, admin = _order_setup(sv, mock_server)
    for step in range(4):                                   # io passes 6 s apart, all inside the book lag
        sv.signals._place_orders(now + timedelta(seconds=6 * step))
    assert len(_broker_orders(mock_server)) == 1, "the lost-ack order must not be sent a second time"
    assert _row(sv)["status"] == "placing"
    admin(book_lag_s=0)                                     # the broker book catches up -> adopted
    sv.signals._place_orders(now + timedelta(seconds=30))
    r = _row(sv)
    assert r["status"] == "open" and r["oid"] and len(_broker_orders(mock_server)) == 1


def test_d1_a_live_order_with_an_unknown_outcome_is_never_resent_and_pauses_the_deployment(make_services,
                                                                                            mock_server):
    from tradealpha.platform.db.models import DataIssue, Deployment
    sv = make_services(start=False)
    sv.signals._save_live({**sv.signals.live_config(), "enabled": True, "max_total_notional": 1e9})
    now, admin = _order_setup(sv, mock_server, mode="live")
    sv.signals._place_orders(now)
    assert len(_broker_orders(mock_server)) == 1
    for sec in (60, 130, 400):                              # even long after the grace window: never re-sent
        sv.signals._place_orders(now + timedelta(seconds=sec))
    assert len(_broker_orders(mock_server)) == 1
    r = _row(sv)
    assert r["status"] == "unknown" and "reconcile at the broker" in r["error"]
    with sv.meta.session() as s:
        assert s.get(Deployment, "dep-aaaaaaaa").status == "paused"
        assert s.query(DataIssue).filter(DataIssue.fingerprint == "paper_orders:unknown:hfy-1-1").count() == 1
    admin(book_lag_s=0)                                     # it shows up at the broker -> adopted, still one order
    sv.signals._place_orders(now + timedelta(seconds=410))
    r = _row(sv)
    assert r["status"] == "open" and r["oid"] and len(_broker_orders(mock_server)) == 1


def test_d1_a_request_that_never_left_is_sent_again(make_services, mock_server):
    from tradealpha.execution.routes.bridge import BridgeError
    sv = make_services(start=False)
    now, admin = _order_setup(sv, mock_server)
    admin(ack_lost_next=0, book_lag_s=0)
    br = sv.signals.bridge()
    real, calls = br.place, {"n": 0}

    def refused(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise BridgeError("placeorder: ConnectError", "unsent")
        return real(*a, **k)
    br.place = refused
    sv.signals._place_orders(now)
    assert _row(sv)["status"] == "pending"                  # nothing left the machine: safe to send again
    sv.signals._place_orders(now + timedelta(seconds=6))
    assert _row(sv)["status"] == "open" and len(_broker_orders(mock_server)) == 1


# ---------------------------------------------------------------------------------------------- D2
PEEK = '''
from tradealpha.research.strategies.python_tier import Strategy


class Peek(Strategy):
    name = "peek"
    description = "CHEATS: buys when tomorrow closes above tomorrow's open"
    timeframes = {"signal": "D", "exec": "D"}
    params = {}
    product = "CNC"

    def features(self, d):
        b = d.bars("signal")
        nxt = b["close"].shift(-2) > b["open"].shift(-1)
        return {"up": nxt.fillna(False).astype(bool), "down": (~nxt).fillna(False).astype(bool)}

    def signals(self, f, d):
        return self.long(entry=f.up, exit=f.down, size=("pct_equity", 100.0))
'''


def _seeded(make_services):
    from datetime import date

    from tests.support.lab.bt_synth import seed_universe
    sv = make_services(start=False)
    seed_universe(sv, ["AAA", "BBB"], date(2023, 1, 2), date(2025, 3, 28))
    return sv


def test_d2_a_strategy_that_reads_the_future_cannot_be_frozen(make_services):
    import os

    from tradealpha.research.runs.backtests import BacktestError
    sv = _seeded(make_services)
    os.makedirs(sv.backtests.strategies_dir, exist_ok=True)
    with open(os.path.join(sv.backtests.strategies_dir, "peek.py"), "w") as f:
        f.write(PEEK)
    with pytest.raises(BacktestError) as e:
        sv.backtests.freeze("peek")
    assert e.value.status == 409 and "LOOK-AHEAD" in str(e.value)
    assert not [v for v in sv.backtests.frozen() if v["strategy"] == "peek"]
    from tradealpha.platform.db.models import LabSnapshot
    with sv.meta.session() as s:
        assert s.query(LabSnapshot).count() == 0            # the check's own snapshot is removed afterwards


def test_d2_builtin_strategies_pass_and_the_result_is_recorded_once(make_services):
    sv = _seeded(make_services)
    out = sv.backtests.freeze("rsi2")
    assert out["frozen"] and out["lookahead"]["verified"] is True and out["lookahead"]["instruments"] == 2
    again = sv.backtests.freeze("rsi2")                     # same code: no second check, the stored result
    assert again["lookahead"] == out["lookahead"]


def test_d2_unverified_code_never_reaches_an_order_path(make_services):
    """No data at freeze time -> frozen but UNVERIFIED; the promotion to paper (which places orders) re-runs the
    check and is refused - even when forced - until the strategy is verified on data."""
    from datetime import date

    from tests.conftest import add_instrument
    from tests.support.lab.bt_synth import daily_bars
    from tradealpha.lifecycle.signals.common import SignalError
    from tradealpha.platform.time.intervals import INTERVALS
    sv = make_services(start=False)
    iid = add_instrument(sv, "AAA")                         # known instrument, no bars yet
    out = sv.backtests.freeze("rsi2")
    assert out["frozen"] and out["lookahead"]["verified"] is None and "no bars" in out["lookahead"]["unverifiable"]
    d = sv.signals.create({"strategy": "rsi2", "universe": {"kind": "symbols", "symbols": ["AAA"]}})
    for st_ in ("signals", "shadow"):                       # no orders in these stages: allowed
        sv.signals.promote(d["id"], {"to": st_, "force": True, "reason": "test"})
    with pytest.raises(SignalError) as e:
        sv.signals.promote(d["id"], {"to": "paper", "force": True, "reason": "test"})
    assert "not verified free of look-ahead" in str(e.value)
    days = sv.calendar().trading_days("NSE", date(2023, 1, 2), date(2025, 3, 28))
    sv.store.upsert(iid, INTERVALS["D"].id, daily_bars(days, 3))                # data arrives -> checked at promotion
    sv.signals.promote(d["id"], {"to": "paper", "force": True, "reason": "test"})
    assert sv.backtests.verify_lookahead("rsi2", out["code_sha"])["verified"] is True


# ---------------------------------------------------------------------------------------------- U3 one guardrail set
def test_u3_the_signals_order_path_uses_the_hbt_lab_guardrails(make_services, mock_server):

    import numpy as np

    from tradealpha.lifecycle.signals.common import SignalError
    from tradealpha.platform.db.models import PaperOrder
    from tradealpha.platform.time.intervals import INTERVALS
    sv = make_services(start=False)
    iid = add_instrument_sbin(sv)
    t0 = int(datetime(2025, 3, 28, 9, 15, tzinfo=UTC).timestamp()) - 19_800
    sv.store.upsert(iid, INTERVALS["1m"].id, __import__("pandas").DataFrame(
        {"ts": t0 + 60 * np.arange(30), "open": 500.0, "high": 501.0, "low": 499.0, "close": 500.0,
         "volume": 10_000, "oi": 0}))
    now, admin = _order_setup(sv, mock_server)
    admin(ack_lost_next=0, book_lag_s=0)
    with sv.meta.session() as s:
        s.query(PaperOrder).update({"qty": 50_000})         # 50k shares vs 10k average bar volume (limit 10 %)
        s.add(PaperOrder(client_id="hfy-1-stop", deployment_id="dep-aaaaaaaa", signal_id=1, role="stop",
                         key="NSE:SBIN", action="SELL", pricetype="SL-M", product="MIS", qty=10, trigger_price=450.0,
                         place_after=0))                   # 10 % below the last price: a collar must not block it
        s.commit()
    sv.signals._place_orders(now)
    with sv.meta.session() as s:
        rows = {o.client_id: o for o in s.query(PaperOrder)}
        assert rows["hfy-1-1"].status == "refused" and rows["hfy-1-1"].error.startswith("G-PRE-4")
        assert rows["hfy-1-stop"].status == "open"          # protective: risk-reducing, placed
        s.query(PaperOrder).filter(PaperOrder.client_id == "hfy-1-1").update(
            {"qty": 10, "status": "pending", "error": None})
        s.commit()
    sv.signals._place_orders(now + timedelta(seconds=1))
    assert _row_by(sv, "hfy-1-1")["status"] == "open"       # a normal entry passes
    with pytest.raises(SignalError):
        sv.signals.update_config({"guardrails": {"no_such_rule": 1}})


def add_instrument_sbin(sv):
    from tests.conftest import add_instrument
    return add_instrument(sv, "SBIN")


def _row_by(sv, cid):
    from tradealpha.platform.db.models import PaperOrder
    with sv.meta.session() as s:
        o = s.query(PaperOrder).filter(PaperOrder.client_id == cid).one()
        return {"status": o.status, "error": o.error}
