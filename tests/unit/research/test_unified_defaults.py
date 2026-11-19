"""ADR-001: the two kernels share one set of execution defaults (and one cost source)."""
import json
import os

from tests.support.paths import PROJECT
from tradealpha.research.kernel.costs import DEFAULT_SCHEDULE, _data_dir
from tradealpha.research.kernel.rules import ExecConfig

HBT = os.path.join(PROJECT, "rust", "hbt-py", "python", "hbt")


def _hbt_spec_constants() -> dict:
    src = open(os.path.join(HBT, "spec.py")).read()            # no hbt_core needed: read the constants
    ns: dict = {}
    start = src.index("AUTO_PARTICIPATION_INTRADAY =")
    end = src.index("DEFAULT_PLAN =")
    exec(src[start:end], ns)                                    # noqa: S102 - our own constants
    return ns


def test_square_off_participation_and_intrabar_agree():
    ns = _hbt_spec_constants()
    cfg = ExecConfig()
    assert ns["DEFAULT_EXEC"]["squareoff_at"] == cfg.mis_squareoff == "15:10"
    assert ns["AUTO_PARTICIPATION_INTRADAY"] == cfg.participation == 0.05
    assert ns["DEFAULT_EXEC"]["intrabar_policy"] == "pessimistic" and cfg.ambiguity == "stop_first"


def test_costs_come_from_the_hbt_tables():
    st = json.load(open(os.path.join(_data_dir(), "statutory_nse.json")))
    assert DEFAULT_SCHEDULE["source"].startswith("lab/hbt/data")
    mis = [r for r in DEFAULT_SCHEDULE["rates"] if r["product"] == "MIS"]
    assert [round(r["stt_sell"] / 100, 8) for r in mis] == [v["stt_sell"] for v in st["segments"]["EQ_INTRADAY"]]
    assert DEFAULT_SCHEDULE["verified"] is False                # D-13: unverified until checked on a contract note


def test_u3_price_collar_blocks_only_the_aggressive_side_when_the_side_is_known():
    from tradealpha.execution.risk import guardrails as G
    p = G.params(None, 1_000_000.0)
    base = {"symbol": "X", "product": "MIS", "qty": 1, "order_type": "LIMIT", "last_price": 100.0}

    def blocked(**kw):
        return any(b.guardrail.id == "G-PRE-3" for b in G.evaluate({**base, **kw}, p, entry=True))
    assert not blocked(side="BUY", price=50.0)            # passive: can never fill worse than 50
    assert not blocked(side="SELL", price=150.0)
    assert blocked(side="BUY", price=103.0)               # aggressive beyond the 1 % collar: a fat finger
    assert blocked(side="SELL", price=97.0)
    assert blocked(price=50.0)                            # side unknown (older callers): symmetric as before
    assert not blocked(side="BUY", price=101.0 - 0.5)


def test_adr003_the_python_execution_model_maps_onto_the_hbt_exec_spec():
    """ADR-003: the unified pipeline and the paper host give the hbt kernel the Python engine's execution model -
    MIS leverage, the participation remainder rule (entries cancelled, exits carried) and the flagfix2 slippage model
    (bucket bps, >= 1 tick, x1.5 at the session edges, clipped to the bar)."""
    from tradealpha.research.kernel.ir_engine import slippage_spec, unified_spec
    mis, cnc = unified_spec(ExecConfig(product="MIS")), unified_spec(ExecConfig(product="CNC"))
    assert mis["leverage"] == ExecConfig().mis_leverage == 5.0 and cnc["leverage"] == 1.0
    assert mis["participation_remainder"] == cnc["participation_remainder"] == "cancel_entries"
    assert mis["squareoff_at"] == "15:10" and cnc["squareoff_at"] is None
    s = slippage_spec(ExecConfig(), 0.0005)
    ex = ExecConfig()
    assert s == {"slippage_model": "pct", "slippage_value": 0.0005, "slippage_min_ticks": ex.slippage_ticks,
                 "slippage_edge_minutes": ex.edge_minutes, "slippage_edge_widen": ex.edge_widen, "slippage_clip": True}
    assert slippage_spec(ExecConfig(slippage_ticks=0.0), 0.0) == {"slippage_model": "none"}
