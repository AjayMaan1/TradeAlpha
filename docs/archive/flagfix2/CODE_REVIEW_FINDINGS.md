# Code Review Findings (after P4)

| | |
|---|---|
| **Scope** | The whole v2 codebase with P4 in place:<br>• download engine, planner and job service;<br>• scheduler, follow and quotes;<br>• DuckDB store and integrity (ledger, revisions, maintenance);<br>• refdata / intel / feeds schedulers;<br>• lab (snapshot v2, DataPortal, engine, runner, backtests) and the new `app/lab/live/`;<br>• web / API security. |
| **Method** | Code reading focused on state machines, concurrency, time handling and trust boundaries. Each suspected defect was reproduced with a test before it was fixed. |
| **Result** | **11 findings: 1 High · 4 Medium · 6 Low.** 10 are fixed: 7 with regression tests (8 tests in `historify/tests/integration/test_review_fixes.py`) and 3 small Low fixes by inspection (CR-08 to CR-10). 1 (CR-11) is documented with a recommendation. |
| **P4 self-review** | 19 issues in the new P4 code were found and fixed before release. They are listed in §3 so you can see what the review caught. |

Severity scale:

| Severity | Meaning |
|---|---|
| **Critical** | Money or data loss without user action |
| **High** | Credential or account compromise, or silent wrong trading behaviour |
| **Medium** | Wrong results / stuck processing under realistic conditions, or a security weakness that needs a user action to exploit |
| **Low** | Edge cases, robustness, hygiene |

No Critical findings.

---

## 1. Findings by severity

### High

#### CR-01 · DNS rebinding: any web page can drive the local API and steal the OpenAlgo API key (**fixed**)

**Where:**
- `app/web/__init__.py`: before this fix, requests were not checked for the `Host` header.
- `app/web/api.py` `put_settings()`: `openalgo_api_host` could be changed while the stored key was kept.

**What.** Historify binds to 127.0.0.1, which does not stop **DNS rebinding**:
1. A page on `attacker.example` rebinds its name to 127.0.0.1.
2. The browser then treats `http://attacker.example:5001` as same-origin.
3. That page can read every API response and send JSON `PUT`s.

One request, `PUT /api/v2/settings {"openalgo_api_host": "http://attacker.example"}`, made the next download or quote
call send the stored API key to the attacker. With that key, OpenAlgo places orders on the linked Angel One account.
The same attack works against any other route: deleting data, importing a forged calendar, starting jobs, and the
new P4 routes.

**Fix.**
1. An app-wide `request_guards()` refuses unknown `Host` names (`host_not_allowed`, 403):
   - allowed: loopback names, the bind address, and `HISTORIFY_ALLOWED_HOSTS` (`*` switches the check off for a
     validating reverse proxy);
   - IP literals are always accepted, because rebinding needs a DNS name, so LAN access by IP keeps working;
   - the token-bearing Signals page and the order-capable routes also check the host themselves.
2. Moving the broker host to a **new public** address now requires re-entering the key (409 `key_required`), so
   the stored key is never sent to a host it was not entered for. Loopback, private and LAN hosts are unaffected, so
   the v1-import setup flow still works.

**Tests:** `test_dns_rebinding_hosts_are_refused`, `test_moving_the_broker_to_a_public_host_needs_the_key_again`,
`test_signals.py::test_api_deployments_token_protection_and_page`.

### Medium

#### CR-02 · Cross-site form posts (CSRF) reach state-changing endpoints (**fixed**)

**Where:**
- multipart uploads: `app/web/api.py` `POST /calendar/import` (`request.files`), `app/web/refdata_api.py`
  `POST /refdata/indices/import`;
- every `POST` without a body, for example `/engine/resume`, `/storage/backup` and maintenance runs (26 `POST` routes
  in `api.py` alone).

