# Pre-P5 Audit: Gaps and Flaws in the Current System

| | |
|---|---|
| **Scope** | The whole v2 system after P1–P4:<br>• data platform (engine, planner, jobs, scheduler, store, integrity, backups);<br>• reference data, intelligence, news archive;<br>• research (snapshots, DataPortal, simulator, backtests);<br>• signals and paper trading (P4);<br>• web / API security;<br>• operations (startup, upgrade, backup / restore, retention, monitoring);<br>• readiness for P5 (Track L long-term). |
| **Method** | 1. Full test suite with coverage: **86.7 %** of 18,975 v2 statements.<br>2. Static security scan (bandit).<br>3. A fresh-install smoke run of `run.py`: startup, health, host and CSRF guards.<br>4. Performance budgets (`benchmarks/p3_backtest.py`, `p4_signals.py`).<br>5. A targeted review of failure paths (crash, timeout, key loss, upgrade, restore) and P5 requirements.<br>Each defect was reproduced with a test before it was fixed. |
| **Result** | **31 findings.** In the current system: **3 High · 7 Medium · 13 Low / Info**. For P5: **8 gaps**, 3 of them prerequisites.<br>• **13 fixed now**, with 10 new regression tests: `tests/integration/test_review_fixes.py` and `test_signals.py`.<br>• 10 are documented recommendations (§2).<br>• The 8 P5 gaps are planned into P5 (§3). |
| **Tests after the audit** | Non-UI **679 passed**, 1 skipped, 9 xfailed (669 before the audit) · UI **43 passed** |
| **Verdict** | **Ready for P5**, once the three P5 prerequisites in §4 are scheduled into its first sprint. Nothing found blocks starting P5; all High items are fixed. |

---

## 1. Fixed in this audit

### High

#### A-01 · A backup could not be restored
**Where:** `MaintenanceService.backup` (weekly).

**What.** The weekly backup wrote verified files, but:
- there was no restore command, procedure or test;
- your strategy files (`instance/strategies/`) were **not in the backup**, and deployments refuse to run without the
  exact code they were frozen with.

A backup that has never been restored is unproven.

**Fix:**
- New `flask historify restore <backup> [--verify-only]` (`app/core/integrity/restore.py`). It:
  1. checks every file's sha256 against the manifest, and refuses a damaged backup;
  2. moves the current databases to `instance/pre-restore-<time>/` (nothing is deleted);
  3. restores the metadata DB (upgrading it to this release), bars and the revision archive into a fresh store,
     reference data, the news archive and strategies;
  4. checks row counts against the manifest.
- Backups now include `strategies/`.

**Tests:**
- `test_backup_then_restore_round_trip`: damage after a backup (deleted series, an extra watchlist, a lost strategy),
  restore, then the next start sees the backup's state.
- `test_a_damaged_backup_is_refused`.

#### A-02 · A paper order could be placed twice when the outcome was unknown
**Where:** `paper.py` `_place_orders`. OpenAlgo has no client order id.

**What.**
- A timeout after OpenAlgo had accepted `placeorder` was retried as a new order.
- A crash between the broker call and our commit left the row `pending`, so it was sent again after restart.

Both duplicate the position in the Analyzer book. The bridge's docstring promised a lookup that did not exist.

**Fix: two-phase placement.**
1. The row is committed as `placing` *before* the call.
2. An unknown outcome (network / 5xx / protocol error, or a crash) stays `placing`.
3. The next pass looks the order up in the order book (same symbol, side, quantity, type, product, and not already
   linked to another row) and **adopts** it.
4. Only an order that never arrived goes back to `pending` and is sent again.

**Test:** `test_paper_orders_are_not_duplicated_when_the_outcome_is_unknown` covers both the timeout and the crash case.

#### A-03 · A lost encryption key locked you out of the app
**Where:** `SettingsRepo.get`.

**What.** After a backup is restored on another machine, an OS keyring reset, or a deleted `fernet.key`, decrypting
the stored API key raised an exception. `config()` failed, so **every page and API call returned 500**, including
Settings, where the key would be re-entered. Recovery needed manual DB surgery.

**Fix:** an undecryptable secret is treated as unset, logged, and flagged (`undecryptable: true` in
`/api/v2/settings`). The app shows setup again, and re-entering the key clears the flag.

**Test:** `test_a_lost_encryption_key_does_not_lock_you_out`.

### Medium

