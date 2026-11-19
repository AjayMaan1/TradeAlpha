# ADR-002 · One lifecycle, one order path, one set of operations settings

| | |
|---|---|
| Status | **Accepted** (owner's decisions 2 and 3 of `UNIFICATION_ANALYSIS.md`; implemented in unification U3-U5) |
| Context | After U2 there was one backtest process, but still two ways from a strategy to orders: flagfix1 lab paper sessions (IR, per-bar host, OMS, guardrails, no lifecycle) and flagfix2 deployments (Python, replay runtime, stages, promotion, delivery), with two guardrail sets, two Telegram configurations and three worker settings. |
| Enforced by | `tests/lab/test_unified_lifecycle.py`, `tests/lab/test_paper.py::test_d3_*`, `tests/integration/test_unification_safety.py`, `tests/integration/test_unified_ops.py`, `tests/repo/test_settings_audit.py`, `tests/ui/test_lab_pages.py::test_u4_*` |

## Decisions

### 1. The deployment is the only lifecycle; the runtime follows the strategy kind
| Strategy | `deployments.runtime` | Runtime |
|---|---|---|
| Python (flagfix2 class) | `replay` | P4 replay runtime (unchanged) |
| IR (`ir:<name>` or `ir:<name>@<v>`) | `bar_host` | hbt per-bar host: one lab paper session per running stage (`lab_paper_sessions.deployment_id / deployment_stage`, migration 0018) |

A deployment is **pinned** to the IR version it was created with (`code_sha` = IR hash; `BacktestService.load_strategy(name, code_sha)`); a newer version never changes it (it shows "a newer IR version exists").

Stage → route for hosted deployments:

| Stage | Session mode | Orders |
|---|---|---|
| draft | – | – |
| signals, shadow | `paper` (simulated broker, shadow quotes) | none at any broker |
| paper, eligible_live | `analyzer` (OpenAlgo Analyzer, G2) | Analyzer orders; protective stop rests at the broker |
| live | **refused** | only through the Execution Gateway's live route (trading-engine phase 4) |

- A promotion that changes the route **creates the next session first** (session guardrails validate it, e.g. G-IN-1 "every entry carries a stop"); a refusal fails the promotion and changes nothing. The previous session is stopped; the new stage starts **flat** (simulated positions are never moved to a broker).
- Pause / retire stop the session; resume restarts it (refused while the kill switch is on); a killed session or a nightly-parity divergence pauses the deployment (P4 rule: a logic divergence is a bug).
- The Lab paper page can stop a managed session (safety) but not start or delete it.

### 2. One evidence model for promotions
The thresholds (`signals.promotion`) are the same for both runtimes; hosted deployments read the evidence from the host journal: sessions = trading days with journaled decisions in the stage; logic divergences = nightly parity divergences (as-seen replay ≠ journal, or a final-data difference without a revised bar); gate pass rate = decisions without host error or stale-feed block; shadow returns = realised P&L per day of the stage's simulated book; slippage ratio = adverse Analyzer fill vs quote mid ÷ the ADR-001 slippage fraction; loss stops = G-IN-2 events.
**paper → eligible_live** additionally requires, for every runtime: the four operational drills passed in the last 30 days and a readiness sign-off on record (flagfix1 T0).

### 3. Decisions are signals
Every journaled decision of a hosted deployment that produced an order becomes a `Signal` of the deployment (fingerprint = the as-seen bars hash) and goes through the one delivery pipeline (SSE, HMAC webhook, Telegram). Exactly once per (deployment, instrument, decision time, action).

### 4. One order path (first cut)
- OMS semantics of flagfix1 (journal first, per-order tag, never a blind resend) – the flagfix2 path got the same rule in U0 (D1).
- On broker-filled routes, **one protective stop rests at the broker** at the engine's active stop (Rust `Outcome.active_stop`), replaced when it trails or the quantity changes; the engine's own stop exit is then never sent (no double exit). Trailing / time exits stay engine-driven.
- **One guardrail set**: the flagfix1 guardrails (notional, price collar, fat finger, order rate) also run on the signals order path (`signals.guardrails` overrides). Risk-reducing orders pass the sizing checks. The price collar blocks only the **aggressive** side when the side is known (a passive limit can never fill worse than its price).
- One kill switch (both paths) and one orders view: `/api/v2/trading/orders`, `/api/v2/trading/kill-switch` (one physical orders table since migration 0019, ADR-003).

### 5. One set of operations settings
- **One notification bus**: kill switch, paused deployments, killed sessions are notifications (the originating side notifies once); signal delivery to Telegram uses the app's one bot, chat and API base (`notify_telegram_bot_token`, `notify_telegram_chat_id`, `notify_telegram_api`).
- **One worker budget**: `lab_workers` (default cores − 1) and `lab_workers_during_downloads`, shared by the hbt Lab dispatcher and the P3 pool; `backtest.workers` is an alias that writes `lab_workers`.
- **One follow service**: each hosted deployment owns a managed FeedService subscription (`deployment <id>`) of its session's instruments and interval, active exactly while its session runs.
- Legacy keys (`signals_telegram_token`, `signals.telegram.chat_id/base_url`, `backtest.workers`, `compute_workers*`) are adopted once at start (`app/core/settings_registry.adopt_legacy`); `tests/repo/test_settings_audit.py` fails on any unregistered key, any concept with two keys, or a legacy key used outside the adoption code.

## Consequences
- An IR idea can go idea → backtest → freeze → signals → shadow → Analyzer paper with one set of gates, one journal and one delivery; live stays closed until the gateway's live route exists.
- Stage changes on hosted deployments start flat: schedule promotions outside market hours.
- Hosted deployments are limited to 50 instruments per session for now.

## Follow-up
The items listed here as "not yet" are closed by ADR-003: Rust portfolio mode, one physical orders table, one trading mock engine, WebSocket bars as an optional source of the one follow service, and the built-in strategies ported to IR. Python deployments keep the replay runtime by design until `step_bar` (Phase 4).
