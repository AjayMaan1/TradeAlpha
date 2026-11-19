# ADR-001 · One set of execution defaults for both kernels

| | |
|---|---|
| Status | **Accepted** (owner, unification U1) |
| Context | flagfix1 (`hbt` ExecSpec) and flagfix2 (`ExecConfig`) shipped different defaults, so the same idea could get two verdicts (`docs/UNIFICATION_ANALYSIS.md`, D8). |
| Enforced by | `tests/unit/test_unified_defaults.py` (both tables must agree) |

| Setting | flagfix1 | flagfix2 | **Decision** | Where |
|---|---|---|---|---|
| MIS engine square-off | 15:10 | 15:15 | **15:10** - before the broker's auto square-off window | `DEFAULT_EXEC.squareoff_at`, `ExecConfig.mis_squareoff` |
| Stop and target inside one bar | pessimistic | stop_first | **pessimistic** = stop first for a bracket (identical for single brackets) | `intrabar_policy`, `ambiguity` |
| Participation cap (intraday) | 10 % | 5 % | **5 %** of bar volume | `AUTO_PARTICIPATION_INTRADAY`, `ExecConfig.participation` |
| Slippage | none / ticks / pct | size-bucketed bps + session-edge widening | **flagfix2 model** (large 2 bps, mid 5, small 10, ×1.5 in the first/last 15 min, at least 1 tick) | `ExecConfig` today; the hbt kernel gets the same model in U2 (Rust `bps_bucket`) |
| Costs | hbt tables | own schedule | **one schedule generated from `lab/hbt/data`** (statutory + broker tariffs) | `app/lab/market/costs.schedule_from_tables` |
| Intraday bar availability | planner finality (60 s) | 0 s in research | **planner finality** (per broker) | `app.core.availability`, `BacktestService.finality_s` |

Consequences: intraday results of flagfix2 runs end 5 minutes earlier and may size smaller (5 % cap); flagfix1 intraday
runs with `participation_cap="auto"` size at most 5 % of a bar's volume. Stored runs keep their recorded settings.
