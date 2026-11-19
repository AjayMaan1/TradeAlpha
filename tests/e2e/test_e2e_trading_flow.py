"""The end-to-end trading flow (scripts/e2e_trading_flow.py) as a test: idea -> evaluation -> deployment -> signals
-> shadow (research node) -> signed bundle -> paper on the OpenAlgo Analyzer (trader node) -> reconciliation ->
drills + sign-off -> eligible_live -> live gate -> dry run -> live orders (Analyzer OFF) -> reconciliation +
contract note -> pilot report -> trade events relayed to the research node -> audit chain -> rotation -> automatic
demotion. Every step must pass; the step list is printed on failure."""
from __future__ import annotations

import importlib.util
import json
import os

import pytest

from tests.support.paths import SCRIPTS

pytestmark = pytest.mark.trading_mock_matching     # the mock OpenAlgo's matching calibration


SCRIPT = os.path.join(SCRIPTS, "e2e_trading_flow.py")


def test_e2e_trading_flow(tmp_path):
    spec = importlib.util.spec_from_file_location("e2e_trading_flow", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.run(str(tmp_path / "nodes"), quiet=True)
    if os.getenv("HISTORIFY_E2E_REPORT"):                  # scripts/verify.sh: the step table in TEST_REPORT.md
        with open(os.environ["HISTORIFY_E2E_REPORT"], "w") as f:
            json.dump(out, f, indent=2, default=str)
    brief = [(s["step"], s["ok"], s.get("error")) for s in out["results"]]
    assert out["ok"], json.dumps(brief, indent=1) + "\n" + next(
        (s.get("trace", "") for s in out["results"] if not s["ok"]), "")
    assert out["steps"] >= 22