**What.** The design assumption was that JSON-only bodies block simple-form CSRF. That holds for JSON routes,
because `body()` rejects non-JSON content. It does not hold for:
- multipart uploads;
- body-less `POST`s, which a browser sends cross-site without a CORS preflight.

A malicious page could import a forged holiday calendar (bars then get quarantined as "closed day"), import index
histories, or trigger backups and compactions.

**Fix.** `request_guards()` refuses `POST/PUT/PATCH/DELETE` when the browser marks the request cross-site
(`Sec-Fetch-Site: cross-site`, or an `Origin` that differs from the addressed host; `Origin: null` too). Same-origin
pages and non-browser clients (CLI, curl, scripts send no `Origin`) are unaffected.

**Test:** `test_cross_site_state_changes_are_refused`.

#### CR-03 · `enqueue_download(dedupe=True)` drops the whole request when one series is busy (**fixed**)

**Where:** `app/core/jobs/service.py` `enqueue_download()`. Before the fix:
`if active: return EnqueueResult("", 0, "deduplicated")`, where `active` counted pending tasks for *any* of the
requested instruments.

**What.** One instrument with pending work, such as a long backfill, made the call skip **every** instrument. The
caller is told "deduplicated" and nothing downloads. The visible victim is Backtests → readiness → **Repair**
(`LabSnapshots.queue_repairs`, `app/lab/data/snapshot.py:177`): after one symbol of a 200-symbol universe is queued,
repairs for the others silently never run. The P4 UniverseFollow would have had the same problem; it is built
without this API.

**Fix.** Only the (instrument, interval) series with pending work are skipped; the rest is planned. The result is
`deduplicated` only when nothing remains.

**Test:** `test_dedupe_skips_only_the_busy_series`.

#### CR-04 · A job paused while its last task runs hangs in `running` forever after resume (**fixed**)

**Where:**
- `app/core/jobs/engine.py` `_maybe_finish()`: `if job.status == "pausing" and not running: job.status = "paused"`,
  even with zero pending tasks;
- `app/core/jobs/service.py` `resume()`.

**What.**
1. You pause a job while its last task is in flight.
2. The task finishes and the job becomes `paused` with nothing left to run.
3. "Resume" sets it to `running`.
4. No task is left to trigger the engine's completion check, so the job stays `running` for ever.

