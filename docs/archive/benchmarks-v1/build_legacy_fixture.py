"""
Build the L1 legacy fixture: a v1.2 database produced by the UNMODIFIED v1 code (plan task 1.8).

    git archive <v1-commit> historify-master | tar -x -C /tmp/v1
    python tests/fixtures/build_legacy_fixture.py --v1-app /tmp/v1/historify-master/historify

It drives the v1 Flask app (test client) against the mock OpenAlgo server and records what it asked for in
``legacy_v1.manifest.json`` so tests can compute the exact expected bars with ``expected_bars()``.

The resulting DB contains every known v1 defect the importer must handle:
  * collided table   (M&M and M-M both write data_m_m_nse_d)                    DT-01
  * orphan table     (ORPHANCO downloaded, then removed from the watchlist)      DT-02
  * W table holding daily bars (RELIANCE 'W')                                   DL-04
  * '1d' table duplicating 'D' rows (SBIN)                                       DT-06
  * daily rows stamped "previous day 18:30" (IST-midnight broker convention)     DL-05
  * NSE_INDEX exchange (unparseable table name), MCX evening session, late listing
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))   # historify/ (for tests.mock_provider)

from tests.mock_provider.server import MockServer  # noqa: E402

WATCHLIST = [("RELIANCE", "NSE"), ("SBIN", "NSE"), ("M&M", "NSE"), ("M-M", "NSE"), ("BAJAJ-AUTO", "NSE"),
             ("NIFTY", "NSE_INDEX"), ("CRUDEOIL", "MCX"), ("TCS", "NSE"), ("ORPHANCO", "NSE"), ("NEWLISTCO", "NSE")]
DOWNLOADS = [  # (symbols, interval, start, end)
    ([s for s, _ in WATCHLIST], "D", "2025-01-01", "2025-03-31"),
    (["RELIANCE"], "W", "2025-01-01", "2025-03-31"),
    (["SBIN"], "1d", "2025-02-01", "2025-02-28"),
    (["SBIN", "NIFTY", "CRUDEOIL"], "5m", "2025-03-24", "2025-03-28"),
    (["SBIN"], "1m", "2025-03-27", "2025-03-28"),
]
REMOVE_AFTER = ["ORPHANCO"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1-app", required=True, help="path to the unmodified v1 'historify' directory")
    ap.add_argument("--out", default=os.path.join(HERE, "legacy_v1.db"))
    args = ap.parse_args()

    server = MockServer().start()
    server.configure(history_limits=[[1000, 1.0]], quote_limits=[[1000, 1.0]], daily_ts="ist_midnight",
                     now="2025-06-30T18:00:00+05:30")
    work = tempfile.mkdtemp()
    shutil.copytree(args.v1_app, os.path.join(work, "historify"))
    app_dir = os.path.join(work, "historify")
    sys.path.insert(0, app_dir)
    os.chdir(app_dir)
    from tradealpha.apps.research import create_app  # v1 package

    app = create_app()
    try:
        from app.utils.scheduler import scheduler_manager  # v1 starts it in create_app
        scheduler_manager.shutdown()
    except Exception:
        pass
    c = app.test_client()
    assert c.post("/api/settings", json={"openalgo_api_key": "fixture-key", "openalgo_api_host": server.url}).status_code == 200
    ids = {}
    for sym, ex in WATCHLIST:
        r = c.post("/watchlist/items", json={"symbol": sym, "exchange": ex, "name": f"{sym} Ltd"})
        assert r.status_code == 201, r.get_json()
        ids[sym] = r.get_json()["item"]["id"]
    exch = dict(WATCHLIST)
    for syms, iv, s, e in DOWNLOADS:
        r = c.post("/api/download", json={"symbols": syms, "exchanges": [exch[x] for x in syms], "interval": iv,
                                          "start_date": s, "end_date": e, "mode": "fresh"}).get_json()
        assert not r["failed"], r["failed"]
        print(f"v1 download {iv:3s} {s}..{e}: {len(r['success'])} symbols")
    for sym in REMOVE_AFTER:
        assert c.delete(f"/watchlist/items/{ids[sym]}").status_code == 200

    db_path = os.path.join(app.instance_path, "historify.db")
    from app.models import db

    with app.app_context():
        db.session.remove()
        db.engine.dispose()
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(db_path)) as src, closing(sqlite3.connect(args.out + ".tmp")) as dst:
        src.backup(dst)
        dst.execute("UPDATE app_settings SET value='fixture-key' WHERE key='openalgo_api_key'")
        dst.execute("UPDATE app_settings SET value='http://127.0.0.1:5000' WHERE key='openalgo_api_host'")
        dst.commit()
        dst.execute("VACUUM")
    os.replace(args.out + ".tmp", args.out)
    manifest = {"watchlist": WATCHLIST, "downloads": DOWNLOADS, "removed": REMOVE_AFTER,
                "mock_config": {"daily_ts": "ist_midnight", "now": "2025-06-30T18:00:00+05:30",
                                "listing_date": {"NEWLISTCO": "2025-03-03"}},
                "built_with": "unmodified v1.2 code (git baseline commit)"}
    with open(os.path.splitext(args.out)[0] + ".manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    server.stop()
    with closing(sqlite3.connect(args.out)) as con:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE name LIKE 'data_%' ORDER BY 1")]
    print(f"wrote {args.out} ({os.path.getsize(args.out) // 1024} KB) tables={tables}")


if __name__ == "__main__":
    main()
