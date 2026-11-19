"""
Process roles (trading-engine Phase 4, L3): one code base, three ways to run it - ``TRADEALPHA_ROLE``:

    all        (default) everything in one process - research, paper and live (single-box installs, tests)
    research   ideas intake + evaluation, backtests, data, research UI, the simulated stages (signals, shadow);
               NO order path: promotions to an order stage (paper, eligible_live, live) are refused, no session
               may use a broker route (analyzer / dry_run / live), the WebSocket stream does not start - export the
               deployment bundle to the trader node instead
    trader     the order path only: hosted sessions, OMS, guardrails, broker reconciliation, the TradeEvent relay to
               the research node; no idea intake, no backtest pool, no news / scanner / intel / reference-data jobs

The split keeps broker credentials and the order path on a small, separately deployed process (see
``deploy/`` and ``docs/OPERATIONS_LIVE.md``), and research load can never starve the order loop.
"""
from __future__ import annotations

from tradealpha.platform.config.env import env

ROLES = ("all", "research", "trader")
# service parts that do NOT start in a role
SKIP = {"research": {"stream"},
        "trader": {"feeds", "scanner", "refdata", "intel", "backtests", "portfolios", "replica"},
        "all": set()}
ORDER_STAGES = ("paper", "eligible_live", "live")


def current() -> str:
    r = (env("ROLE") or "all").strip().lower()
    if r not in ROLES:
        raise SystemExit(f"TRADEALPHA_ROLE must be one of {ROLES} (got {r!r})")
    return r


def runs(role: str, part: str) -> bool:
    return part not in SKIP.get(role, set())


def refuse_orders(role: str) -> bool:
    return role == "research"
