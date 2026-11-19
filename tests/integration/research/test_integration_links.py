"""Cross-track safety links added when flagfix1 (hbt Lab paper runtime) and flagfix2 (P4 signals / live) were
integrated: the two order paths share ONE kill switch."""
from tradealpha.platform.db.models import Deployment


def test_signals_kill_switch_latches_the_hosted_sessions(make_services, monkeypatch):
    """ADR-006: ONE kill switch; the hosted (Lab paper) sessions apply the same latch - they are not stopped."""
    sv = make_services(start=False)
    calls = []
    real = sv.lab_paper.latch
    monkeypatch.setattr(sv.lab_paper, "latch", lambda on, **kw: calls.append((on, kw)) or real(on, **kw))
    res = sv.signals.kill_switch(True, "tester", flatten=True)
    assert res["kill_switch"] is True and sv.safety.killed and sv.signals.config()["kill_switch"] is True
    assert calls and calls[0][0] is True and calls[0][1]["flatten"] == "mis"      # flatten=True means MIS only
    assert res["lab_paper"]["sessions"] == []                    # nothing running: a no-op, but it was asked
    sv.signals.kill_switch(False, "tester")
    assert not sv.safety.killed and sv.signals.config()["kill_switch"] is False


def test_lab_global_kill_also_switches_the_signals_kill_switch_on(make_services):
    sv = make_services(start=False)
    with sv.meta.session() as s:                                 # a paper deployment is exposed to the signals path
        s.add(Deployment(id="d1", name="p", strategy="rsi2", code_sha="x", stage="paper", status="active"))
        s.commit()
    assert sv.signals.config()["kill_switch"] is False
    out = sv.lab_paper.kill(reason="drill", source="test")
    assert out["signals"]["kill_switch"] is True and "lab_paper" not in out["signals"]   # no ping-pong
    assert sv.signals.config()["kill_switch"] is True


def test_a_single_session_kill_stays_local(make_services):
    sv = make_services(start=False)
    import pytest

    from tradealpha.research.runs.lab import LabError
    with pytest.raises(LabError):
        sv.lab_paper.kill(session_id="nope")
    assert sv.signals.config()["kill_switch"] is False


def _flagfix2_era_db(path: str, f2_rev: int = 10):
    """A metadata DB exactly as the flagfix2 build left it: 0001-0003, then flagfix2's migrations (today's files
    0009-0015, the same code), alembic_version holding the flagfix2 revision id."""
    import importlib.util
    import os

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text

    from tradealpha.platform.db.meta import MIGRATIONS_DIR, make_engine, upgrade

    eng = make_engine(path)
    upgrade(eng, "0003")
    vdir = MIGRATIONS_DIR / "versions"
    files = sorted(f for f in os.listdir(vdir) if f[:4].isdigit() and 9 <= int(f[:4]) <= f2_rev + 5)
    with eng.begin() as conn:
        ops = Operations(MigrationContext.configure(conn))
        for f in files:
            spec = importlib.util.spec_from_file_location(f[:-3], vdir / f)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            with Operations.context(ops.migration_context):
                mod.upgrade()
        conn.execute(text("UPDATE alembic_version SET version_num = :v"), {"v": f"{f2_rev:04d}"})
        conn.execute(text("INSERT INTO scan_runs (id, kind, trade_date, as_of, origin, status, universe, eligible, "
                          "params_json, created_at) VALUES ('r1', 'confirm', '2025-03-28', '2025-03-28 04:01:00', "
                          "'manual', 'succeeded', 3, 2, '{}', '2025-03-28 04:01:00')"))
    eng.dispose()


def test_a_flagfix2_era_metadata_db_is_adopted_on_open(tmp_path):
    """Renumbering flagfix2's migrations must not strand an instance created by the flagfix2 build."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import text

    from tradealpha.platform.db.meta import MetaDB, current_revision, head_revision
    from tradealpha.platform.db.models import Base

    for rev in (10, 7):                                          # the flagfix2 head, and an older flagfix2 release
        path = str(tmp_path / f"f2-{rev}.db")
        _flagfix2_era_db(path, rev)
        m = MetaDB(path)
        assert m.adopted == f"flagfix2 {rev:04d} -> {rev + 5:04d}"
        assert m.pre_upgrade_backup and current_revision(m.engine) == head_revision()
        with m.engine.connect() as c:
            assert compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata) == []
            assert c.execute(text("SELECT id FROM scan_runs")).fetchall() == [("r1",)]      # data kept
        m.dispose()
        assert MetaDB(path).adopted is None                       # idempotent: a second open is a normal open


def test_fresh_and_flagfix1_era_dbs_are_not_touched_by_the_adoption(tmp_path):
    from tradealpha.platform.db.meta import MetaDB, make_engine, upgrade
    eng = make_engine(str(tmp_path / "f1.db"))
    upgrade(eng, "0008")                                          # a flagfix1-build instance: same ids, plain upgrade
    eng.dispose()
    assert MetaDB(str(tmp_path / "f1.db")).adopted is None
    assert MetaDB(str(tmp_path / "new.db")).adopted is None


def test_one_trading_view_and_one_kill_endpoint(make_v2):
    """U3: every order of both paths in one list; one kill switch endpoint (order-route token required)."""
    from tradealpha.platform.db.models import Deployment, PaperOrder
    c, sv, _ = make_v2()
    with sv.meta.session() as s:
        s.add(Deployment(id="d1", name="p", strategy="rsi2", code_sha="x", stage="paper", status="active"))
        s.add(PaperOrder(client_id="hfy-1-1", deployment_id="d1", signal_id=1, role="stop", key="NSE:SBIN",
                         action="SELL", pricetype="SL-M", product="MIS", qty=5, trigger_price=480.0, status="open"))
        s.commit()
    rows = c.get("/api/v2/trading/orders?open=1").get_json()
    assert [(r["path"], r["role"], r["price"], r["open"]) for r in rows] == [("signals", "stop", 480.0, True)]
    assert c.post("/api/v2/trading/kill-switch", json={}).status_code == 403              # no token: refused
    r = c.post("/api/v2/trading/kill-switch", json={"flatten": False},
               headers={"X-Historify-Token": sv.signals.token()})
    assert r.status_code == 200 and r.get_json()["kill_switch"] is True and "lab_paper" in r.get_json()
    st = c.get("/api/v2/trading/kill-switch").get_json()
    assert st["signals_kill_switch"] is True
