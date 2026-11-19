#!/usr/bin/env bash
# =====================================================================================================================
#  TradeAlpha - one-command verification: installs every dependency into an isolated venv and runs all test layers
#  (lint, unit, contract, integration/API E2E, regression, browser E2E, repo hygiene, performance, and the TradeAlpha
#  Lab: Rust crates, Lab suite (engine, IR, paper runtime, research, readiness), determinism), then writes
#  test-results/TEST_REPORT.md.  Exit code 0 only if everything passed.
#
#      bash scripts/verify.sh                      # from the historify-master folder (or give the full path)
#
#  Optional environment variables
#      PYTHON=python3.14   interpreter to use (default: newest of python3.14 / 3.13 / 3.12 / python3 that is >= 3.12;
#                          if none is found and `uv` is installed, uv provides Python 3.14)
#      REPEAT=3            run the complete suite N times and report any test that failed in any run (flakiness)
#      SKIP_UI=1           skip the browser tests
#      SKIP_RUST=1         skip the Rust toolchain, the Lab core build and the Lab layers
#      DEEP_FUZZ=1         look-ahead fuzz with 2,000 generated programs (default 200; ~5 min more)
#      FRESH=1             recreate the virtual environment
#      PYTEST_WORKERS=4    parallel test workers (default auto = one per CPU core; 0 = serial)
#      VENV=path           where to create the virtual environment (default: .venv-verify next to this repo)
#  Needs network access for pip, static.rust-lang.org / crates.io (Rust), and once for Chromium (see bootstrap.sh).
# =====================================================================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT"
OUT="$ROOT/test-results"
VENV="${VENV:-$ROOT/.venv-verify}"
REPEAT="${REPEAT:-1}"
TMO=""; if command -v timeout >/dev/null 2>&1; then TMO="timeout 600"; fi     # macOS has no `timeout`
say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m    %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------------------------- 1-2. dependencies
# Everything (venv + Python packages, Rust toolchain + the Lab core wheel, Chromium) is installed by bootstrap.sh;
# it writes .sandbox-env (PATH for cargo, venv, TRADEALPHA_CHROMIUM, locale) which is sourced here.
say "Bootstrap (dependencies, Rust toolchain, Lab core, Chromium)"
VENV="$VENV" bash "$ROOT/scripts/bootstrap.sh" || { echo "bootstrap failed"; exit 2; }
# shellcheck disable=SC1091
source "$ROOT/.sandbox-env"
VPY="$VENV/bin/python"

# ---------------------------------------------------------------------------------------------- 3. environment
rm -rf "$OUT"; mkdir -p "$OUT"
export TRADEALPHA_SECRET_BACKEND=file           # tests never touch the OS keyring
unset TRADEALPHA_ENGINE TRADEALPHA_INSTANCE_PATH TRADEALPHA_META_DB TRADEALPHA_BARS_DB TRADEALPHA_V1_DB 2>/dev/null || true
export COVERAGE_FILE="$OUT/.coverage"
cd "$APP" || exit 2
"$VPY" - >"$OUT/environment.json" <<'EOF'
import json, platform, sys
from importlib import metadata
pk = {}
for n in ("flask", "sqlalchemy", "alembic", "duckdb", "pandas", "numpy", "httpx", "apscheduler", "pytest",
          "hypothesis", "playwright", "ruff", "pytest-timeout", "hbt-core", "maturin", "TA-Lib", "jsonschema",
          "backtesting"):
    try:
        pk[n] = metadata.version(n)
    except metadata.PackageNotFoundError:
        pk[n] = "-"
import shutil, subprocess
for tool in ("rustc", "cargo"):
    try:
        pk[tool] = subprocess.run([tool, "--version"], capture_output=True, text=True).stdout.split()[1] \
            if shutil.which(tool) else "-"
    except (IndexError, OSError):
        pk[tool] = "-"
print(json.dumps({"python": sys.version.split()[0], "platform": platform.platform(), "packages": pk}))
EOF

# ---------------------------------------------------------------------------------------------- 4. static checks
say "Lint (ruff)"
if "$VENV/bin/ruff" check src tests scripts tools >"$OUT/lint.log" 2>&1 \
   && "$VPY" tools/check_boundaries.py >>"$OUT/lint.log" 2>&1; then
  echo "PASS" >"$OUT/lint.txt"; echo "    ruff: all checks passed"
else
  { echo "FAIL"; cat "$OUT/lint.log"; } >"$OUT/lint.txt"; warn "ruff found problems (see test-results/lint.log)"
