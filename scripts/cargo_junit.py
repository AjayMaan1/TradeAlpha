"""Turn `cargo test` / `cargo fmt` / `cargo clippy` output into JUnit XML for the test report.

    python scripts/cargo_junit.py rust.log <exit code> junit-rust.xml

Every "test path::name ... ok|FAILED|ignored" line becomes a test case; fmt and clippy become one case each (they
pass when the log shows no diff / no error and the combined exit code is 0)."""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET


def main() -> None:
    log_path, rc, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    text = open(log_path, encoding="utf-8", errors="replace").read()
    suite = ET.Element("testsuite", name="rust")
    cases = []
    fmt_bad = "Diff in" in text
    clippy_bad = bool(re.search(r"^error(\[|:)", text, re.M)) and "could not compile" in text
    cases.append(("cargo", "fmt --check", "failure" if fmt_bad else None, "rustfmt found unformatted code"))
    cases.append(("cargo", "clippy -D warnings", "failure" if clippy_bad else None, "clippy reported errors"))
    crate = "rust"
    for line in text.splitlines():
        m = re.match(r"\s*Running .*deps/([A-Za-z0-9_]+)-[0-9a-f]+\)", line)
        if m:
            crate = m.group(1)
        m = re.match(r"^test (\S+) \.\.\. (ok|FAILED|ignored)", line)
        if m:
            status = {"ok": None, "FAILED": "failure", "ignored": "skipped"}[m.group(2)]
            cases.append((crate, m.group(1), status, "cargo test failure"))
    ran_tests = any(c[0] not in ("cargo",) for c in cases)
    if rc != 0 and not any(c[2] == "failure" for c in cases):
        cases.append(("cargo", "build", "failure", f"exit code {rc} (see rust.log)"))
    if not ran_tests and rc == 0:
        cases.append(("cargo", "test", "failure", "no test output found"))
    fails = 0
    for cls, name, status, msg in cases:
        tc = ET.SubElement(suite, "testcase", classname=f"rust.{cls}", name=name, time="0")
        if status == "failure":
            fails += 1
            ET.SubElement(tc, "failure", message=msg)
        elif status == "skipped":
            ET.SubElement(tc, "skipped", message="ignored")
    suite.set("tests", str(len(cases)))
    suite.set("failures", str(fails))
    t = re.findall(r"finished in ([\d.]+)s", text)
    suite.set("time", str(sum(float(x) for x in t)))
    ET.ElementTree(suite).write(out, encoding="utf-8", xml_declaration=True)


if __name__ == "__main__":
    main()
