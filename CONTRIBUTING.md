# Contributing to TradeAlpha

TradeAlpha is licensed under **Apache-2.0** (inherited from upstream historify; ADR-005). By contributing you agree your
contribution is licensed the same way. New runtime dependencies must use an AGPL-compatible licence (MIT, BSD,
Apache-2.0, ISC, PSF, MPL-2.0, LGPL) - CI enforces this with `pip-licenses`.

## Development setup
Python **3.14** is the primary development version (`.python-version`); 3.12 and 3.13 are supported. The Rust
execution kernel is **required** (ADR-005 D-4): toolchain pinned in `rust/rust-toolchain.toml`.
```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt -r requirements-lab.txt
pip install --no-deps -e .                                # the tradealpha package (src/ layout)
maturin develop --release -m rust/hbt-py/Cargo.toml       # hbt_core (Rust) + hbt (Python layer), editable
pre-commit install                                        # ruff + gitleaks on every commit
pytest -m "not perf"                                      # full suite against the mock OpenAlgo server
bash scripts/run_tests.sh unit                            # one layer (lint | unit | invariants | integration | e2e | ui | ...)
```
`bash scripts/bootstrap.sh` does all of the above in `.venv-verify` (including a Chromium for the browser tests);
`bash scripts/verify.sh` runs the whole CI gate locally and writes `test-results/TEST_REPORT.md`.

## Layout (ADR-007)
| Path | What |
|---|---|
| `src/tradealpha/<context>/` | bounded contexts: `platform`, `marketdata`, `altdata`, `features`, `research`, `ideas`, `lifecycle`, `execution` (+ `portfolio`, `learning`, `evolution` as they are built) |
| `src/tradealpha/<context>/api.py` | the context's **public door**: other contexts import it only through this module |
| `src/tradealpha/interfaces/` | API v2 (`api/`), pages + templates + static (`web/`), CLI (`cli/`) |
| `src/tradealpha/apps/` | composition roots: `container` (Services), `research`, `trader`, `serve` |
| `rust/` | the kernel: `hbt-core`, `hbt-ir`, `hbt-py` (+ the `hbt` Python layer in `rust/hbt-py/python/hbt`) |
| `tests/unit/<context>`, `tests/integration/<context>` | tests per context; `tests/integration/cross` for cross-context regression suites |
| `tests/invariants` | safety and market-rule invariants (kill latch, live switch, charges, ticks, leverage, short delivery, MIS square-off, roll-ups, structure) |
| `tests/contract`, `tests/e2e`, `tests/ui`, `tests/repo` | bar-store contract, end-to-end flows, browser tests, repository hygiene |
| `tests/support` | the mock OpenAlgo (`mock_provider`), mock public sites (`mock_feeds`), fixtures, synthetic markets |
| `tools/check_boundaries.py` | the import rules; `tools/boundaries_allowlist.txt` lists known violations (may only shrink) |

## Dependency rule
`interfaces, apps -> ideas, lifecycle, learning, execution, portfolio -> research -> features -> marketdata, altdata ->
platform` (contexts of one layer may import each other). `python tools/check_boundaries.py` enforces the direction
and must pass; an intentional, reviewed exception goes into `tools/boundaries_allowlist.txt` with a comment naming
the fix. Target (not yet enforced - most contexts have no `api.py` yet): import another context only through its
`api.py` (platform is infrastructure and may be imported directly).

## Test layers
`tests/unit` and `tests/invariants` need no services; `tests/integration` runs services, API and CLI against the mock
OpenAlgo (`tests/support/mock_provider/server.py`, Angel One limits, holidays, session expiry, faults; run it
standalone with `python -m tests.support.mock_provider.server`). Tests run in parallel (`pytest -n auto`); every worker
has its own mock broker and temporary instances. `make_services(seed=("key", fn))` builds a seeded instance once per
session and copies it into each test. Tests of the former Lab suite are marked `trading_mock_matching` (the mock's
matching-engine calibration). Performance budgets (`-m perf`) run in the nightly workflow, serially.

## Updating dependencies
Top-level dependencies live in `requirements.in`; `requirements.txt` is a fully pinned, cross-platform lock:
```bash
uv pip compile requirements.in --universal --python-version 3.12 -o requirements.txt
```
Do not generate the lock with `pip freeze` on one OS: it silently drops platform-specific dependencies.

## Rules
- No new per-symbol tables. All bars go through the bar store port (`tradealpha.marketdata.bars`).
- Timestamps: UTC epoch seconds for bar open; daily bars at 00:00 IST (`tradealpha.platform.time.timeutil`). Never
  call `datetime.now()` for market dates - use the injectable clock.
- Everything asynchronous is a durable job (`platform/jobs/runner.py`, handlers registered in `apps/container.py`);
  never start threads from HTTP handlers (checked by `tests/invariants`).
- Safety semantics are fixed (ADR-006): the kill switch is a latch - no new risk, protective stops stay, exits allowed.
  Changes to `execution/`, `*/risk`, `*/safety`, `lifecycle/gates`, `platform/auth` or the migrations need an ADR.
- UI: page templates (`src/tradealpha/interfaces/web/templates`) are shells - data comes from `/api/v2` and is rendered
  with `x-text` (never `innerHTML`). Alpine runs in its CSP build: no inline scripts/handlers/styles - put logic in the
  component (`static/js/v2/pages/*.js`). After changing templates or `ui/input.css`, rebuild and commit the assets:
  `cd ui && npm ci && python build.py` (CI fails if the committed build differs).
- Data integrity: every bar write goes through the engine's single writer (validation → revision check → upsert →
  ledger). Tests that seed the store directly must run the integrity audit afterwards.
- DuckDB has a high fixed cost per query: never loop one query per series on a user-facing path - add a batched
  method to the bar store port.
