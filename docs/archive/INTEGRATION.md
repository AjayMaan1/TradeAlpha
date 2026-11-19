# Integration of flagfix1 + flagfix2

Two fix packages were developed **in parallel from the same baseline** (`4c8f14e`) and never saw each other. This
document records how they were merged, every decision taken where they collided, and what is still duplicated.

| Package | What it contains | Reports (moved into the repo) |
|---|---|---|
| **flagfix1** ("hbt Lab", phases 1.1-1.7) | Rust execution core `hbt-core` + Strategy IR (`hbt-ir`, 86 operators) + Python layer `historify/lab/hbt`; Lab in the app (strategies, runs in worker processes, compare); **paper runtime G1** (strategy host, journal-first OMS, 19 guardrails, kill switch, parity, drills) + first cut of the **G2 OpenAlgo Analyzer route**; research MVP (walk-forward, deflated Sharpe, trial registry, cohorts); T0 readiness review; data readiness + hardening (calendar sync, corporate actions, lot/tick history, replica, restore, notifications, web guard, ...) | `docs/reports/flagfix1/` |
| **flagfix2** (P1-P6) | Reference data (bhavcopy, calendar, corporate actions, index membership, TRI), News & Filings archive, market intelligence, intraday scanner + regime + E17, **P3 Python backtest engine**, **P4 signals** (deployments, replay-at-trigger, shadow + OpenAlgo Analyzer books, parity, promotion gate), P5 portfolios (Track L, tax), P6a F&O, **P6b live operations** (auth, live order gate, WebSocket bars, metrics) | `docs/reports/flagfix2/` |

The folder `flagfix-8cc42a30-...-final (1)` is a byte-identical copy of `flagfix1` (same `changes.diff` md5) and was
not applied twice.

## 1. How it was merged

1. Each package was applied to the baseline on its own branch (`fix1`, `fix2`) and its suite run **in isolation**:
   flagfix1 753 passed / 9 xfailed (with the Rust core built), flagfix2 717 passed / 1 flaky failure (root-caused, §4).
2. **Prep commits** removed the collisions that git cannot see (same module / attribute / URL / table / folder names
   with different meaning), each branch still green on its own.
3. A real three-way **git merge** (`integration`), 17 textual conflicts resolved by hand (below).
4. The integrated suite, the Rust checks and the browser suite were run; post-merge bugs were fixed with tests.

## 2. Name map (what lives where now)

| Concept | flagfix1 | flagfix2 | Integrated |
|---|---|---|---|
| `app.lab.service` | `LabService` (IR strategies, runs) | `ScannerService` | `app.lab.service` = LabService · **`app.lab.scanner_service`** = ScannerService |
| `services.lab` | LabService | ScannerService | `services.lab` = LabService · **`services.scanner`** = ScannerService |
| `app.core.feeds` | module: live **bar** feeds for paper | package: **news & filings** archive | `app.core.feeds` = news archive · **`app.core.bar_feeds`** = bar feeds (`services.bar_feeds`) |
| `app/web/lab_api.py` | hbt Lab blueprint `/api/v2/lab/*` | scanner routes | `lab_api.py` = hbt Lab · **`scanner_api.py`** = `/api/v2/lab/scanner/*`, `/lab/e17`, `/lab/prepare` |
| Backtest runs API | `/api/v2/lab/runs`, `/lab/strategies` ... | **same URLs**, other engine | hbt Lab keeps `/api/v2/lab/*` · P3 engine moved to **`/api/v2/bt/*`** (JS, tests, docs updated) |
| Bar feeds API | `/api/v2/feeds` (+ `/<id>`) | `/api/v2/feeds/*` (news) | news keeps `/api/v2/feeds/*` · bar feeds **`/api/v2/bar-feeds`** |
| Run folders | `instance/lab/runs/` | `instance/lab/runs/` | hbt Lab `instance/lab/runs/` · P3 **`instance/lab/bt/{runs,groups}`** |
| `corporate_actions` table | per instrument (ratio, confirmed) | per symbol/ISIN (bhavcopy-verified) | **`instrument_corporate_actions`** (flagfix1, class `InstrumentCorporateAction`) + `corporate_actions` (flagfix2 refdata). The research replica still publishes `corporate_actions` (reader contract unchanged) |
| CLI `historify lab` | group `lab_cli` (ir, run, paper, kill, research, readiness ...) | group `lab` (scan, regime, prepare, e17) | one group; flagfix2's four commands added to it |
| Nav "Backtests" | Lab → Backtests | Research → Backtests | "Backtests (IR)" / "Backtests (Python)" |

