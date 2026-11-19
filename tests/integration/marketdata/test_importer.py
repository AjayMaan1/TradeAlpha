"""Legacy importer against the L1 fixture built from the unmodified v1 code (gate G2, E2E-32)."""
import json
import os
import shutil
from datetime import date

import pytest
from sqlalchemy import text

from tests.conftest import FIXTURES
from tests.support.mock_provider.server import STATE, expected_bars
from tradealpha.marketdata.bars.base import checksum_frame
from tradealpha.marketdata.ingestion.legacy_v1 import LegacyImporter, sha256_file
from tradealpha.marketdata.providers.normalize import frame_from_payload, normalize_bars
from tradealpha.platform.config.secrets import SecretBox
from tradealpha.platform.db.models import Instrument, SyncState
from tradealpha.platform.time.intervals import resolve

with open(os.path.join(FIXTURES, "legacy_v1.manifest.json")) as _f:
    MANIFEST = json.load(_f)


@pytest.fixture
def legacy_copy(tmp_path):
    p = tmp_path / "historify_v1.db"
    shutil.copy(os.path.join(FIXTURES, "legacy_v1.db"), p)
    return str(p)


@pytest.fixture
def importer(legacy_copy, meta_db, duck_store, tmp_path):
    return LegacyImporter(legacy_copy, meta_db, duck_store, SecretBox(str(tmp_path), backend="file"), str(tmp_path))


def _oracle_series():
    """Expected (symbol, exchange, interval) -> normalised bars, from the manifest + deterministic mock."""
    saved = dict(STATE.config)
    STATE.config.update(MANIFEST["mock_config"])
    STATE.config["mcx_dst"] = False            # the L1 fixture was captured from the mock before it followed US DST
    try:
        exch = dict(map(tuple, MANIFEST["watchlist"]))
        rows = {}
        for syms, iv, s, e in MANIFEST["downloads"]:
            code = "D" if iv in ("D", "1d", "W") else iv
            for sym in syms:
                rows.setdefault((sym, exch[sym], code), []).extend(
                    expected_bars(sym, exch[sym], code, date.fromisoformat(s), date.fromisoformat(e)))
        return {k: normalize_bars(frame_from_payload(v), resolve(k[2]))[0] for k, v in rows.items()}
    finally:
        STATE.config.clear()
        STATE.config.update(saved)


def test_import_reconciles_and_matches_broker_oracle(importer, meta_db, duck_store):
    report = importer.run()
    assert report.reconciled and report.legacy_unchanged and report.backup_path and os.path.exists(report.backup_path)
    assert [c["table"] for c in report.collisions] == ["data_m_m_nse_d"]
    assert report.orphans == ["data_orphanco_nse_d"]
    assert report.rejects_total == 0
    with meta_db.session() as s:
        ids = {(i.symbol, i.exchange): i.id for i in s.query(Instrument)}
    assert len(ids) == 9                                             # watchlist minus removed ORPHANCO
    oracle = _oracle_series()
    checked = 0
    for (sym, ex, code), expected in oracle.items():
        if sym in ("M&M", "M-M", "ORPHANCO"):
            continue
        got = duck_store.read_range(ids[(sym, ex)], resolve(code).id)
        assert checksum_frame(got) == checksum_frame(expected), (sym, code)
        checked += 1
    assert checked == 11
    assert len(report.series) == 11


def test_daily_dates_fixed_from_1830_artifact(importer, duck_store, meta_db):
    importer.run()
    with meta_db.session() as s:
        rid = s.query(Instrument).filter_by(symbol="RELIANCE").one().id
    first = duck_store.read_range(rid, 8, limit=1)["ts"].iloc[0]
    from tradealpha.platform.time.timeutil import ist_date_of_epoch
    assert ist_date_of_epoch(first) == date(2025, 1, 1)              # v1 stored '2024-12-31 18:30'


def test_w_and_1d_tables_merged_into_daily(importer):
    report = importer.run()
    by = {(s["symbol"], s["interval"]): s for s in report.series}
    assert sorted(by[("RELIANCE", "D")]["source_tables"]) == ["data_reliance_nse_d", "data_reliance_nse_w"]
    assert by[("RELIANCE", "D")]["duplicates_merged"] == 61
    assert sorted(by[("SBIN", "D")]["source_tables"]) == ["data_sbin_nse_1d", "data_sbin_nse_d"]
    assert by[("SBIN", "D")]["conflicting_duplicates"] == 0


def test_import_is_idempotent(importer, duck_store):
    importer.run()
    snapshot = duck_store.coverage().to_dict("records")
    rows = duck_store.total_rows()
    second = importer.run()
    assert second.reconciled
    assert duck_store.total_rows() == rows and duck_store.coverage().to_dict("records") == snapshot


def test_dry_run_writes_nothing(importer, meta_db, duck_store, legacy_copy):
    before = sha256_file(legacy_copy)
    report = importer.run(dry_run=True)
    assert report.dry_run and not report.reconciled and report.backup_path is None
    assert duck_store.total_rows() == 0
    with meta_db.session() as s:
        assert s.query(Instrument).count() == 0
    assert sha256_file(legacy_copy) == before
    assert all(s["status"] == "dry_run" for s in report.series)


def test_sync_state_and_encrypted_settings(importer, meta_db):
    importer.run()
    with meta_db.session() as s:
        assert s.query(SyncState).count() == 11
        ss = s.query(SyncState).all()
        assert all(x.bar_count > 0 and x.last_final_ts is None for x in ss)
    with meta_db.engine.connect() as c:
        v = c.execute(text("SELECT value, is_secret FROM app_settings WHERE key='openalgo_api_key'")).one()
    assert v[1] == 1 and v[0] != "fixture-key"
    assert importer.settings.get("openalgo_api_key") == "fixture-key"
