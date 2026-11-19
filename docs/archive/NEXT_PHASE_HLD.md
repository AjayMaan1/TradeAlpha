# Next phase: idea intake → evaluation → paper trading → trade data

> **Status: IMPLEMENTED.** Every task in §3 is built and tested (intake 2.1–2.2, validation 2.3, evaluation 2.4,
> TradeEvents 2.5, portfolio risk 3.3, order types 3.4, orchestrator + Ideas UI 3.5, outbox drills 3.6b).
> The generator-facing contract is `IDEAS.md`; what remains (Phase 4 live + hardening) is `FEATURE_AUDIT.md`.

The next phase covers **Phase 2** of `TRADING_ENGINE_5_PHASES.md` plus **the remainder of Phase 3**. The unification (U0–U5) and the gap closure (ADR-003) already delivered most of Phase 3.1 (the Execution Gateway core) and all of 3.6 (one orders table). What is left is the part that connects **your idea generation engine** to the platform, and the part that sends the **trade data** back.

![Next phase HLD](diagrams/next-phase/hld_next_phase.png)

Colours: green = built (unified platform) · blue = new in this phase · grey = Phase 4 (live) · white = external.

## 1. Flow
1. **Intake.** The idea engine posts an **IdeaSpec v1** to `POST /api/v2/ideas`. The spec holds:
   - the IR strategy, parameters, universe and interval;
   - the claimed metrics and the number of trials tested;
   - `source.data_end`;
   - a signature.

   It is authenticated with a scoped token (`ideas:write`) and rate-limited. It is idempotent: the same IR hash with the same parameters returns the existing idea version. It is stored as `ideas` / `idea_versions`, with lineage from parent to child.
2. **Validation job** (minutes):
   - JSON schema and canonical form;
   - IR compile (causal by construction, E-CAUSAL);
   - look-ahead fuzz;
   - only catalogued data columns (`app.lab.ir_extras`);
   - **embargo:** `source.data_end` must be before the hold-out cut-off, so the generator never saw the evaluation period.
3. **Evaluation job.** It runs on the **built** single backtest process: lab snapshot v2, hbt-core **portfolio mode** (shared cash, max positions), ADR-001/003 execution model, cost tables, PIT extras, one trial registry. It computes:
   - walk-forward OOS;
   - the sweep around the parameters;
   - **deflated Sharpe with N = `trials_tested` (declared by the generator) + the registry**;
   - PBO;
   - robustness: costs ×1.5, +1 bar latency, parameter neighbours;
   - capacity: participation at the intended capital;
   - claimed vs reproduced metrics.

   The output is an **evidence bundle**, hash-linked to the data, IR, ExecSpec and costs.
4. **Lifecycle orchestrator** (new):
   - It creates the draft deployment from a passed bundle (pinned IR version).
   - It promotes automatically when each gate's evidence is met: signals → shadow → paper.
   - It demotes automatically on a logic parity divergence, a daily-loss stop, drawdown or host errors.
   - Paper → eligible_live and live stay **human** approvals (drills + readiness sign-off + typed confirmation).
5. **Runtime.** The **built** hbt per-bar host runs every stage:
   - decisions are journaled with the as-seen bars;
   - live PIT extras are available;
   - nightly parity is reported in the P4 classes;
   - bars come from the one follow service.
6. **Portfolio risk v1** (new) sits between the host and the gateway. It provides capital slots across ideas, per-symbol / sector / gross caps and a global daily loss stop. The excess waits; nothing is silently resized.
7. **Execution Gateway** (built core):
   - journal-first OMS, with no blind resend;
   - one guardrail set;
   - one orders table;
   - a resting broker stop;
   - one kill switch.

   Its adapters are the Simulated broker (signals / shadow) and the OpenAlgo Analyzer (paper). New order types: stop-limit, IOC, cancel-replace.
8. **Trade data out** (new):
   - **TradeEvent v1** rows cover idea / version, decision / signal, order, fill, position, P&L, parity and lifecycle.
   - They are written to an **outbox in the same transaction** as the OMS or lifecycle row.
   - A relay delivers them by HMAC webhook with retries, a **pull API with a cursor**, and a nightly Parquet export.
   - Exactly-once happens on the consumer side: an event id plus cursor.
   - The analytics side of the idea engine consumes them and feeds the next ideas.

