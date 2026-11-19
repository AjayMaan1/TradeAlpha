"""Unification U5: one notification bus (trading events are notifications; signals and notifications share ONE
Telegram bot), one research worker budget, and the one-time adoption of the legacy duplicate settings."""
from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from tradealpha.platform.config.registry import adopt_legacy
from tradealpha.platform.db.models import Deployment, Notification


def _notes(sv, kind):
    with sv.meta.session() as s:
        return [(n.level, n.title) for n in s.scalars(select(Notification).where(Notification.kind == kind))]


def test_legacy_duplicate_settings_are_adopted_once(make_services):
    sv = make_services(now="2025-01-15T10:00:00+05:30")
    st = sv.settings
    st.set("signals_telegram_token", "123:legacy")
    st.set("signals", {"telegram": {"enabled": True, "chat_id": "77", "base_url": "https://tg.example"}}, "json")
    st.set("backtest", {"workers": 3, "holdout_months": 6}, "json")
    st.set("compute_workers_during_downloads", 2, "integer")
    done = adopt_legacy(sv)
    assert set(done) == {"signals.telegram", "backtest.workers", "compute_workers_during_downloads"}
    assert st.get("notify_telegram_bot_token") == "123:legacy" and st.get("signals_telegram_token", None) is None
    assert st.get("notify_telegram_chat_id") == "77" and st.get("notify_telegram_api") == "https://tg.example"
    assert st.get("signals")["telegram"] == {"enabled": True}
    assert st.get("lab_workers") == 3 and st.get("backtest") == {"holdout_months": 6}
    assert st.get("lab_workers_during_downloads") == 2
    assert sv.notify.telegram_target() == ("123:legacy", "77", "https://tg.example")
    assert adopt_legacy(sv) == []                                         # idempotent


def test_an_existing_notification_bot_wins_over_the_legacy_signals_bot(make_services):
    sv = make_services(now="2025-01-15T10:00:00+05:30")
    sv.settings.set("notify_telegram_bot_token", "999:app")
    sv.settings.set("notify_telegram_chat_id", "1")
    sv.settings.set("signals_telegram_token", "123:legacy")
    sv.settings.set("signals", {"telegram": {"enabled": True, "chat_id": "77"}}, "json")
    adopt_legacy(sv)
    assert sv.notify.telegram_target()[:2] == ("999:app", "1")
    assert sv.settings.get("signals_telegram_token", None) is None


def test_one_worker_budget_for_both_dispatchers(make_services):
    from tradealpha.research.runs.compute import ComputeBudget
    sv = make_services(now="2025-01-15T10:00:00+05:30")
    sv.backtests.update_config({"workers": 2})                            # the P3 alias ...
    assert sv.settings.get("lab_workers") == 2                            # ... writes the ONE key
    assert sv.lab.workers() == 2 and sv.backtests.workers() == 2 and ComputeBudget(sv).limit() == 2
    assert "workers" not in (sv.settings.get("backtest", None) or {})
    # both dispatchers take their slots from the SAME budget object, with download back-pressure
    assert sv.compute is not None and isinstance(sv.compute, ComputeBudget)
    assert sv.compute.try_acquire() and sv.compute.try_acquire() and not sv.compute.try_acquire()
    sv.compute.release()
    sv.compute.release()
    from tradealpha.platform.db.models import Job, utcnow
    with sv.meta.session() as s:
        s.add(Job(id="dl-busy", kind="download", origin="ui", status="running", params_json="{}", started_at=utcnow()))
        s.commit()
    assert sv.compute.limit() == 1                                         # downloads keep their CPU share
    sv.settings.set("lab_workers_during_downloads", 2, "integer")
    assert sv.compute.limit() == 2


def test_trading_events_are_notifications_once(make_services, mock_server):
    sv = make_services(now="2025-01-15T10:00:00+05:30")
    httpx.post(mock_server.url + "/__admin/trading", json={"reset": True})
    sv.signals.kill_switch(True, "tester")
    notes = _notes(sv, "kill_switch")
    assert notes == [("error", "Kill switch ON - no new entries on any order path")]   # the linked Lab side adds none
    sv.signals.kill_switch(False, "tester")
    sv.lab_paper.kill(reason="drill", source="test")                      # originating on the Lab side: one more
    assert len(_notes(sv, "kill_switch")) == 3
    with sv.meta.session() as s:
        s.add(Deployment(id="d1", name="momo", strategy="x", code_sha="y", stage="signals", status="active"))
        s.commit()
    sv.signals._fail_dep("d1", "parity: 1 logic divergence")
    assert _notes(sv, "deployment_paused") == [("error", "Deployment paused: momo")]


