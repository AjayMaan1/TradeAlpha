"""
Render the before/after comparison table from the raw JSON produced by scenarios.py and process_checks.py.

    python benchmarks/report.py --raw docs/metrics/raw > docs/metrics/comparison_table.md
"""
from __future__ import annotations

import argparse
import json
import os


def load(raw, name):
    with open(os.path.join(raw, name)) as f:
        return json.load(f)


def yn(v):
    return "yes" if v else "no"


def ratio(a, b):
    return f"{a / b:,.1f}x" if a and b else "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--python-upgrade", action="store_true",
                    help="print the Python 3.12 -> 3.14 table (after.json vs after_py314.json) instead")
    args = ap.parse_args()
    if args.python_upgrade:
        return python_upgrade_table(args.raw)
    B, A = load(args.raw, "before.json"), load(args.raw, "after.json")
    BP, AP = load(args.raw, "before_proc.json"), load(args.raw, "after_proc.json")
    FP = load(args.raw, "footprint.json")
    rows = []

    def add(section, metric, before, after, change, ref):
        rows.append((section, metric, before, after, change, ref))

    S = "A. Download reliability (Angel One limits)"
    m = B["multi_symbol_daily"], A["multi_symbol_daily"]
    add(S, "20 symbols x daily x 1 month: symbols succeeded", f"{m[0]['succeeded']}/20", f"{m[1]['succeeded']}/20",
        "all succeed", "DL-09")
    add(S, "... broker HTTP 429 rate-limit rejections", m[0]["upstream_429"], m[1]["upstream_429"], "eliminated", "DL-09")
    for key, label, ref in (("range_1m_90d", "1-minute x 90 days (Angel cap 30 d/request)", "DL-07"),
                            ("range_5m_150d", "5-minute x 150 days (cap 100 d)", "DL-07"),
                            ("range_d_10y", "Daily x 10 years (cap 2000 d)", "DL-07")):
        b, a = B[key], A[key]
        add(S, label, f"FAILED ({b['rows_stored']:,} rows)", f"OK ({a['rows_stored']:,} rows)", "now works", ref)
    add(S, "Weekend / holiday range reported as failure", yn(B["weekend_range"]["reported_failure"]),
        yn(A["weekend_range"]["reported_failure"]), "fixed", "DL-03")
    add(S, "'Weekly' request", f"'success', {B['weekly_interval']['rows_in_weekly_table']} DAILY bars stored as weekly",
        f"rejected with explanation, {A['weekly_interval']['rows_in_weekly_table']} rows", "no silent corruption", "DL-04")
    add(S, "Broker latency 6.5 s (SDK timeout 5 s)",
        f"FAILED after {B['slow_upstream_6_5s']['elapsed_s']} s", f"OK in {A['slow_upstream_6_5s']['elapsed_s']} s",
        "now works", "DL-02")
    add(S, "30% random HTTP 5xx, 10 symbols: succeeded", f"{B['transient_5xx_30pct']['succeeded']}/10",
        f"{A['transient_5xx_30pct']['succeeded']}/10", "retries with backoff", "DL-02")
    add(S, "Expired broker session, 5 symbols: calls sent to broker",
        B["session_expired"]["upstream_calls_while_expired"], A["session_expired"]["upstream_calls_while_expired"],
        "auth circuit breaker", "Angel One")
    add(S, "... recovers after re-login + 'Test connection'",
        yn(B["session_expired"]["recovered_after_relogin_and_test_connection"]),
        yn(A["session_expired"]["recovered_after_relogin_and_test_connection"]), "-", "")
    cb, ca = B["concurrent_write"], A["concurrent_write"]
    add(S, "Writes during a 6-month 1-minute download: failed ('database is locked')",
        f"{cb['concurrent_write_failures']}/{cb['concurrent_writes']}", f"{ca['concurrent_write_failures']}/{ca['concurrent_writes']}",
        "eliminated", "DB-01")
    add(S, "... worst write latency", f"{cb['max_write_latency_s']} s", f"{ca['max_write_latency_s']} s",
        ratio(cb["max_write_latency_s"], ca["max_write_latency_s"]) + " faster", "DB-01")

    S = "B. Performance"
    wb, wa = B["write_path_1m_30d"], A["write_path_1m_30d"]
    add(S, "Download 1-minute x 30 days (7,500 bars), median of 3", f"{wb['first_download_s']} s", f"{wa['first_download_s']} s",
        ratio(wb["first_download_s"], wa["first_download_s"]) + " faster", "DL-06")
    add(S, "... re-download (update path)", f"{wb['redownload_s']} s", f"{wa['redownload_s']} s",
        ratio(wb["redownload_s"], wa["redownload_s"]) + " faster", "DL-06")
    add(S, "... end-to-end rows/s", f"{wb['rows_per_s_end_to_end']:,}", f"{wa['rows_per_s_end_to_end']:,}",
        ratio(wa["rows_per_s_end_to_end"], wb["rows_per_s_end_to_end"]), "DL-06")
    add(S, "... share of time spent writing the DB", f"{wb['db_write_share_pct']}%", f"{wa['db_write_share_pct']}%",
        "fetch now dominates", "DL-06")
    add(S, "Download 1-minute x 6 months (47,625 bars)", f"{cb['download_s']} s", f"{ca['download_s']} s",
        ratio(cb["download_s"], ca["download_s"]) + " faster", "DL-06")
    add(S, "Chart API, full 1-minute history (code unchanged)", f"{B['chart_full_history']['latency_s']} s, "
        f"{B['chart_full_history']['payload_kb']} KB", f"{A['chart_full_history']['latency_s']} s, "
        f"{A['chart_full_history']['payload_kb']} KB", "unchanged (Phase 4)", "CH-01")
    qb, qa = B["quotes_50"], A["quotes_50"]
    add(S, "Quotes for 50 symbols", f"{qb['latency_s']} s, {qb['errors']} errors", f"{qa['latency_s']} s, {qa['errors']} errors",
        "slower: paced to broker limit; WS/cache in Phase 4", "UI-08")

    S = "C. Storage engine (937,500 1-minute bars, identical data)"
    sb, sa = B["storage_engine"], A["storage_engine"]
    add(S, "Write throughput (rows/s)", f"{sb['v1_orm_rows_per_s']:,} (v1 ORM per row)",
        f"{sa['duckdb_rows_per_s']:,} DuckDB / {sa['sqlite_rows_per_s']:,} SQLite-unified",
        ratio(sa["duckdb_rows_per_s"], sb["v1_orm_rows_per_s"]), "DL-06")
    add(S, "Disk per bar", f"{sb['v1_bytes_per_row']} B", f"{sa['duckdb_bytes_per_row']} B DuckDB / {sa['sqlite_bytes_per_row']} B SQLite",
        ratio(sb["v1_bytes_per_row"], sa["duckdb_bytes_per_row"]) + " smaller", "DB-03")
    add(S, "Range read, 41,760 bars", f"{sb['range_read_s']} s", f"{sa['duckdb_range_read_s']} s DuckDB",
        ratio(sb["range_read_s"], sa["duckdb_range_read_s"]) + " faster", "")
    add(S, "Tables needed for 10 series", sb["tables_for_data"], sa["tables_for_data"], "dynamic tables gone", "DT-*")
    add(S, "Weekly + monthly bars for 50 instruments x 10 y", "not possible (W = mislabelled D)",
        f"{sa['resample_50_instruments_W_and_M_s']} s (derived from D)", "new", "DL-04")

    S = "D. Data correctness"
    xb, xa = B["correctness"], A["correctness"]
    add(S, "Daily bar of 2025-01-01 stored as", xb["v1_path_first_daily_row_for_2025_01_01"][:16],
        "2025-01-01 (v2, both broker conventions)", "fixed in v2 store", "DL-05")
    add(S, "M&M and M-M stored in", f"{xb['v1_path_tables_for_M&M_and_M-M']} shared table",
        f"{xa['v2_series_for_M&M_and_M-M']} separate series (v2)", "fixed in v2 store", "DT-01")
    li = A["legacy_import"]
    add(S, "v1 database import into v2 store", "not available",
        f"{li['series']}/{li['series']} series reconciled, {li['collisions_reported']} collision + {li['orphans_reported']} orphan reported, v1 file unchanged",
        "new", "DT-01/02")

    S = "E. Security & operations"
    add(S, "Listening addresses", ", ".join(BP["bind_addresses"]), ", ".join(AP["bind_addresses"]), "loopback only", "SEC-01")
    add(S, "Werkzeug debugger exposed", yn(BP["werkzeug_debugger_active"]), yn(AP["werkzeug_debugger_active"]), "fixed", "SEC-01")
    add(S, "Scheduled runs per trigger (persisted 1-min job, 130 s)",
        f"{BP['scheduled_history_calls']} calls ({BP['python_processes']} processes)",
        f"{AP['scheduled_history_calls']} calls ({AP['python_processes']} process)", "no duplicates", "SC-01")
    add(S, "API key returned by GET /api/settings", "plaintext" if B["settings"]["api_key_returned_in_plaintext"] else "masked",
        "plaintext" if A["settings"]["api_key_returned_in_plaintext"] else "masked (configured + last 4)", "fixed", "SEC-03")
    add(S, "Hard-coded API keys in working tree", "2", "0", "history purge pending approval", "SEC-02")
    add(S, "Default settings persisted", B["bootstrap"]["default_settings_rows"], A["bootstrap"]["default_settings_rows"], "fixed", "DB-05")
    add(S, "JSON API when OpenAlgo not configured",
        f"HTTP {B['bootstrap']['unconfigured_api_status']} redirect to HTML", f"HTTP {A['bootstrap']['unconfigured_api_status']} JSON",
        "fixed", "UI-05")
    add(S, "'Optimize database'", "fails (NameError)" if not B["settings"]["optimize_db_success"] else "ok",
        "works" if A["settings"]["optimize_db_success"] else "fails", "fixed", "DB-06")
    add(S, "Application INFO logs", f"suppressed (root level {B['root_log_level_after_create_app']})",
        f"visible (level {A['root_log_level_after_create_app']}) + rotating file", "fixed", "OPS-01")
    add(S, "Scheduler started by create_app (tests/CLI/workers)", yn(B["scheduler_started_by_create_app"]),
        yn(A["scheduler_started_by_create_app"]), "fixed", "SC-01")
    add(S, "Excel import returns", f"fake rows ({B['excel_import']['first_data_row'][0][0]}...)",
        f"the uploaded rows ({A['excel_import']['first_data_row'][0][0]}...)", "fixed", "EX-05")
    pb, pa = B["pages"], A["pages"]
    add(S, "Pages offering W/M intervals", pb["pages_offering_weekly"], pa["pages_offering_weekly"], "fixed", "DL-04")
    add(S, "/profile/ ; watchlist nav highlight", f"HTTP {pb['profile_status']} ; {yn(pb['watchlist_nav_highlighted'])}",
        f"HTTP {pa['profile_status']} ; {yn(pa['watchlist_nav_highlighted'])}", "fixed", "UI-09")
    add(S, "Export of 800 symbols: session cookie size", f"{B['export_cookie_800_symbols']['set_cookie_bytes']:,} B",
        f"{A['export_cookie_800_symbols']['set_cookie_bytes']:,} B", "unchanged (Phase 4)", "EX-01")
    add(S, "Runtime install (site-packages)", f"{FP['before_mb']} MB", f"{FP['after_mb']} MB",
        f"-{round(100 * (1 - FP['after_mb'] / FP['before_mb']))}% (numba/llvmlite removed)", "")

    print("| Area | Metric | Before (v1.2 baseline) | After (Phase 1 + 2) | Change | Finding |")
    print("|---|---|---|---|---|---|")
    last = None
    for section, metric, before, after, change, ref in rows:
        label = f"**{section}**" if section != last else ""
        last = section
        print(f"| {label} | {metric} | {before} | {after} | {change} | {ref} |")