| ID | Finding | Fix | Test |
|---|---|---|---|
| A-04 | **Broker stop / target orders were not re-based after corporate actions.** A stop is a fixed level in the adjusted view, so its *raw* level changes on an ex-date (a 1:2 split halves it). The SL-M order left at the old raw price triggered at once. This mattered for CNC swing deployments in paper and would matter in P5 long-term holdings. | `_rebase_protectives` on every paper run: working stop / target orders of the *same trade* whose level differs from the model's current raw level are cancelled and re-placed at the new level and quantity. Audited as `order_rebased`. | `test_protective_orders_are_rebased_after_a_corporate_action` |
| A-05 | **Unbounded metadata-DB growth.** `purge_finished()` existed but was never called. A followed chart creates one job per bar; a 15m deployment creates 25+ jobs a day. Schedule runs, deliveries and resolved issues were never pruned either. | Weekly maintenance step `retention`: finished jobs after 30 days, schedule runs after 180 d, delivered deliveries after 90 d, resolved issues after 180 d (configurable: `retention_days`). Then `PRAGMA optimize`. **Never pruned:** signals, books, journal, parity, audit log, deployment history, backtest runs. | `test_retention_bounds_the_metadata_db_but_keeps_the_decision_record` |
| A-06 | **The quote-budget reservation for paper mode (HLD §13.1) was missing.** The QuoteService could use 8/s of Angel One's 10/s quote limit while the OpenAlgo sandbox needs quotes to price paper fills, so sandbox fills could be rate-limited. | While any deployment is in `paper`, Historify's quote calls are capped at 5/s, leaving ≥ 3/s for OpenAlgo | `test_quotes_leave_budget_for_the_sandbox_while_paper_trading` |
| A-07 | **Schema upgrades ran in place, with no copy of the old DB.** The metadata DB is migrated automatically on start. A failed or unwanted upgrade could not be undone. | Before any upgrade the pre-upgrade DB is copied (SQLite online backup) to `historify_meta.db.pre-<from>-to-<to>.bak`; the newest 3 are kept | `test_a_schema_upgrade_keeps_a_copy_of_the_previous_database` |
| A-08 | **The v1 rollback app had none of the new request guards.** With `HISTORIFY_ENGINE=v1`, DNS rebinding and CSRF were still possible (CR-01 / CR-02 of the previous review). | `install_request_guards()` is shared by both apps | `test_the_v1_rollback_app_has_the_same_request_guards` |

### Low

| ID | Finding | Fix |
|---|---|---|
| A-09 | Each deployment cached its own copy of the bars before today: 10 intraday deployments on NIFTY 200 meant about 10 × 150 MB | One per-session cache shared by all deployments, keyed by series and first day |
| A-10 | The DataPortal read an instrument's 1m partitions twice (once for rolled-up signal bars, once for execution bars) | Stored rows are cached per instrument until it is evicted. P3 benchmark: **A 14.0 → 11.4 s, B 120.8 → 74.5 s** (budgets 15 / 90 s) |
| A-11 | A Telegram delivery error could echo the bot token, which is part of the URL, into the UI and logs | Redacted |
| A-12 | `/healthz` could not show a dead background thread | `dead_threads` + `status: degraded` |
| A-13 | Paper-order edge paths (broker rejection, validity expiry of limit entries, manual cancel, OCO double fill, kill switch with flatten) were untested. Coverage of `paper.py` was 55 %. | Test added (`test_paper_order_edge_paths…`); no defect found |

## 2. Documented, not fixed

| ID | Severity | Finding | Recommendation |
|---|---|---|---|
| A-14 | Medium | **Backups exclude `instance/lab`** (snapshots, partitions, run artefacts) and the news archive's PDF attachments. The promotion evidence itself (the gate reports) is in the metadata DB and is backed up. | Optional `backup.include_lab` (hard links to content-addressed partitions make it cheap). At minimum, snapshots used as promotion evidence. |
| A-15 | Medium | **No dependency vulnerability scan.** `pip-audit` could not reach PyPI from this environment. | Run `pip-audit -r requirements.txt` in CI (network available) on every lock update |
| A-16 | Low | The cost schedule is as of Oct 2024 (STT, exchange fees, stamp duty). Budget changes since then are not verified. | Verify against a current Angel One contract note (the acceptance check in `costs.py`); add a dated row if rates changed |
| A-17 | Low | News feeds (untrusted XML) are parsed with the stdlib `ElementTree`. Modern expat has billion-laughs protection; bandit flags it. | `defusedxml` for defence in depth |
| A-18 | Low | The server is Werkzeug's threaded server, fine for one user on localhost. Every UI poll is logged at INFO, and the rotating logs fill with noise. | Set the `werkzeug` logger to WARNING, or serve with `waitress` |
| A-19 | Low | RefData morning tasks re-run after every restart (CR-11 of the previous review) | Persist the morning run in the refdata `files` table |
| A-20 | Low | Corporate-action events without an announcement time (`available_at`) are treated as known in advance. That is correct for scheduled calendars, a small look-ahead for ex-dates whose feed record had no timestamp. | Use the record date minus the exchange notice period as a conservative `available_at` |
| A-21 | Info | MIS entries on special evening sessions (Muhurat) are rejected by the fixed 15:15 entry cut-off, in both P3 and P4 | Conservative and acceptable; make the cut-off session-relative when needed |
| A-22 | Info | `worker.py` shows 40 % coverage because its code runs in spawned processes that coverage does not follow | `coverage` with `concurrency = multiprocessing` in CI |
| A-23 | Info | P3 benchmark margins vary ±30 % on this shared VM (B measured 74–121 s against a 90 s budget across runs) | Run benchmarks on a quiet machine; treat > 80 % of the budget as a warning |