def test_signal_telegram_delivery_uses_the_one_bot(make_services):
    sv = make_services(now="2025-01-15T10:00:00+05:30")
    sv.signals.update_config({"telegram": {"enabled": True, "chat_id": "42", "base_url": "https://tg.example"}})
    sv.signals.set_secret("signals_telegram_token", "123:abc", "test")    # the legacy name still works ...
    assert sv.settings.get("notify_telegram_bot_token") == "123:abc"      # ... and lands on the one key
    assert sv.settings.get("signals")["telegram"] == {"enabled": True}
    assert json.dumps(sv.signals.status()["secrets"]) == json.dumps({"webhook_secret": False, "telegram_token": True})
    assert sv.signals.status()["telegram_chat_id"] == "42"


def test_one_lot_and_tick_history_exchange_past_broker_present(make_services):
    """ONE lot / tick history: the exchange F&O bhavcopy is the record of the past, the broker master covers what the
    exchange files do not (today); ticks come from the broker. Consumers (datasets, paper host, snapshots, E15) read
    it through instrument_attrs.merged_history / lot_on."""
    from datetime import date

    import pandas as pd

    from tradealpha.marketdata.refdata import instrument_attrs as ia
    from tradealpha.platform.db.models import Instrument
    sv = make_services(now="2025-03-10T10:00:00+05:30")
    with sv.meta.session() as s:
        fut = Instrument(symbol="NIFTY25MARFUT", exchange="NFO", instrument_type="FUT", segment="FUT",
                         underlying="NIFTY", tick_size=0.05)
        s.add(fut)
        s.flush()
        iid = fut.id
        ia.record(s, iid, 75, 0.05, date(2025, 3, 3))              # broker master: observed from 3 March 2025
        s.commit()
    for d, lot in ((date(2024, 1, 2), 50), (date(2024, 6, 28), 50), (date(2024, 7, 1), 25), (date(2025, 2, 28), 25)):
        sv.refdata.store.replace_day("fo_eod", d, pd.DataFrame({"trade_date": [d], "instrument": ["FUTIDX"],
                                                                "symbol": ["NIFTY"], "lot_size": [float(lot)]}))
    hist = ia.merged_history(sv, iid)
    assert [(h["valid_from"], h["lot_size"], h["source"]) for h in hist] == [
        ("2024-01-02", 50, "exchange"), ("2024-07-01", 25, "exchange"), ("2025-03-03", 75, "broker")]
    assert all(h["tick_size"] == 0.05 for h in hist)              # exchange periods take the instrument's tick
    assert ia.lot_on(sv, date(2024, 3, 1), instrument_id=iid) == 50   # a past day: the lot IN FORCE then ...
    assert ia.lot_on(sv, date(2024, 8, 1), underlying="NIFTY") == 25
    assert ia.lot_on(sv, date(2025, 3, 10), instrument_id=iid) == 75  # ... today: the broker master
    assert ia.lot_on(sv, date(2025, 3, 10), underlying="NIFTY") == 75
    # the engine's time-versioned lot changes come from the merged history
    import sys
    sys.path.insert(0, "lab")
    from hbt.dataset import core_changes
    ch = core_changes(hist)
    assert list(ch["lot"]) == [50, 25, 75] and list(ch["tick"]) == [5, 5, 5]


def test_one_follow_service_stream_bar_closes_wake_the_recorded_downloads(make_services):
    """FeedService.follow is the one live-bar download entry point (feeds, hosted deployments, the replay runtime's
    UniverseFollow); the WebSocket stream is an optional source that only WAKES it at a bar close (tick-built bars
    are never the record)."""
    from types import SimpleNamespace

    from tradealpha.platform.db.models import Instrument, Job
    sv = make_services(now="2025-01-15T10:00:00+05:30")
    with sv.meta.session() as s:
        inst = Instrument(symbol="SBIN", exchange="NSE", name="SBIN")
        s.add(inst)
        s.commit()
        iid = inst.id
    res = sv.bar_feeds.follow([iid], "1m", origin="signals", label="test follow", dedupe=False)
    with sv.meta.session() as s:
        job = s.get(Job, res.job_id)
    assert job.origin == "signals" and sv.bar_feeds.follows == 1
    assert not sv.bar_feeds._wake.is_set()
    sv.stream._on_final("NSE:SBIN", SimpleNamespace(to_dict=lambda: {"ts": 0, "close": 1.0}))
    assert sv.bar_feeds.stream_wakeups == 1 and sv.bar_feeds._wake.is_set()
    # the replay runtime's decision-point refresh has no other download path left
    import inspect

    from tradealpha.lifecycle.signals import service
    assert "enqueue_download" not in inspect.getsource(service.SignalService._refresh_data)