It counts as active, so a schedule with the default `overlap_policy = skip` **never fires again** ("previous run is
still active"). It also shows as a permanent "active job".

**Fix.** A pausing job with nothing pending finishes normally. `resume()` of a download job with nothing pending
finishes it (`succeeded` / `partial` / `failed`), which also repairs jobs already stuck in this state.

**Test:** `test_a_job_paused_while_its_last_task_runs_finishes_on_resume`.

#### CR-07 · A restarted sweep / walk-forward leaves stale member runs (**fixed**)

**Where:** `app/lab/backtests.py` `_start_group()` and `LabRunner.start()`. The runner re-queues the group's job
after a restart, and the group creates **new** member runs.

**What.** The interrupted attempt's members were left behind:
- members that were `queued` / `running` when the app stopped stay `queued` for ever;
- finished ones are listed twice next to their recomputed twins.

Group tables, counts and "runs" views become wrong, and the stale rows never complete.

**Fix.** When a group (re)starts, members of earlier attempts are deleted together with their artefact folders.
Every member is recomputed anyway.

**Test:** `test_a_restarted_sweep_does_not_leave_stale_members`.

### Low

#### CR-05 · Snapshot GC can delete a partition that a concurrent build has just re-used (**fixed**)

**Where:**
- `app/lab/data/store.py` `write_partition()`: an existing content-addressed file is returned untouched;
- `app/lab/data/snapshot.py` `gc()`: deletes unreferenced partitions older than `keep_days`.

**What.** A snapshot build that re-uses an old, unreferenced partition only references it at the end of the build.
A `gc()` running in between deletes the file. The new snapshot then references a missing partition, and its runs
fail.

**Fix.** `write_partition()` touches a re-used file, and `gc()` skips files whose mtime is newer than the cutoff.

**Test:** `test_gc_keeps_a_partition_reused_by_a_build_in_progress`.

#### CR-06 · Weekly / monthly bars: the period the data ends in is shown as a complete bar (**fixed**)

**Where:** `app/lab/data/portal.py`: before the fix, W / M frames were the plain resample of D.

**What.** In a backtest that ends mid-week (or mid-month), the last decision reads a half week as if it were a
finished weekly bar. Everywhere else, a weekly bar only appears once its week is complete. The last decision can
therefore differ from what the same strategy does live (P4). No built-in strategy uses W / M, so only user strategies
were affected, and only at the final bar.

**Fix.** `DataPortal._finalize()` drops a W / M row whose period continues past the end of the data, according to the
calendar. The LivePortal uses the same rule after its `now` cut. Re-runs of existing W / M runs that ended
mid-period will differ at that final bar, as intended.

**Test:** `test_weekly_bars_never_show_a_half_week_at_the_end_of_the_data`.

#### CR-08 · QuoteService provider race between fetch threads (**fixed**, by inspection)

**Where:** `app/core/quotes.py` `_prov()`, called concurrently by 4 `quotes` threads.

**What.** On the first burst, or after a settings change, two threads could each build a provider. One then closes
the other's `httpx.Client` while it is mid-request, which shows up as a spurious quote error.

**Fix.** Provider creation and replacement is serialised with a lock.

#### CR-09 · The calendar view uses the server's local date (**fixed**)

**Where:** `app/web/api.py` calendar view: the default `from = date.today()`.

**What.** On a UTC host, between 00:00 and 05:30 IST, the view starts on the previous Indian day. Everything else in
the app uses the injected clock in IST.

**Fix.** `sv().clock.now().astimezone(IST).date()`.

#### CR-10 · The RefData scheduler's attempt set grows without bound (**fixed**)

**Where:** `app/core/refdata/service.py` `tick()`, `self._attempted`.

**What.** Keys for every day and slot are added and never removed; this is a slow memory growth in a long-running
server.

**Fix.** Attempts older than 3 days are pruned on every tick.

#### CR-11 · RefData morning tasks re-run after every restart (**open**, documented)

**Where:** `app/core/refdata/service.py` `tick()`. The morning block is guarded only by the in-memory `_attempted`
set.

**What.** Each restart after the morning time re-fetches the ban list and index lists. On Mondays it also re-fetches
the equity master and holidays, and re-runs the corporate-action sync. That is a few extra requests to public NSE
pages; the data stay correct because imports are idempotent.

**Recommendation.** Record the morning run in the refdata `files` table (kind `morning_run`, date) and skip it when
it is present. This was left open because the impact is small and the change touches a P1 module with live-site
behaviour that needs your machine to verify.

---

## 2. Areas reviewed with no defect found

| Area | Notes |
|---|---|
| Download engine writer / watermark | Bars are written first, then the watermark. Upserts never delete. The revision archive is written before the overwrite. Auth pause / probe works. Off-grid responses are refused whole. |
| Planner | Incremental windows re-fetch provisional sessions. Chunking puts the newest chunk first. Repair skips confirmed sessions. |
| Scheduler | Holiday / special-session handling, catch-up, overlap policy (apart from CR-04's stuck job) |
| DuckDB store | Parameterised SQL. `f`-strings contain only constants or quoted paths. The compaction verifies fingerprints before the file swap. |
| Secrets | The API key is write-only and encrypted at rest. The key file and token file are created with mode 0600. |
| Feeds store | Search is parameterised (FTS `MATCH` with a bound argument). Keyset paging is correct. |
| Lab runner | Cancel via file, broken-pool recovery, restart re-queue (apart from CR-07) |
| Export / artefact paths | IDs are resolved through the DB first, so there is no path traversal |

## 3. P4 self-review (found and fixed before release)

| # | Issue in the new P4 code | Fix |
|---|---|---|
| 1 | A sliced / delayed stop or target remainder was emitted as an EXIT signal, which would double-exit a position the broker's stop already closed | EXIT only for open-based exits |
| 2 | An entry was allowed while in position because *any* pending exit (even days away) counted as "exiting" | Only exits due by the entry's fill bar count (fill estimate) |
| 3 | MIS entries after the entry cut-off were emitted (P3 rejects them) | Cut-off rule in live mode |
| 4 | Undeclared default references (INDIAVIX with no 1m) blocked intraday gates | Only the strategy's declared `refs` are gated, on their daily bar |
| 5 | A deployment whose code changed logged a stack trace every 5 s | Paused quietly with the reason |
| 6 | Exit clocks and manual runs could be blocked by the gate | Never blocked (warnings only) |
| 7 | EXIT signals for positions whose ENTER was never acted on | Recorded as `missed` |
| 8 | The data refresh and readiness used job-level dedupe (the CR-03 pattern) | Per-instrument pending check; readiness = no pending task covering today |
| 9 | Parity was skipped for a session when the app was down at 22:45 | Catch-up of the last 4 days |
| 10 | Signals blocked by risk were pushed to the webhook / Telegram | Shown on the page only |
| 11 | OpenAlgo bridge creation race; `/status` made a network call on every page poll | Lock; the Analyzer state is cached by the io loop |
| 12 | An OCO double fill went unnoticed; working limit entries outlived their signal; EXIT left working entries | Alert issue; cancel at validity end; EXIT cancels working entries |
| 13 | Analyzer fills were compared with the latest same-side shadow fill | Matched by the intended fill bar / trade |
| 14 | Consequences of a bar revision were classed `logic`, which paused a healthy deployment | `cascade` classification within the instrument |
| 15 | The page raised a JS error when the tab changed during a refresh | Fetch plan captured per refresh |
| 16 | DB timestamps used the wall clock, not the service clock | All P4 rows use the injectable clock |
| 17 | Entries per day were counted by `created_at` | Counted by decision day |
| 18 | The warm-up cache could mix old and new corporate-action factors | Cache reset whenever the reference tables refresh |
| 19 | The token page was reachable by DNS rebinding | Host guard (became CR-01's app-wide fix) |

## 4. P5 / P6 post-change review (R-01 … R-18)

The review of the Track L portfolios, F&O / options research and live-operations code is in
`P5_P6_IMPLEMENTATION_REPORT.md` §4. Summary: **2 High (incl. R-19 from the test review), 8 Medium, 9 Low - all fixed**, each High / Medium with a test.

| ID | Sev | Finding (short) |
|---|---|---|
| R-01 | High | Continuous futures: at the end of the data every front contract looked like it expired today (premature rolls) |
| R-02 | Medium | Portfolio simulator rebalanced on the last day of any run / live replay (daily "rebalances" mid-month) |
| R-03 | Medium | A late daily bar made the live replay sell a holding as delisted |
| R-04 | Medium | Rebalance decisions could use partially downloaded daily bars |
| R-05 | Medium | Concurrent replays could insert a rebalance row twice |
| R-06 | Medium | A financial year spanning the July-2024 tax change was taxed at the higher rate only |
| R-08 | Medium | Reverse-proxied requests looked local: `/metrics` and first-account creation exposed |
| R-09 | Medium | Portfolio / F&O user strategy code missing from backups and restore |
| R-10 | Medium | `member_mask` crashed on open-ended index memberships read from Parquet |
| R-19 | High | P4 nightly parity matched next-open exits (decided by an EOD run) to the wrong session: false `logic` divergence → healthy EOD deployments auto-paused (found in the test review; fixed, test asserts 41 clean parity checks) |
| R-07, R-11 … R-18 | Low | LTCG 12-month boundary, promoter-selling window, incremental margin, live execution-quality, stream universes, API error codes, Options freeze action, run.py warning, lint |