## 2. Contracts (v1)

| Contract | Key fields | Guarantees |
|---|---|---|
| **IdeaSpec v1** (in) | `idea_id`, `version`, `ir` (Strategy IR v1), `params`, `universe`, `interval`, `product`, `capital_hint`, `claimed {sharpe, cagr, max_dd, trades}`, `trials_tested`, `source {generator, run_id, data_end}`, `signature` | canonical JSON + signature; same IR hash + params = same version; schema-versioned |
| **EvidenceBundle v1** (internal, readable) | hashes (data manifest, IR, ExecSpec, cost schedule); WFO / DSR / PBO / robustness / capacity; claimed vs reproduced; gate report | immutable; referenced by every promotion |
| **TradeEvent v1** (out) | `event_id` (monotonic), `kind` (idea.*, signal, order.*, fill, position, pnl.daily, parity, lifecycle.*), `idea_id`, `version`, `deployment_id`, `stage`, `ts`, `payload`, `prev_hash` | outbox written in the OMS / lifecycle transaction; at-least-once delivery; consumer dedupes by `event_id`; pull with a cursor; hash chain (Phase 4.6) |

## 3. Tasks

| # | Task | Builds on (already done) | d |
|---|---|---|---|
| 2.1 | IdeaSpec v1 schema, canonical form, signature; `ideas` / `idea_versions` (lineage) | IR compile + hash | 2 |
| 2.2 | `POST /api/v2/ideas` + read API, scoped tokens (`ideas:write`, `trades:read`, `admin`), rate limits | auth (users, tokens, audit) | 2 |
| 2.3 | Validation job: schema, compile, look-ahead fuzz, extras catalogue, embargo | look-ahead harness, `ir_extras`, hold-out lock | 1.5 |
| 2.4 | Evaluation job → evidence bundle (DSR with external N, PBO, robustness, capacity, claimed vs reproduced) | ONE backtest process, portfolio mode, registry, evaluation library | 2.5 |
| 2.5 | TradeEvent v1 outbox + relay (webhook / pull cursor / Parquet) | one orders table, one delivery pipeline (HMAC webhook, retries) | 3 |
| 3.3 | Portfolio risk v1 across ideas (slots, symbol / sector / gross caps, global loss stop) | guardrails, portfolio sizing rule | 3 |
| 3.4 | Order types: stop-limit, IOC, cancel-replace (kernel + gateway + mock) | one OMS, one mock engine | 3 |
| 3.5 | Lifecycle orchestrator (auto promote / demote) + Ideas UI (inbox, lifecycle, evidence, paper vs backtest band) | deployments, gates, host evidence, parity view | 3 |
| 3.6b | Drills extended to the outbox (kill-9 between the OMS write and the relay) | kill-9 drills | 1 |
| | **Total** | | **≈ 21 d** (was ≈ 32 d before the unification) |

**Milestone (M2 + M3).**
- The idea engine posts an idea, gets an evidence bundle, and the idea runs in signals → shadow → Analyzer paper without a human touching it.
- Every decision, order, fill and lifecycle event comes back as a TradeEvent.
- The paper calendar starts: ≥ 20 sessions and ≥ 30 round trips per idea.

**After that: Phase 4 (live)** (grey in the diagram):
- `Engine::step_bar`, WebSocket bars as the decision source, a separate trader process;
- the approval workflow and the live adapter behind the P6b gates (global switch, auth, Analyzer OFF before every order, dry run), stepped caps;
- the hash-chained audit log.

## 4. Exit tests
- Intake:
  - a tampered signature returns 401;
  - a wrong token scope returns 403;
  - the same IR + params returns the same version (idempotent);
  - a peeking IR is rejected, naming the offending node;
  - `data_end` inside the hold-out is refused (embargo).
- Evaluation: the positive control is accepted and noise is rejected. Both use the external N; declaring 1,000 trials raises the DSR bar measurably.
- Orchestrator: a property test of the state machine (no skipped stage, no automatic live); an automatic demotion for each trigger.
- Portfolio risk: 30 correlated synthetic ideas → caps hold, the excess waits, and the global loss stop flattens.
- Trade data:
  - kill-9 at every outbox point → every event is delivered after restart;
  - the consumer sees each `event_id` once;
  - the cursor resumes exactly.
