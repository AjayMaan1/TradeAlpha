#!/usr/bin/env python3
"""Where does the test time go? Reads the JUnit XML files that run_tests.sh / verify.sh write
(test-results/junit-<layer>.xml) and prints, per layer: tests, summed test time, the slowest files and tests.

    python scripts/test_timings.py [--results test-results] [--top 10]

Summed time > wall time when the layer ran in parallel (pytest-xdist); a single test longer than
(wall / workers) is the layer's critical path - split it or make it cheaper first.
"""
from __future__ import annotations

import argparse
import glob
import os
import xml.etree.ElementTree as ET
from collections import defaultdict


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                                      "test-results"))
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args(argv)
    files = sorted(glob.glob(os.path.join(a.results, "junit-*.xml")))
    if not files:
        print(f"no junit-*.xml in {a.results} - run scripts/run_tests.sh <layer> or scripts/verify.sh first")
        return 1
    grand = 0.0
    for f in files:
        root = ET.parse(f).getroot()
        cases = [(float(tc.get("time") or 0), f"{tc.get('classname')}::{tc.get('name')}") for tc in root.iter("testcase")]
        total = sum(t for t, _ in cases)
        grand += total
        layer = os.path.basename(f)[len("junit-"):-len(".xml")]
        print(f"\n== {layer}: {len(cases)} tests, {total:.0f} s of test time")
        by_file: dict[str, float] = defaultdict(float)
        for t, n in cases:
            by_file[n.split("::")[0]] += t
        for n, t in sorted(by_file.items(), key=lambda x: -x[1])[: min(5, a.top)]:
            print(f"   {t:7.1f} s  {n}")
        print("   slowest tests:")
        for t, n in sorted(cases, reverse=True)[: a.top]:
            print(f"   {t:7.1f} s  {n}")
    print(f"\nall layers: {grand:.0f} s of test time")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