## 3. Decisions taken where the two collided

| Area | Collision | Decision |
|---|---|---|
| **Migrations** | both chains started at 0004 after 0003 | flagfix1 0004-0008, then flagfix2 renumbered **0009-0015** (one linear head `0015`). Model/migration autogenerate diff is empty; 0005 → 0015 → 0005 round-trips. **Existing instances:** a flagfix1-era DB (0008) upgrades normally; a **flagfix2-era DB is adopted automatically** on open (`meta.db.adopt_pre_integration_db`: detects it, applies flagfix1 0004-0008, stamps r+5, upgrades; the pre-upgrade backup is taken first) - tested at flagfix2 0010 and 0007. |
| **Chart bars `adjust`** | two corporate-action systems, two vocabularies (`adjusted/total_return` vs `split/total`) | one source per request, never both: instrument events (flagfix1) when the instrument has any and the broker stored raw prices, otherwise the refdata master (flagfix2, which also un-adjusts broker-adjusted history). Both vocabularies accepted; response adds `adjust_source`. |
| **Calendar writers** | OpenAlgo sync (flagfix1), NSE holiday master + derived-from-bhavcopy (flagfix2) all write `market_calendar` | explicit row ownership in `source`: `manual / import / exchange / derived / openalgo`. The OpenAlgo sync only replaces rows it owns (before: a derived row on the same key made the whole sync transaction fail with a primary-key error). Official imports carry both markers. |
| **Weekly maintenance** | `purge_history` (flagfix1) vs `retention` (flagfix2) for the same audit finding | one step `purge_history` covering the union (jobs, schedule runs, delivered webhooks, resolved issues, released quarantine, read notifications); `step_retention` kept as an alias. |
| **Backup** | flagfix1 hardened it (disk check, cleanup, no rotation of a failed backup, manifest `format`/`schema`); flagfix2 extended its content (news archive, refdata, strategy code) | both: flagfix1's structure with flagfix2's content; the manifest carries the fields both restore paths read. |
| **Restore** | two implementations | CLI `historify restore` uses flagfix2's (restores everything incl. refdata / news / strategies) **plus flagfix1's instance-lock guard** (refuses while the server runs); `--dry-run` alias for `--verify-only`; looks in the configured `backup_dir`. flagfix1's library API stays for `historify backups`. |
| **Request guard** | two host / CSRF guards in series with different policies and error codes (421 `forbidden_host` vs 403 `host_not_allowed`) | one host policy at the WSGI layer (flagfix2's: IP literals, loopback, bind address, `HISTORIFY_ALLOWED_HOSTS`, `*`), one error model (403 `host_not_allowed` / `cross_site`); flagfix1's stricter origin check (also `same-site`) kept. LAN access over an IP works again behind flagfix1's guard. |
| **Moving the OpenAlgo host** | flagfix1: https off-machine + key re-entry off loopback; flagfix2: key re-entry only for public hosts, LAN http fine | **stricter flagfix1 policy kept** (the key places real orders). Settings + Setup pages now offer the explicit `allow_insecure_remote_http` opt-in (it was API-only). |
| **Kill switch** | two independent kill switches (Lab paper vs signals) | **linked**: a global kill on either side also kills the other (after its own kill is committed; a failure on one side never blocks the other; switching OFF is not propagated). |
| **Mock OpenAlgo (tests)** | two trading sides on the same endpoints | flagfix1's matching engine + faults (`trading.py`) and flagfix2's Analyzer sandbox (`sandbox.py`) behind one dispatcher (`trading_mock`, default sandbox; `tests/lab` selects matching). |
| **`app.css`** | both rebuilt the Tailwind bundle | rebuilt from the merged templates (pinned toolchain, `ui/build.py`); the selector set is a superset of both. |
| Dead code | `app/utils/rate_limiter.py`, `data_fetcher_chunked.py` (documented as deleted in OPS-04, still present, failed lint) | removed |

## 4. Bugs found by the integration (fixed, with tests)

| Bug | Where | Test |
|---|---|---|
| OpenAlgo calendar sync aborted with a primary-key error whenever flagfix2 had written a row for the same day | `calendar_sync`, `refdata/calendar`, `refdata/service.sync_holidays` | calendar suites (both) |
| LAN access over an IP refused by the WSGI guard although SECURITY.md promised it | `core/web_guard.py` | `test_dns_rebinding_hosts_are_refused` |
| Restore of a backup under a running server (flagfix2 path had no lock) | `core/integrity/restore.py` | restore suites |
| Two kill switches, each stopping only half of the order paths | `lab/live/paper.py`, `lab/paper/service.py` | `tests/integration/test_integration_links.py` |
| News filings for a newly added instrument stayed unlinked up to 5 min (and for 5 min after boot) - the flaky `test_attachments_follow_policy_and_instruments_are_linked` | `core/feeds/service.py` | same test, now deterministic |
| flagfix2-era instances would crash on start after the renumbering | `core/meta/db.py` | `test_a_flagfix2_era_metadata_db_is_adopted_on_open` |

## 5. Still duplicated at the integration → consolidated by the unification (U0-U5)

Status after the unification: `docs/UNIFICATION_STATUS.md` (decisions: `docs/adr/ADR-001`, `ADR-002`, `ADR-003`). Next phase: `docs/NEXT_PHASE_HLD.md`.

| Capability | flagfix1 | flagfix2 | After U0-U5 |
|---|---|---|---|
| Backtest engine | Rust `hbt-core` + declarative IR (deterministic, look-ahead fuzzed, fast) | Python `app/lab/sim` + Python strategy files (richer: F&O, portfolios, events) | ONE process (P3 pipeline); engine kind hbt for IR (Rust portfolio mode, ADR-003), sim for Python; the 8 built-ins ported to IR |
| Paper trading | `lab_paper` G1 (per-bar host, journal-first OMS, 19 guardrails, drills) + G2 route | `signals` (replay-at-trigger, shadow + Analyzer books, parity, promotion gate) | ONE lifecycle (deployments); IR deployments hosted by `lab_paper`, Python ones on the replay runtime |
| Live orders | not built (G3/G4 planned) | `signals` live gate (P6b) | live only via the Execution Gateway (phase 4); refused for hosted deployments |
| Research statistics | `hbt.research` (WFO, DSR, registry) | `app/lab/eval` (WFO, DSR, Monte Carlo, regimes) | one append-only trial registry; both DSR implementations proven equal |
| Corporate actions | `instrument_corporate_actions` | `corporate_actions` (refdata) | one merged view (`CAEvent`) for both consumers |
| Restore | `app.core.restore` | `app.core.integrity.restore` (used by the CLI) | unchanged (both lock-guarded) |

## 6. Verification (this integration)

Run in the integration sandbox (Python 3.14.7, Rust 1.99.1, 4 vCPU):

| Layer | Result |
|---|---|
| flagfix1 alone (after prep) | 753 passed, 9 xfailed (v1 characterisation) |
| flagfix2 alone | 717 passed, 1 skipped, 1 flaky → root-caused (§4) |
| Lint | ruff clean (app, tests, run.py, benchmarks, scripts); `node --check` on every page script |
| **Integrated: unit + contract + integration + regression + Lab + repo** | **1,223 passed, 0 failed, 1 skipped** (the skip needs live NSE captures from the internet) |
| Integrated: browser E2E (Playwright + Chromium 153) | **62 passed** (every page of both tracks without console errors / CSP violations) |
| Legacy v1 known defects (opt-in) | 9 xfailed (expected) |
| Performance budgets | DuckDB ingest + 5 Lab perf budgets passed |
| Rust | `cargo fmt --check`, `cargo clippy --all-targets -D warnings`, `cargo test` (14) - clean |
| Schema | fresh DB → 0015; model/migration autogenerate diff empty; 0005 → 0015 → 0005 round trip; flagfix2-era DB adoption |
| Sandbox demo (mock OpenAlgo, end to end) | 13/13 steps |

Commands:

```bash
cd historify-master/historify
pytest tests/unit tests/contract tests/integration tests/e2e tests/lab tests/repo -m "not perf" -n 4
HISTORIFY_CHROMIUM=/path/to/chromium pytest tests/ui -n 4
cd rust && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test
```