def test_one_physical_orders_table_for_every_order_path(make_services, tmp_path):
    """Execution Gateway: the hbt host's OMS journal (LabOrder) and the signals / live path (PaperOrder) are rows of
    ONE table (orders.path), one id space; per-path CHECK constraints keep each path's required fields; the old table
    names are read-only views; a database of the previous schema is migrated keeping every row (lab order ids kept,
    signals orders renumbered after them, fills still pointing at their orders)."""
    import sqlalchemy as sa
    from sqlalchemy import text

    from tradealpha.platform.db.lab_models import LabOrder
    from tradealpha.platform.db.meta import make_engine, upgrade
    from tradealpha.platform.db.models import Order, PaperOrder
    sv = make_services(now="2025-01-15T10:00:00+05:30")
    with sv.meta.session() as s:
        s.add(PaperOrder(client_id="hfy-1-1", deployment_id="d1", signal_id=1, role="entry", key="NSE:SBIN",
                         action="BUY", pricetype="MARKET", product="MIS", qty=5))
        s.commit()
        row = s.scalar(sa.select(Order))
        assert row.path == "signals" and isinstance(row, PaperOrder) and row.side == "BUY" and row.action == "BUY"
        assert row.status == "pending" and row.mode == "paper"
        assert s.scalars(sa.select(LabOrder)).all() == []             # each class sees its own path only
        with pytest.raises(sa.exc.IntegrityError):                   # a lab row without its OMS fields
            s.add(LabOrder(symbol="X", exchange="NSE", side="BUY", qty=1, reason="entry"))
            s.commit()
        s.rollback()
    with sv.meta.engine.connect() as c:
        assert c.execute(text("SELECT action, pricetype FROM paper_orders")).fetchall() == [("BUY", "MARKET")]
        assert c.execute(text("SELECT count(*) FROM lab_orders")).scalar() == 0
    # the migration of a previous-schema database keeps every row
    db = str(tmp_path / "old.db")
    eng = make_engine(db)
    upgrade(eng, "0018")
    with eng.begin() as c:
        c.execute(text("INSERT INTO lab_paper_sessions (id, name, status, mode, interval, adjustment, exec_json, "
                       "costs_json, guardrails_json, cash, warmup_days, created_at, deployment_id) VALUES "
                       "('s1', 'x', 'running', 'paper', '5m', 'raw', '{}', '{}', '{}', 1e6, 20, '2025-01-01', 'dep')"))
        c.execute(text("INSERT INTO lab_strategies (id, name, created_at) VALUES (1, 'st', '2025-01-01')"))
        c.execute(text("INSERT INTO lab_strategy_versions (id, strategy_id, version, ir_json, ir_sha256, ops_version,"
                       " created_at, frozen) VALUES (1, 1, 1, '{}', 'x', 1, '2025-01-01', 0)"))
        c.execute(text("INSERT INTO instruments (id, symbol, exchange, is_active, created_at) VALUES "
                       "(1, 'SBIN', 'NSE', 1, '2025-01-01')"))
        c.execute(text("INSERT INTO lab_paper_members (id, session_id, strategy_version_id, instrument_id, "
                       "params_json, status) VALUES (1, 's1', 1, 1, '{}', 'active')"))
        for i in (1, 2):
            c.execute(text(f"INSERT INTO lab_orders (id, session_id, member_id, idem_key, symbol, exchange, side, qty, "
                           f"order_type, reason, status, bar_ts, filled_qty, created_at) VALUES ({i}, 's1', 1, "
                           f"'k{i}', 'SBIN', 'NSE', 'BUY', 1, 'MARKET', 'entry', 'filled', 1, 1, '2025-01-01')"))
        c.execute(text("INSERT INTO lab_fills (id, order_id, qty, price, fees, bar_ts, seq, created_at) VALUES "
                       "(1, 2, 1, 100.0, 0, 1, 0, '2025-01-01')"))
        c.execute(text("INSERT INTO paper_orders (id, client_id, deployment_id, signal_id, role, key, action, "
                       "pricetype, product, qty, place_after, status, filled_qty, attempts, created_at, updated_at, "
                       "mode) VALUES (1, 'hfy-9-1', 'd9', 9, 'entry', 'NSE:TCS', 'SELL', 'LIMIT', 'CNC', 3, 0, "
                       "'open', 0, 0, '2025-01-01', '2025-01-01', 'paper')"))
    upgrade(eng, "head")
    with eng.connect() as c:
        rows = c.execute(text("SELECT id, path, symbol, exchange, side, role, deployment_id FROM orders ORDER BY id"))
        assert rows.fetchall() == [(1, "lab", "SBIN", "NSE", "BUY", "entry", "dep"),
                                   (2, "lab", "SBIN", "NSE", "BUY", "entry", "dep"),
                                   (3, "signals", "TCS", "NSE", "SELL", "entry", "d9")]
        assert c.execute(text("SELECT o.id FROM lab_fills f JOIN orders o ON o.id = f.order_id")).scalar() == 2
        assert c.execute(text("SELECT client_id, action FROM paper_orders")).fetchall() == [("hfy-9-1", "SELL")]
    eng.dispose()