## 3. P5 readiness: gaps that P5 must close

P5 (Track L: model portfolio, factor toolkit, PIT fundamentals, thesis-breaker alerts) reuses the platform as is.
The following are **missing today** and are P5 work. Three are prerequisites and should come first.

| ID | Gap | Why it matters for P5 | Plan |
|---|---|---|---|
| **A-24** ⚑ | **No symbol-change / ISIN continuity.** Instruments, corporate actions and index membership are keyed by symbol. A rename (e.g. ZOMATO → ETERNAL, 2025) starts a new instrument, so a 10-year history is split and membership breaks. | Long-horizon backtests and holdings silently lose history or double-count | Ingest NSE's symbol-change list into refdata. Add an ISIN-based series identity in the DataPortal that stitches instruments and maps CA / membership through the rename date. (~2 d) |
| **A-25** ⚑ | **Survivorship.** Delisted stocks have no broker history. | Factor and portfolio results are biased upward. (P3 warns about missing members but cannot fill them.) | The bhavcopy archive (`cm_eod`) holds OHLCV for every symbol that traded, including later-delisted ones. Serve D bars for non-broker instruments from it (source = `bhavcopy`, raw prices + the existing CA factors). (~2 d) |
| **A-26** ⚑ | **The benchmark is the NIFTY price index**, with no total-return index | Alpha and information ratio are overstated by the dividend yield (≈ 1.2–1.5 %/yr), which is material for a long-term portfolio | Add NIFTY 50 / NIFTY 500 TRI (niftyindices.com) as reference series and make TRI the default benchmark for CNC runs (roadmap P5 "TRI benchmarks", 1 d) |
| A-27 | The simulator is entry / exit only. It cannot resize a position, has no target weights and no turnover-aware rebalancing. A held instrument's new intent is skipped. | The core of Track L | The P5 rebalance simulator (planned, 5 d). The P4 differ will need a `REBALANCE` action (target qty delta) on the same deployment model. |
| A-28 | No tax model: STCG 20 % / LTCG 12.5 % above ₹1.25 L (since 23 Jul 2024), FIFO lots, year boundaries | After-tax return is the decision metric for long-term holdings | Add a tax-lot ledger to the rebalance simulator's report |
| A-29 | No point-in-time fundamentals or shares outstanding (`security_master` has ISIN, listing date and face value only) | Value / quality factors, market-cap universes | Roadmap P5 (vendor adapter 5 d or XBRL 15 d) |
| A-30 | Ownership / pledge data exist (P2), but there are no composite scores | Thesis-breaker alerts | Roadmap P5 (3 d) |
| A-31 | Deployments assume signals on bar or EOD clocks; there are no monthly or quarterly rebalance calendars | Model-portfolio signals | A `schedule:` variant for month-end / quarter-end in `triggers.py` (0.5 d) |

⚑ = prerequisite: do it in the first P5 sprint, before any long-horizon results are trusted.

## 4. Before you start P5: checklist

1. **Prerequisites A-24, A-25, A-26** at the start of P5 (≈ 5 d inside the P5 estimate).
2. **On your machine** (carried over, still pending):
   - `openalgo-probe --fill-test`;
   - `rollup-audit`;
   - `intel selftest` / `capture-fixtures`;
   - the real-data P3 acceptance runs (E1, E9, E11, E14);
   - deploy E11 / E9 and start the signals → shadow → paper sessions.

   The paper period runs in calendar time, in parallel with P5.
3. **Operations:**
   - run `flask historify maintenance` once;
   - then **test a restore** on a copy: `historify restore <latest> --verify-only`, then a full restore into a
     scratch instance (`HISTORIFY_INSTANCE_PATH=…`);
   - keep `fernet.key` (or the keyring entry) safe **separately** from the backups.
4. CI: add `pip-audit` (A-15) and multiprocessing coverage (A-22).

## 5. Evidence

| Check | Result |
|---|---|
| Full test suite (after fixes) | 679 passed, 1 skipped, 9 xfailed · UI 43 passed |
| Coverage (v2 code) | 86.7 % overall. Lowest: `worker.py` (spawned processes) and `paper.py` (raised by A-13). All new audit code is tested. |
| Bandit (medium+) | 63 × B608 reviewed: every dynamic SQL fragment is a constant, a column list or a quoted path, with values bound. 2 × B324: SHA-1 used as ids, not for security. 1 × B314 → A-17. 1 × B104: bind address is documented. 1 × B704: `Markup` only wraps packaged icon SVGs. |
| Fresh-install smoke (`run.py`) | Clean start, `/healthz` ok, no dead threads. Foreign `Host` → 403, cross-site POST → 403. Clean shutdown. |
| Benchmarks | P3 A 11.4 s / 15, B 74.5 s / 90 · P4 A 6.5 s / 15, B 8.2 s / 10, C 6.1 s / 10 |