fi
if command -v node >/dev/null 2>&1; then
  bad=0; n=0
  for f in src/tradealpha/interfaces/web/static/js/v2/*.js src/tradealpha/interfaces/web/static/js/v2/pages/*.js; do
    n=$((n+1)); node --check "$f" || bad=$((bad+1)); done
  if [ $bad -eq 0 ]; then echo "✅ $n files OK (node --check)" >"$OUT/js_syntax.txt"; else echo "❌ $bad of $n files fail node --check" >"$OUT/js_syntax.txt"; fi
else
  echo "skipped (node not installed; the browser tests execute every page's JavaScript)" >"$OUT/js_syntax.txt"
fi

# ---------------------------------------------------------------------------------------------- 5. test layers
run_layer() {   # name, pytest args...
  local name="$1"; shift
  say "Tests: $name"
  "$VPY" -m pytest "$@" -p no:cacheprovider -q -o junit_family=xunit2 --junitxml="$OUT/junit-$name.xml" \
      --basetemp="$OUT/tmp-$name" >"$OUT/$name.log" 2>&1
  local rc=$?
  tail -n 1 "$OUT/$name.log"
  rm -rf "$OUT/tmp-$name"
  return $rc
}
COV=(--cov=tradealpha --cov-append --cov-report=)
XDIST=(-n "${PYTEST_WORKERS:-auto}" --dist worksteal)                           # parallel workers (pytest-xdist); PYTEST_WORKERS=0: serial
export HBT_FUZZ_EXAMPLES="${HBT_FUZZ_EXAMPLES:-200}"
if [ -n "${DEEP_FUZZ:-}" ]; then export HBT_FUZZ_EXAMPLES=2000; fi
run_layer unit        tests/unit tests/invariants -m "not perf" "${COV[@]}" "${XDIST[@]}"
run_layer contract    tests/contract -m "not perf" "${COV[@]}" "${XDIST[@]}"
run_layer integration tests/integration -m "not perf" "${COV[@]}" "${XDIST[@]}"
TRADEALPHA_E2E_REPORT="$OUT/e2e_trading_flow.json" run_layer e2e tests/e2e -m "not perf" "${COV[@]}" "${XDIST[@]}"
if [ -z "${SKIP_UI:-}" ]; then run_layer ui tests/ui "${XDIST[@]}"; fi
run_layer repo        tests/repo
run_layer perf        tests -m perf -p no:xdist

# ---------------------------------------------------------------------------------------------- 5b. TradeAlpha Lab
if [ -z "${SKIP_RUST:-}" ]; then
  say "Rust: cargo fmt --check, clippy -D warnings, cargo test (hbt-ir, hbt-core)"
  ( cd "$ROOT/rust" && cargo fmt --all -- --check && cargo clippy --workspace --all-targets --release -- -D warnings \
      && cargo test -p hbt-ir -p hbt-core --release ) >"$OUT/rust.log" 2>&1
  rust_rc=$?
  "$VPY" "$ROOT/scripts/cargo_junit.py" "$OUT/rust.log" "$rust_rc" "$OUT/junit-rust.xml"
  grep -E "^test result:" "$OUT/rust.log" | tail -n 3
  say "Determinism: intents / fills / equity hashes, two independent processes"
  "$VPY" tests/support/lab/determinism_hashes.py >"$OUT/determinism-a.json" 2>"$OUT/determinism.log" \
    && "$VPY" tests/support/lab/determinism_hashes.py >"$OUT/determinism-b.json" 2>>"$OUT/determinism.log"
  if cmp -s "$OUT/determinism-a.json" "$OUT/determinism-b.json" && [ -s "$OUT/determinism-a.json" ]; then
    nh="$("$VPY" -c 'import json, sys; d = json.load(open(sys.argv[1])); print(sum(len(v) if isinstance(v, dict) else 1 for v in d.values()))' "$OUT/determinism-a.json")"
    echo "✅ identical across two processes ($nh hashes of intents / fills / equity; CI also compares Linux with Windows)" >"$OUT/determinism.txt"
  else
    echo "❌ hashes differ between two runs (see determinism-*.json)" >"$OUT/determinism.txt"
  fi
  cat "$OUT/determinism.txt"
fi
for ctx in platform marketdata altdata features research ideas lifecycle execution interfaces; do
  "$VENV/bin/coverage" report --include="*/tradealpha/$ctx/*" --omit="*/migrations/*" 2>/dev/null | tail -n 1 \
    | awk -v c="$ctx" '{printf "%-12s %s\n", c, $NF}'
done >"$OUT/coverage.txt"

# ---------------------------------------------------------------------------------------------- 6. repeat runs
if [ "$REPEAT" -gt 1 ]; then
  say "Repeating the whole suite $((REPEAT - 1)) more time(s) to detect intermittent failures"
  for i in $(seq 2 "$REPEAT"); do
    args=(tests -m "not perf")
    if [ -n "${SKIP_UI:-}" ]; then args+=(--ignore=tests/ui); fi
    "$VPY" -m pytest "${args[@]}" -p no:cacheprovider -q -o junit_family=xunit2 --junitxml="$OUT/repeat-$i.xml" \
        --basetemp="$OUT/tmp-repeat" >"$OUT/repeat-$i.log" 2>&1
    tail -n 1 "$OUT/repeat-$i.log"; rm -rf "$OUT/tmp-repeat"
  done
  "$VPY" - "$OUT" "$REPEAT" <<'EOF'
import glob, json, os, sys, xml.etree.ElementTree as ET
from collections import Counter
out, runs = sys.argv[1], int(sys.argv[2])
flaky, clean = Counter(), 1
main_failed = set()
for f in glob.glob(os.path.join(out, "junit-*.xml")):
    for tc in ET.parse(f).getroot().iter("testcase"):
        if tc.find("failure") is not None or tc.find("error") is not None:
            main_failed.add(f"{tc.get('classname')}::{tc.get('name')}")
for t in main_failed:
    flaky[t] += 1
clean = 0 if main_failed else 1
for f in sorted(glob.glob(os.path.join(out, "repeat-*.xml"))):
    failed = {f"{tc.get('classname')}::{tc.get('name')}" for tc in ET.parse(f).getroot().iter("testcase")
              if tc.find("failure") is not None or tc.find("error") is not None}
    clean += 0 if failed else 1
    for t in failed:
        flaky[t] += 1
json.dump({"runs": runs, "clean_runs": clean, "flaky": dict(flaky)}, open(os.path.join(out, "repeat.json"), "w"))
EOF
fi

# ---------------------------------------------------------------------------------------------- 7. report
say "Report"
"$VPY" "$ROOT/scripts/test_report.py" --results "$OUT"
rc=$?
if [ "$rc" -eq 0 ]; then echo "RESULT: ALL PASSED - exit code 0"; else echo "RESULT: FAILURES - exit code $rc"; fi
exit "$rc"
