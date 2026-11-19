"""Audit 2 evidence (app side). Each test ASSERTS THE DEFECT; it is moved to /workspace/audit2 after the run."""
from __future__ import annotations

import os
from datetime import date

import numpy as np
import pytest

from tests.conftest import add_instrument
from tests.integration.test_hardening import D, daily


def test_a2_01_failed_restore_leaves_instance_without_metadata(make_services, monkeypatch):
    from app.core.restore import restore
    from app.core.store.duckdb_store import DuckDBBarStore

    sv = make_services(start=False)
    a = add_instrument(sv, "SBIN")
    days = sv.calendar().trading_days("NSE", date(2025, 3, 3), date(2025, 3, 14))
    sv.store.upsert(a, D, daily(days, np.linspace(100, 110, len(days))))
    bk = sv.maintenance.backup(keep=4)
    inst = sv.instance_path
    sv.close()

    def boom(self, *a, **k):
        raise OSError("No space left on device (simulated during the bars rebuild)")
    monkeypatch.setattr(DuckDBBarStore, "maintenance", boom)
    with pytest.raises(OSError):
        restore(inst, bk["backup"])
    left = sorted(x for x in os.listdir(inst) if not x.startswith("pre-restore"))
    print("instance after the failed restore:", left)
    assert not os.path.exists(os.path.join(inst, "historify_meta.db"))      # DEFECT: meta moved aside, not put back
    assert os.path.exists(os.path.join(inst, "bars.duckdb"))                # DEFECT: half-built store left in place


def test_a2_02_empty_first_calendar_sync_is_recorded_as_success(make_services):
    from app.core.calendar_sync import sync_years

    sv = make_services(start=False)

    class Empty:
        def holidays(self, year):
            return []
    res = sync_years(sv, [2026], provider=Empty())
    print("sync summary:", res["years"], "calendar_source:", sv.settings.get("calendar_source", None))
    assert res["years"][2026]["rows"] == 0
    assert sv.settings.get("calendar_source", None) == "openalgo"          # DEFECT: recorded as a good sync