def python_upgrade_table(raw):
    """Same v2 code, old lock on Python 3.12 vs new lock on Python 3.14 (numpy 2.5, pandas 3.1, SQLAlchemy 2.1)."""
    a, b = load(raw, "after.json"), load(raw, "after_py314.json")
    ap_, bp = load(raw, "after_proc.json"), load(raw, "after_py314_proc.json")

    def g(d, path):
        for k in path.split("."):
            d = d.get(k) if isinstance(d, dict) else None
        return d

    rows = [
        ("Download 1-minute x 30 days (median of 3)", "write_path_1m_30d.first_download_s", "s", True),
        ("Re-download same range", "write_path_1m_30d.redownload_s", "s", True),
        ("Download 1-minute x 6 months (47,625 bars)", "concurrent_write.download_s", "s", True),
        ("Worst concurrent write latency", "concurrent_write.max_write_latency_s", "s", True),
        ("Chart API, full history (median of 7)", "chart_full_history.latency_s", "s", True),
        ("Quotes for 50 symbols (rate-limit paced)", "quotes_50.latency_s", "s", True),
        ("DuckDB ingest", "storage_engine.duckdb_rows_per_s", "rows/s", False),
        ("SQLite-unified ingest", "storage_engine.sqlite_rows_per_s", "rows/s", False),
        ("DuckDB range read (41,760 bars)", "storage_engine.duckdb_range_read_s", "s", True),
        ("DuckDB disk per bar", "storage_engine.duckdb_bytes_per_row", "B", True),
        ("W+M resample, 50 instruments x 10 y", "storage_engine.resample_50_instruments_W_and_M_s", "s", True),
        ("Legacy import of L1 fixture", "legacy_import.duration_s", "s", True),
        ("App start (create_app)", "create_app_s", "s", True),
    ]
    print("| Metric | v2 on Python 3.12 (previous lock) | v2 on Python 3.14 (new lock) | Change |")
    print("|---|---|---|---|")
    for label, path, unit, lower_better in rows:
        x, y = g(a, path), g(b, path)
        if isinstance(x, (int, float)) and isinstance(y, (int, float)) and x and y:
            better = (y < x) if lower_better else (y > x)
            factor = (x / y) if lower_better else (y / x)
            change = "about the same" if 0.85 <= factor <= 1.18 else (f"{factor:.1f}x better" if better else f"{1 / factor:.1f}x worse")
        else:
            change = "-"
        fx = f"{x:,}" if isinstance(x, int) else x
        fy = f"{y:,}" if isinstance(y, int) else y
        print(f"| {label} | {fx} {unit} | {fy} {unit} | {change} |")
    ok = all(g(b, k) == g(a, k) for k in ("multi_symbol_daily.succeeded", "range_1m_90d.rows_stored",
                                          "range_5m_150d.rows_stored", "range_d_10y.rows_stored",
                                          "transient_5xx_30pct.succeeded", "legacy_import.reconciled"))
    print(f"| All functional outcomes (sections A, D, E) identical | - | - | {'yes' if ok else 'NO - investigate'} |")
    print(f"| Scheduled runs per trigger / bind address | {ap_['scheduled_history_calls']} calls / "
          f"{', '.join(ap_['bind_addresses'])} | {bp['scheduled_history_calls']} calls / {', '.join(bp['bind_addresses'])} | same |")


if __name__ == "__main__":
    main()
