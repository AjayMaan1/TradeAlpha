"""
Build TEST_REPORT.md (and a short console summary) from the JUnit XML files written by scripts/verify.sh.

    python scripts/test_report.py --results test-results

Layers are discovered from the file names junit-<layer>.xml; extra facts (lint, JS syntax, coverage, environment,
repeat runs) come from the small *.txt / *.json files verify.sh writes next to them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime

LAYERS = [
    ("lint", "Static checks", "ruff (pyflakes, bugbear, imports, pyupgrade) over src, tests, scripts, tools + bounded-context check"),
    ("unit", "Unit", "pure logic: time/IST, calendar (holidays, MCX DST), intervals, rate limiter, error "
             "classification, secrets, planner (property tests), schedule triggers"),
    ("contract", "Contract (storage)", "BarStore contract on DuckDB and SQLite: idempotent writes, upsert, paging, "
                                       "resample, batched export parity, property tests"),
    ("integration", "Integration + API E2E", "download engine under faults (429/5xx/timeouts/truncation/expired "
                                             "session/restart), scheduler time-travel, API v2, data integrity "
                                             "(ledger, quarantine, revisions, audit, archive, rewrite, backups, "
                                             "maintenance, snapshots), 1m roll-up + roll-up audit, quotes, CLI, "
                                             "legacy v1 import, reference data, intelligence, backtests, signals "
                                             "(paper + live order gate), portfolios with tax, F&O / options, auth, "
                                             "WebSocket bars, metrics"),
    ("regression", "Regression (findings)", "one test per review finding: v2 fixes (CP-01..04, DL-05, DT-01, DB-04, "
                                            "EX-01/02), Phase-1 hotfix regressions of the v1 app"),
    ("legacy", "Legacy v1 known defects (opt-in)", "the v1 rollback app's known defects pinned as strict xfail - "
                                                   "'XFail' here is the expected result, not a failure"),
    ("ui", "Browser E2E (Playwright + Chromium)", "every page without console errors or CSP violations, charts, "
                                                  "scheduler, settings, import/export, setup, Data Health, roll-up "
                                                  "source, XSS payloads inert"),
    ("repo", "Repository hygiene", "no secrets, licence, pinned requirements, no dynamic tables"),
    ("perf", "Performance budget", "DuckDB ingest >= 100k rows/s"),
    ("rust", "Lab: Rust crates", "cargo fmt --check, clippy -D warnings, hbt-ir (parser, catalogue, compiler, canonical "
                                 "hash) and hbt-core (kernels, evaluator, engine) unit + property tests"),
    ("lab", "Lab: engine, IR, integration, paper, research, readiness",
     "golden scenarios + fix pack, oracle parity (E3/E5, TA-Lib, backtesting.py), look-ahead fuzz, IR compiler, "
     "Python tier, perf budgets; runs / re-runs / API / CLI / worker processes (1.4); paper host replay equality, "
     "OMS stateful test, guardrails, kill switch, kill-9 drills, nightly parity, G2 analyzer route vs the mock (1.5); "
     "DSR, walk-forward, E9 noise rejection, registry, cohorts (1.6); readiness checklist + sign-off (1.7); "
     "Phase 4 live readiness + the e2e trading flow"),
    ("labperf", "Lab: performance budgets (serial)", "IR run / sweep / streaming step / compile budgets, decision "
                                                     "latency of the per-bar host (p95 <= 0.5 s)"),
]


def parse(path: str) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    out = {"tests": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "xfailed": 0, "time": 0.0,
           "failures": [], "cases": []}
    for su in suites:
        out["time"] += float(su.get("time", 0) or 0)
        for tc in su.iter("testcase"):
            name = f"{tc.get('classname', '')}::{tc.get('name', '')}"
            t = float(tc.get("time", 0) or 0)
            out["tests"] += 1
            out["cases"].append((name, t))
            fail, err, skip = tc.find("failure"), tc.find("error"), tc.find("skipped")
            if fail is not None:
                out["failed"] += 1
                out["failures"].append((name, (fail.get("message") or "")[:300]))
            elif err is not None:
                out["errors"] += 1
                out["failures"].append((name, "ERROR: " + (err.get("message") or "")[:300]))
            elif skip is not None:
                if "xfail" in (skip.get("type", "") + skip.get("message", "")).lower():
                    out["xfailed"] += 1
                else:
                    out["skipped"] += 1
            else:
                out["passed"] += 1
    return out


def read(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return f.read().strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    a = ap.parse_args()
    r = a.results
    env = json.loads(read(os.path.join(r, "environment.json"), "{}") or "{}")
    rows, totals, all_cases, failures = [], Counter(), [], []
    overall_ok = True
    for key, title, what in LAYERS:
        if key == "lint":
            status = read(os.path.join(r, "lint.txt"))
            if status is None:
                continue
            ok = status.startswith("PASS")
            overall_ok &= ok
            rows.append(f"| {title} | {'✅ pass' if ok else '❌ fail'} | - | - | - | - | - | {what} |")
            if not ok:
                failures.append(("ruff", status[:500]))
            continue
        path = os.path.join(r, f"junit-{key}.xml")
        if not os.path.exists(path):
            rows.append(f"| {title} | ⏭ not run | - | - | - | - | - | {what} |")
            continue
        d = parse(path)
        ok = d["failed"] == 0 and d["errors"] == 0
        overall_ok &= ok
        for k in ("tests", "passed", "failed", "errors", "skipped", "xfailed"):
            totals[k] += d[k]
        totals["time"] += d["time"]
        all_cases += d["cases"]
        failures += d["failures"]
        status = "✅ pass" if ok else "❌ fail"
        if ok and d["tests"] and d["skipped"] == d["tests"]:
            status = "⏭ skipped"
        rows.append(f"| {title} | {status} | {d['tests']} | {d['passed']} | {d['failed'] + d['errors']} | "
                    f"{d['skipped']} | {d['xfailed']} | {what} |")
    js = read(os.path.join(r, "js_syntax.txt"))
    det = read(os.path.join(r, "determinism.txt"))
    if det:
        overall_ok &= det.startswith("✅")
    cov = read(os.path.join(r, "coverage.txt"))
    repeat = json.loads(read(os.path.join(r, "repeat.json"), "null") or "null")
    flaky = bool(repeat and repeat.get("flaky"))
    verdict = ("✅ ALL PASSED" if overall_ok and not flaky else
               "⚠️ INTERMITTENT FAILURES (see Repeat runs)" if overall_ok else "❌ FAILURES")
    overall_ok &= not flaky

    lines = ["# Historify - test report", "",
             f"Generated {datetime.now():%Y-%m-%d %H:%M:%S} · Python {env.get('python', '?')} · {env.get('platform', '?')}",
             "", f"**Overall: {verdict}** - {totals['tests']} tests, "
             f"{totals['passed']} passed, {totals['failed'] + totals['errors']} failed, {totals['skipped']} skipped, "
             f"{totals['xfailed']} expected failures (v1 characterisation), {totals['time']:.0f} s", "",
             "| Layer | Result | Tests | Passed | Failed | Skipped | XFail | Covers |", "|---|---|---|---|---|---|---|---|",
             *rows, ""]
    e2e = json.loads(read(os.path.join(r, "e2e_trading_flow.json"), "null") or "null")
    if e2e:
        overall_ok &= bool(e2e.get("ok"))
        lines += ["## E2E trading flow (scripts/e2e_trading_flow.py)", "",
                  f"**{'✅' if e2e.get('ok') else '❌'} {e2e.get('passed')}/{e2e.get('steps')} steps** in "
                  f"{e2e.get('seconds')} s (mock OpenAlgo, research + trader nodes)", "",
                  "| Step | Result | Seconds |", "|---|---|---|"] + \
            [f"| {s['step']} | {'✅' if s['ok'] else '❌ ' + str(s.get('error', ''))[:120]} | {s.get('seconds', '-')} |"
             for s in e2e.get("results", [])] + [""]
    if js:
        lines += [f"**JavaScript syntax:** {js}", ""]
    if det:
        lines += [f"**Lab determinism (NFR-10):** {det}", ""]
    if cov:
        lines += ["**Coverage (unit + contract + integration + regression):**", "", "```", cov, "```", ""]
    if repeat:
        lines += ["## Repeat runs (flakiness check)", "",
                  f"The whole suite was run **{repeat['runs']} times**: {repeat['clean_runs']} clean run(s).", ""]
        if repeat.get("flaky"):
            lines += ["| Test | Failed in runs |", "|---|---|"] + [f"| `{t}` | {n} |" for t, n in repeat["flaky"].items()]
        else:
            lines.append("No test failed in any run - no intermittent failures detected.")
        lines.append("")
    if failures:
        lines += ["## Failures", ""] + [f"- `{n}`: {m}" for n, m in failures] + [""]
    slow = sorted(all_cases, key=lambda x: -x[1])[:10]
    if slow:
        lines += ["## Slowest tests", "", "| Test | Seconds |", "|---|---|"] + \
            [f"| `{re.sub(r'^tests[.]', '', n)}` | {t:.1f} |" for n, t in slow] + [""]
    if env:
        lines += ["## Environment", "", "| Package | Version |", "|---|---|"] + \
            [f"| {k} | {v} |" for k, v in sorted(env.get("packages", {}).items())] + [""]
    lines += ["Raw JUnit XML per layer: `test-results/junit-*.xml` · logs: `test-results/*.log`"]
    report = os.path.join(r, "TEST_REPORT.md")
    with open(report, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[:len(rows) + 9]))
    print(f"\nReport: {report}")
    raise SystemExit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
