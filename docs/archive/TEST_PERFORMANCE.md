# Test-suite performance: where the time went, and what was fixed

Measured in this sandbox (4 vCPUs, Python 3.14.7). **Caveat:** the VM shares its host - `top` showed 10-48 % CPU
*steal* during the measurements - so the same command varied by up to 2.5x in wall time over the day. Every
before/after pair below was therefore run back to back on the same tree state (`before` = commit `4e812aa` in a git
worktree, `after` = this change), and the structural causes were confirmed with instrumentation, not only with wall
clocks.

## 1. How the bottlenecks were found

| Tool | What it showed |
|---|---|
| JUnit times per layer (`python scripts/test_timings.py`, new) | summed test time per layer, the slowest files and tests - the critical path of each layer |
| `--durations` per layer (printed by `run_tests.sh`) | the 10 slowest tests of every run |
| A pytest plugin wrapping `MultiWindowRateLimiter.acquire` (seconds slept per limiter and per test) | **where the tests were sleeping in rate limiters** (table below) |
| Per-call timers around the paper host + the order route | the 132 s protective-stop test spent 97 s in `sync`, ~100 ms per broker call - all limiter sleep |
| `python -X importtime`, per-phase timers around the kill-9 drill | each drill child paid ~3 s of interpreter start-up + imports; 9 children ran one after another |
| py-spy dumps of stuck / slow xdist workers | the long tail: a 60-90 s test collected last kept one worker busy while three sat idle |
| `time` (user / sys) of whole layers, A/B | which changes cut CPU vs only waiting |

Limiter sleep per run (before):

| Suite | Limiter | Sleep | Verdict |
|---|---|---|---|
| tests/lab | the order route's single 10 req/s limiter (`orders`) - also pacing order-book / trade-book / position-book reads | **~143 s** (1,757 waits) | **waste**: the mock's matching calibration enforces no order/book limits - fixed |
| tests/integration + findings | `angelone:history` (3 req/s x 0.8 budget) | ~280 s | **intended**: these tests prove the download engine never exceeds Angel One's limits (the mock answers 429 above them). A 4x-scaled profile was tried and **reverted**: it made the integration layer slower in A/B (257 s vs 188 s) - the sleeps were overlapping with other workers' CPU work anyway |
| tests/integration | `p4:books` / `p4:orders` (P4 bridge, Angel One 1 book/s) | ~45 s | intended (Analyzer calibration enforces them) |

## 2. Bottlenecks and fixes

| # | Bottleneck | Fix | Effect (A/B, same machine state) |
|---|---|---|---|
| 1 | **The Lab order route paced every broker call at 10/s**, including the three book reads of every `sync` (so a 150-bar replay waited ~100 s) - and in production it did not respect Angel One's separate 1 req/s book limit at all | `OpenAlgoRoute` now uses the broker profile's **per-kind limiters** (orders: profile windows capped at SEBI's 10/s; books: `book_windows`; others: `quote_windows`), **one provider + limiters + a book cache shared by every session on the account** (N sessions no longer make 3N book calls per tick; any order call invalidates the cache). Tests on the mock's matching calibration use the `mock-matching` profile (`tests/mock_provider/profile.py`) | protective-stop test **132 s → 22-37 s**; unified-lifecycle test 75-89 s → 23-44 s; Analyzer-fault test 31 s → 10 s; `tests/lab/test_paper.py` **254 s → 94 s** wall (user CPU 249 → 183 s) |
| 2 | **kill-9 drill ran its 9 child processes one after another**, each paying ~3 s of interpreter start-up | the reference run and the 4 crash points run **concurrently** (each from its own copy of the set-up instance); the children are **forked from one preloaded helper process** (`_ForkServer`, POSIX; a fresh interpreter elsewhere) - still separate processes killed by SIGKILL | drill **47 s → 15-20 s** standalone |
| 3 | `run_drills` ran the 8 drills sequentially | drills run in parallel worker processes (spawned, never forked from a threaded server); inside the test suite (`HISTORIFY_DRILL_WORKERS=1`) kill-9 runs in the background while the in-process drills run | e2e drill step 70 s → 24-29 s; **e2e flow 102 s → 43-57 s** standalone |
| 4 | **Long tests collected last** became the tail of the parallel run (3 of 4 workers idle at the end) | `tests/conftest.py` collects the measured critical-path tests (and `@pytest.mark.slow`) **first**, longest first; `run_tests.sh` / `verify.sh` use `pytest-xdist --dist worksteal` (idle workers take queued tests from busy ones) | Lab layer with the same CPU (~500 s user): **329 s (load) → 183 s (worksteal + longest-first)** |
| 5 | `verify.sh` ran the **Lab layer serially** (550 tests, ~680 s of test time, ~11-12 min) | Lab runs in parallel like every other layer; its perf budgets run afterwards, serially, as `labperf` | Lab in verify.sh **~11.5 min → ~3-4 min** |
| 6 | `bootstrap.sh` rebuilt the Rust wheel with maturin on every run (~1.5 min) and retried the Playwright download (CDN blocked) | a **content stamp** of the Rust sources + toolchain + interpreter in the venv skips an unchanged build (`FORCE_CORE_BUILD=1` forces it); a Chromium recorded in `.sandbox-env` (or already installed for Playwright) is re-used | **147 s → 6 s** when nothing changed (first run unchanged) |
| 7 | A UI test typed into the IR editor before the page's async `init()` filled it (flaky under load) | wait for the editor to be populated first | flake removed (found by this work's repeated runs) |

Not changed, deliberately: the download-engine rate-limit sleeps in the integration layer (they are the subject of
those tests), the per-worker instance-template build (~10 s once per xdist worker; it saves far more than it costs),
and the ideas / backtest tests that run real process pools.

## 3. Results

| Command | Before | After |
|---|---|---|
| `bash scripts/bootstrap.sh` (nothing changed) | 147 s | **6 s** |
| `bash scripts/run_tests.sh unit` | 23-45 s | 23-49 s (unchanged; noise) |
| `bash scripts/run_tests.sh regression` (965 tests) | 393-527 s (A/B runs), 502 s | **232 s** (quiet machine) - 390 s (27 % steal) |
| `bash scripts/run_tests.sh e2e-flow` (22 steps) | 93-171 s | **57-64 s** |
| Lab layer, `-n 4` | 223-256 s | **158-183 s** |
| `bash scripts/verify.sh` (everything, with coverage) | **19 m 43 s** | **11 m 45 s** (705 s, with phases of 58 % CPU steal); lab layer 11.5 min → 3 min 15 s |

Re-measure any time: run the layers, then `python3 scripts/test_timings.py` - it prints each layer's summed test
time and its slowest files and tests from `test-results/junit-*.xml`.
