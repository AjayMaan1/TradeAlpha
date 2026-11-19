# Historify Lab, Phase 1.3: Strategy IR v1, evaluator and indicators (design)

| | |
|---|---|
| Status | **Implemented** (patch `changes/phase-1.3-strategy-ir.patch`). Decisions OD-1…OD-7 taken as recommended in §18. Deviations from this text are listed in §20. |
| Phase | "Phase 3" in the owner's numbering = roadmap **Phase 1.3** (weeks 5–9 of the phase-1 plan, design §24.3 "M2-lite") |
| Builds on | [HISTORIFY_BACKTEST_ENGINE_DESIGN.md](HISTORIFY_BACKTEST_ENGINE_DESIGN.md) v1.2: D-02 (IR-first), D-10 (indicators), §8.4–8.5, §9, Appendix C. Phase 1.2 Rust core. [AUDIT_2_REPORT.md](AUDIT_2_REPORT.md). |
| Evidence | Prototype `audit2/p13_evaluator_proto` (Rust, streaming DAG evaluator) and `audit2/p13_talib_conventions.py` (streaming kernels vs TA-Lib). Core defects in `audit2/e_core_evidence.json`. |
| Entry criterion | **Fix pack 1.2.1** (§2) merged first: the four High findings of audit 2 sit exactly under the IR (daily bars, limit entries, partial fills, corporate actions) |

---

## 1. Summary

Phase 1.2 delivered the execution core. Strategies reach it as four boolean arrays plus a constant OrderPlan. Those
arrays come from user pandas code, which the system cannot check for look-ahead, cannot hash meaningfully, cannot
share across a parameter sweep, and cannot run live.

Phase 1.3 adds the **strategy layer**:

1. **Strategy IR v1.** A JSON document (parameters, signals, rules, risk, universe) with a schema, a Python builder
   and canonical hashing.
2. **Compiler.** Schema check, type check, **causality check**, declared lookback, constant folding, common-subexpression
   elimination (CSE), and a plan that marks which nodes depend on position state.
3. **One evaluator for batch and streaming.** Every operator is a *streaming kernel* (`step(t)`, O(1) amortised).
   Batch is the same kernel stepped over the array, so **batch == streaming holds by construction**, not by testing
   two implementations against each other. This is the property the paper runtime (1.5) and the decision journal
   (D-25) depend on.
4. **Fused bar loop.** The engine calls the evaluator inside its own bar loop (P0–P2 → **E: evaluate** → P3–P4).
   Position-state operators (`bars_in_trade`, `highest_since_entry`, …) therefore see the true position. Rules
   emit **intents** carrying per-order levels (ATR stops, risk sizing).
5. **~27 indicators + 10 session functions**, with TA-Lib definitions and warm-up. Prototype parity vs TA-Lib:
   NaN pattern identical, max relative error ≤ 4.8e-15 (§10).
6. **Multi-timeframe** only through `available_at`. The rule is tightened in §6.5: a higher-timeframe bar is visible
   when it is available no later than the current bar.
7. **Python tier** (`hbt.Strategy`, `on_bar` / `on_fill`) on the same loop, with a causal `ctx`.
8. **Look-ahead fuzz harness**, a mandatory CI job. It perturbs the future and requires every node and intent up to
   *t* to stay bit-identical.

Measured feasibility (prototype, one core):
- an interpreted streaming DAG runs at **213–284 M node-updates/s**;
- an EMA-cross program on one year of 5m bars (39,150 bars) evaluates in **0.74–0.92 ms**, against a 2.19 ms engine run;
- sharing EMA nodes across a 256-variant sweep cuts signal time **×4.4** (248 ms → 56 ms).

---

## 2. Entry criterion: fix pack 1.2.1

The IR compiles into the core, so defects in the core become defects of every IR strategy. These audit-2 items are
fixed first. Each ships with a golden regression test named after its finding.

| # | Finding | Change in the core / lab | Est |
|---|---|---|---|
| F1 | A2-H1 daily defaults reject every entry | Session rules (`no_new_entries_after`, `squareoff_at`) apply only when `bar_secs < 86,400`. `exec_spec()` picks the product from the bar size (CNC for ≥ 1 session) unless given. MIS on daily bars is an error. Warn when ≥ 50 % of signals are rejected. | 0.5 |
| F2 | A2-H2 daily limit/stop never fill | `PendingEntry.day` = session in which the order becomes **live**. DAY orders are cancelled at the end of *that* session. `entry_expiry_bars` counts live bars. | 0.5 |
| F3 | A2-H3 carried remainder re-enters after exit | An exit or reversal decision cancels any carried entry remainder (counter `remainder_cancelled`). A reversal queues its own entry. New property invariant: *no entry fill without an entry decision since the last exit*. | 1 |
| F4 | A2-H4 corporate actions | Core: CA events applied to positions (§9.3). Lab: `Dataset` carries raw i64 bars + an adjustment-factor column (§9.1). Adjusted prices never enter the integer path. | 3 |
| F5 | A2-M1 zero-volume fills | `volume == 0 && !zero_volume_fills` refuses the fill whatever the cap | 0.25 |
| F6 | A2-M2 forced partial exit | Forced close keeps its carried remainder (same rule as square-off) | 0.25 |
| F7 | A2-M3 inputs | Refuse non-finite prices and NULL volume (both paths). `ts` must be int epoch seconds in [2000-01-01, 2100-01-01]. Signals: bool, or numeric without NaN. | 0.5 |
| F8 | A2-M4 ranges | `ExecSpec::validate` / `OrderPlan::validate` cover every field (pct ∈ (0, 1), HH:MM ∈ session, slippage ≤ 5 %, …). Mirrored in Python for early errors. | 0.5 |
| F9 | A2-M5 manifest identity | `dataset_sha256` (core arrays + CA events + session table) and `decisions_sha256` (signals or intents) | 0.5 |
| F10 | A2-M6 W/M annualisation | `periods_per_year` from the interval code and the calendar (W = sessions/5, M = 12) | 0.25 |
| F11 | A2-M7 cost defaults | `backtest()` requires a broker profile, or an explicit `brokerage="none"` that is recorded as a caveat. DP charge component (per scrip, per sell day, + GST) added to the cost model. | 1 |
| F12 | A2-L1, L7, L8 | `exec_spec` product fix; bracket liveness per coarse bar; `profit_factor` → `null` + flag | 0.5 |
| | | **Core fix pack** | **8.75 d** |

App-side items (A2-M8 restore rollback, A2-M9 feed retry, the Lows) run in parallel in the app track. They do not block 1.3.

**ExecSpec versioning.** No run has been persisted yet: the runs table arrives with 1.4. F1–F12 are therefore recorded
as **v1 errata** (design Appendix B) instead of a version bump. From 1.4 on, every semantic change bumps
`exec_spec_version`.

---

## 3. Goals and non-goals

**Goals**
- G1. A strategy is *data*: validated, typed, hashed and diffable. It can be authored from Python, JSON or (1.4) the UI.
- G2. Look-ahead is impossible by construction in the IR, and detected (fuzz) in the Python tier.
- G3. One implementation of every operator serves batch, sweep and live streaming, bit-identically.
- G4. Indian-market session semantics are correct: session-anchored VWAP, opening range, prior-day levels, special
  and partial sessions from the calendar.
- G5. Research speed: IR evaluation ≤ 50 % of engine time for typical programs, and shared subexpressions across
  sweep variants.
- G6. Every run is reproducible from its manifest: IR hash + params + dataset hash + ExecSpec + costs + engine version.

**Non-goals for 1.3** (the phase that takes each one over is in brackets)
- Portfolio / multi-instrument allocation, rebalancing, `target_weight` intents [1.6].
- Genetic programming over the IR, optuna integration [1.6].
- The numba `@cfunc` tier [later; the IR + Python tier cover phase-1 needs].
- UI form builder [1.4].
- TradingView indicator flavour [1.6, unless OD-2 says otherwise].
- F&O specifics (expiry, rollover, Greeks) [Phase 4].

---

## 4. Requirements

### 4.1 Functional

| ID | Requirement |
|---|---|
| FR-1 | IR v1 documents (§6) are validated against a published JSON Schema. Errors carry a JSON Pointer and a stable code (§7.3). |
| FR-2 | The type checker distinguishes `Series<num>`, `Series<bool>`, `Scalar<num/int/bool/time>`, timeframe and output selectors (multi-output indicators). |
| FR-3 | The causality checker rejects negative shifts, future-referencing operators, and any other-timeframe access that does not go through `available_at`. |
| FR-4 | Lookback is computed per node and per program, and used for warm-up. Decisions during warm-up are suppressed and counted (`warmup_suppressed`). |
| FR-5 | Rules map to intents: `enter_long`, `enter_short`, `exit_long`, `exit_short`, `exit_all`. Entries carry order type (market/limit/stop), a price expression, a bracket (stop/target as pct or price expression, trail pct, time stop) and sizing (pct_equity, fixed_qty, fixed_value, **risk_pct**). |
| FR-6 | Parameters are typed with a range, step, default and optional prior. A sweep grid is declared in the IR or passed at run time. |
| FR-7 | ~27 indicators and 10 session functions (§10), plus the arithmetic, logic, comparison, series and event operators of Appendix C. |
| FR-8 | Multi-timeframe references `{"col": …, "tf": "15m"|"1h"|"D"|"W"|"M"}` resolve only through `available_at` (§6.5). |
| FR-9 | Read-only position-state operators: `position_qty`, `entry_price`, `bars_in_trade`, `unrealised_pct`, `highest_since_entry`, `lowest_since_entry`. |
| FR-10 | `hbt.compile(ir) → Program` offers `.explain()` (pseudo-code), `.lookback`, `.params`, `.sha256` and `.nodes` (with shared/position-dependent flags). |
| FR-11 | `hbt.run(program, dataset, params, spec, costs)` and `hbt.sweep(program, dataset, grid, …)`. Both return the 1.2 `Result` objects and metrics tables. |
| FR-12 | Python tier: `hbt.Strategy` with `init(ctx)`, `on_bar(ctx)`, `on_fill(ctx, fill)`. The causal `ctx` refuses future indices. |
| FR-13 | Debug: `Program.trace(dataset, bars=…)` returns every node's value for the chosen bars (what the UI shows in 1.4). |
| FR-14 | CLI: `flask historify lab ir validate|explain|hash <file>` and `lab run <ir> --snapshot … --symbol …`. |

### 4.2 Non-functional (targets, with the evidence behind them)

| ID | Target | Basis |
|---|---|---|
| NFR-1 | Evaluator ≥ **150 M node-updates/s** per core | prototype: 213–284 M/s |
| NFR-2 | EMA-cross IR, one year of 5m bars: signals ≤ 1 ms; end-to-end run ≤ 4 ms | prototype 0.74–0.92 ms + engine 2.19 ms |
| NFR-3 | 40-node program over 39k bars ≤ 10 ms | prototype 5.7–7.8 ms |
| NFR-4 | Sweep: market-only nodes shared across variants; 256 EMA variants: signals ≤ 80 ms | prototype 56 ms (×4.4 vs unshared) |
| NFR-5 | Compile ≤ 2 ms for 1k nodes, ≤ 20 ms for 10k nodes (the maximum) | linear passes; measured in CI |
| NFR-6 | Streaming step: 100 symbols × 40 nodes ≤ 100 µs per bar | 4,000 updates ≈ 20 µs at NFR-1 |
| NFR-7 | Batch == streaming: bit-identical, all operators | by construction + test |
| NFR-8 | TA-Lib parity: NaN pattern identical; relative error ≤ 1e-9 after warm-up (≤ 1e-12 typical) | prototype ≤ 4.8e-15 |
| NFR-9 | Look-ahead fuzz: 0 violations on every operator, 5,000 generated programs and every saved strategy | CI job |
| NFR-10 | Deterministic across Linux / Windows: identical intents and fills; metrics ≤ 1e-12 | CI matrix compares hashes |

---

## 5. Architecture

```
            authoring                         compile (Rust, hbt-ir)                       run (Rust, hbt-core)
 ┌───────────────────────┐      ┌──────────────────────────────────────────┐      ┌───────────────────────────────┐
 │ Python builder (hbt.ir)│─┐    │ 1 schema  2 resolve  3 types  4 causality │      │ bar loop per instrument       │
 │ JSON file / API        │─┼──▶ │ 5 params bind  6 fold  7 CSE  8 lookback  │──▶   │  P0 session  P1 open  P2 intra│
 │ (1.4) UI form builder  │─┘    │ 9 partition: market-only | position-dep.  │ Plan │  E  evaluate (streaming step) │
 └───────────────────────┘      │ 10 slot layout + kernel states            │      │  R  rules → intents           │
                                └──────────────────────────────────────────┘      │  P3 close: intents → orders   │
 ┌───────────────────────┐                                                         │  P4 mark-to-market            │
 │ Dataset (hbt.data)     │  raw i64 exec bars + f64 signal columns (adjusted)     └──────────────┬────────────────┘
 │ snapshot / replica /   │  + session table + HTF frames with available_at               fills, trades, equity,
 │ store / frame          │  + CA events                                                   intents, node trace
 └───────────────────────┘
```

**Crates and modules** (extends 1.2's two crates; split by the boundary in design Appendix F):

| Crate / module | Content |
|---|---|
| `hbt-ir` (new crate) | serde types of IR v1, JSON Schema export, resolver, type checker, causality checker, constant folding, CSE, lookback, partitioner, `Plan`. No market data. |
| `hbt-core::kernels` (new module; crate `hbt-ind` later) | Streaming kernels: indicators, series ops, session functions. Each is `struct State` + `fn step(&mut self, inputs) -> f64`. |
| `hbt-core::eval` | `Evaluator` = Plan + kernel states + slot values. `step(t, &BarCtx, &PosState)`. Batch driver for market-only nodes. |
| `hbt-core::engine` (1.2, extended) | `DecisionSource` trait (below). Intents with per-order levels. CA position events (F4). |
| `hbt-py` | `compile`, `run_ir`, `sweep_ir`, `trace`; the Python-tier callback adapter |
| `lab/hbt/ir.py` | Python builder DSL, JSON (de)serialisation, canonical hash (mirrors Rust) |
| `lab/hbt/strategy.py` | `Strategy` base class, causal `ctx`, `CausalSeries` |
| `lab/hbt/dataset.py` | `Dataset`: raw exec bars, signal columns, session table, HTF frames, CA events, provenance + hash |

**`DecisionSource` trait.** The engine becomes agnostic of where decisions come from:

```rust
pub trait DecisionSource {
    /// called once per coarse bar after P2 (intrabar) and before P3 (close); returns this bar's intents
    fn decide(&mut self, t: usize, bar: &BarCtx, pos: &PosState, out: &mut Vec<Intent>) -> Result<(), String>;
    fn on_fill(&mut self, _fill: &Fill) {}
}
// ArraySignals   - the 1.2 API (four u8 arrays + OrderPlan) mapped to intents; unchanged behaviour
// IrProgram      - compiled Plan + Evaluator
// PyStrategy     - Python tier (holds the GIL only inside decide/on_fill)
```

`Intent { kind: EnterLong|EnterShort|ExitLong|ExitShort|ExitAll, order: Market|Limit{px}|Stop{px}, bracket: {stop,
target: Level, trail_pct, time_stop_bars}, size: Sizing, tag: u16 }`, with `Level = None | Pct(f64) | Price(i64 raw
units)`. The OrderPlan of 1.2 becomes the *default* for fields an intent leaves empty, so the array API is unchanged.

---

## 6. IR v1 specification

### 6.1 Document

```json
{
  "ir_version": 1,
  "name": "EMA cross, ATR bracket, MIS",
  "description": "optional",
  "params": {
    "fast": {"type": "int", "default": 9, "min": 3, "max": 50, "step": 1},
    "slow": {"type": "int", "default": 21, "min": 10, "max": 200, "step": 1},
    "k":    {"type": "float", "default": 2.0, "min": 0.5, "max": 5.0, "step": 0.25}
  },
  "constraints": [{"fn": "lt", "args": [{"param": "fast"}, {"param": "slow"}]}],
  "universe": {"interval": "5m", "watchlist": "NIFTY-100-EQ"},
  "data": {"adjustment": "adjusted"},
  "signals": {
    "f":   {"fn": "ema", "args": [{"col": "close"}, {"param": "fast"}]},
    "s":   {"fn": "ema", "args": [{"col": "close"}, {"param": "slow"}]},
    "atr": {"fn": "atr", "args": [14]},
    "up_d": {"fn": "gt", "args": [{"col": "close", "tf": "D"}, {"fn": "sma", "args": [{"col": "close", "tf": "D"}, 50]}]}
  },
  "rules": [
    {"id": "x", "when": {"fn": "cross_below", "args": [{"sig": "f"}, {"sig": "s"}]}, "do": {"exit_long": {}}},
    {"id": "e", "when": {"fn": "and", "args": [{"fn": "cross_above", "args": [{"sig": "f"}, {"sig": "s"}]},
                                              {"sig": "up_d"}]},
     "do": {"enter_long": {
       "order": {"type": "market"},
       "size": {"risk_pct": 0.005},
       "bracket": {"stop": {"price": {"fn": "sub", "args": [{"col": "close"}, {"fn": "mul", "args": [{"param": "k"}, {"sig": "atr"}]}]}},
                   "target": {"pct": 0.02}, "time_stop_bars": 30}}}}
  ],
  "product": "MIS",
  "meta": {"author": "…", "tags": ["trend"]}
}
```

- `signals` are named, reusable expressions (DAG nodes). `rules` are evaluated **in order** (§6.6).
- `universe` and `data` are *defaults*; a run may override them (recorded in the manifest).
- `constraints` prune invalid sweep combinations (e.g. fast < slow). They are evaluated on parameters only.

### 6.2 Types

| Type | Meaning |
|---|---|
| `Series<num>` | f64 per bar; NaN = "no value" (warm-up, missing HTF bar) |
| `Series<bool>` | per bar; **never NaN**. A comparison involving NaN is false (the warm-up suppression in §6.4 makes this safe). |
| `Scalar<int|float|bool>` | literals and params; a scalar broadcasts to a series where a series is expected |
| `Scalar<time>` | `"HH:MM"` literal → minutes after midnight IST; only allowed in session comparisons |
| `Tf` | interval code ≥ the run interval (`5m < 15m < 1h < D < W < M`) |

Multi-output indicators (`macd`, `bbands`, `stoch`, `adx`, `donchian`, `keltner`) take `"out": "<name>"`, e.g.
`{"fn": "bbands", "args": [{"col":"close"}, 20, 2.0], "out": "upper"}`. Each output is its own node sharing one kernel
state (CSE).

### 6.3 Expression nodes

`{"fn": name, "args": [...], "out"?: name}` · `{"col": "open|high|low|close|volume|oi|typical|hl2", "tf"?: code}` ·
`{"sig": name}` · `{"param": name}` · literals (number, bool, `"HH:MM"`).

### 6.4 Semantics

- **Evaluation instant.** The evaluator runs once per bar, at the bar's close, after intrabar execution (phase E,
  §8.3). Every series value at index *t* is computed from data with index ≤ *t* only.
- **NaN and warm-up.** Arithmetic propagates NaN; comparisons with NaN are false. Each node has a lookback *L(node)*
  (§7.5). Rules are not evaluated before `t ≥ L(program)` (per session for session-anchored nodes): no decision is
  produced, and `warmup_suppressed` counts the bars. This avoids the classic "cross_above fires on the first valid
  value" artefact.
- **Events.** `cross_above(a, b)` at *t* = `a[t] > b[t] && a[t-1] <= b[t-1]`, with both values defined at *t-1* and
  *t*. `rising(a, n)` / `falling(a, n)` compare against `a[t-n]`.
- **Sessions.** Session functions use the **session table** from the calendar (open and close per day, including
  special and partial sessions, Muhurat). `is_last_bar` is true on the last bar that *starts* before the session close.
  `session_high` and `session_low` are "so far" (they include *t*). `opening_range_high(m)` is NaN until the range
  window has closed.
- **Numeric policy.** f64 throughout; no fused multiply-add (Rust never contracts `a*b+c` implicitly, and kernels must
  not call `mul_add`, which a clippy `disallowed_methods` rule enforces); a fixed evaluation order. Rolling sums are re-summed from the ring buffer
  every 1,024 steps, bounding drift on 10-year 1m series. The TA-Lib tolerance in NFR-8 absorbs the difference.

### 6.5 Multi-timeframe (tightens design §8.5)

- **Decision time** of bar *t* = `available_at_own(t)`: the moment the bar is final in Historify.
  - Intraday bars: `min(ts + interval, session close) + intraday buffer`.
  - Daily bars: session close + daily buffer.
- **Visibility.** A higher-timeframe bar *h* is visible at *t* iff `available_at(h) ≤ decision_time(t)`. Consequences:
  - A 15m bar that ends with the current 5m bar is visible, because both carry the same buffer.
  - The daily bar of the current session becomes visible on the **next** session.
  - This is what the paper runtime can actually know.
- Design §8.5 compares against the bar *open*, which is stricter than needed. The rule above is causal and matches live
  behaviour, so it replaces §8.5.
- **Implementation.** The evaluator advances an `asof` pointer per HTF frame (monotonic, O(1) amortised). HTF series
  are precomputed with the **same streaming kernels** on the HTF bars. Their values are exposed through the pointer,
  so no HTF indicator is recomputed per base bar.
- A `tf` below the run interval is a compile error (E-TF-LOWER). Intraday strategies use 1m bars only via the engine's
  magnifier, never as signals.

### 6.6 Rules → intents → orders

1. Rules are evaluated in document order, **exits before entries**. All exit rules whose `when` is true emit their
   intents. Among the entry rules, the **first** whose `when` is true wins. Later matching entry rules increment
   `entry_conflicts`. This replaces the implicit "long wins" of the array API, which keeps its behaviour but also
   reports `entry_conflicts`.
2. Intents at bar *t* become orders under the ExecSpec exactly as the 1.2 signals do:
   - fills at the next open, or at close + `decision_latency_s` on the 1m timeline;
   - cutoff and square-off;
   - participation cap;
   - fix-pack rules F1–F3.
3. **Price expressions** (limit/stop entry prices, absolute stop/target levels) are evaluated at *t* on the
   **signal scale** (adjusted prices when `adjustment = adjusted`). They are converted to raw price units with the
   adjustment factor of the *fill* bar, then rounded to the tick in the adverse direction (§9.2).
4. **Risk sizing** `risk_pct = r`:
   - `qty = floor_lot(equity × r / |entry_ref − stop_level|)`, where `entry_ref` = the next bar's open at fill time;
   - the result is capped by the all-in budget and the participation cap;
   - if there is no stop level, the compiler rejects the program (E-RISK-NO-STOP).
5. Position-state operators read the state *after* P2 of bar *t*, i.e. including stops hit inside bar *t*.

### 6.7 Parameters and sweeps

- Types: `int`, `float`, `bool`, `choice` (enumerated literals).
- `step` defines the grid; `prior` (uniform / log-uniform) is reserved for optuna in 1.6.
- Sweep grid = the Cartesian product of the listed params, filtered by `constraints`, with a hard cap of 100,000
  variants (E-GRID-SIZE).
- Binding substitutes values as literals and then runs constant folding, so `ema(close, 9)` hashes identically whether
  written literally or bound from a param. This is what makes cross-variant CSE work (§7.6).

### 6.8 Limits (enforced by the compiler)

| Limit | Value |
|---|---|
| Nodes after expansion | 10,000 |
| Expression depth | 64 |
| Lookback of any node | 100,000 bars |
| Rules | 64 |
| Document size | 1 MB |
| String lengths | names ≤ 64 chars; descriptions ≤ 4 KB |

### 6.9 Canonical form and hashing

- Canonical JSON: UTF-8, keys sorted, no insignificant whitespace, numbers in shortest round-trip form (`ryu`),
  `-0.0` → `0`, and `meta` / `description` excluded.
- `ir_sha256` = SHA-256 of the canonical form. Rust and Python implement the same canonicaliser, and a shared
  test-vector file keeps them equal.
- A **strategy version** is immutable and identified by `ir_sha256`; registry tables arrive in 1.4.

### 6.10 Versioning

`ir_version` bumps on breaking grammar changes. The compiler migrates older versions automatically and records the
migration in the manifest. Additive operators do not bump the version; they bump the operator catalogue version
`ops_version`, which is part of the manifest.

---

## 7. Compiler (`hbt-ir`)

### 7.1 Pipeline

| # | Pass | Output / checks |
|---|---|---|
| 1 | Schema | serde + JSON Schema (limits of §6.8) |
| 2 | Resolve | `sig` references (a cycle is E-CYCLE), params, operator names and arity (E-UNKNOWN-FN, E-ARITY) |
| 3 | Types | §6.2 inference. Bool/num misuse is E-TYPE. `HH:MM` only in session comparisons. |
| 4 | Causality | §7.4 |
| 5 | Bind | param values → literals (per variant); constraints evaluated |
| 6 | Fold | constant folding; algebraic identities only where exact in IEEE (no reassociation) |
| 7 | CSE | structural hash of each node (canonical subtree); identical nodes merged |
| 8 | Lookback | §7.5 |
| 9 | Partition | node classes: `market` (depends on bars only), `position` (depends on position state), `rule` |
| 10 | Layout | topological slot order; kernel state sizes; ring-buffer lengths = max shift/window referenced |

### 7.2 Outputs

`Plan { nodes: Vec<NodeSpec>, slots, market_prefix_len, rules: Vec<RuleSpec>, lookback, htf: Vec<TfSpec>, ir_sha256,
plan_sha256 }`. `explain()` renders pseudo-code: for example `f = EMA(close, 9)`, `when cross_above(f, s) and
up_d → enter long market, size 0.5 % risk, stop close − 2.0 × ATR(14)`.

### 7.3 Error model

Errors are structured as `{code, message, pointer: "/rules/1/when/args/0", hint}`.

| Code | Meaning |
|---|---|
| E-SCHEMA | document does not match the schema |
| E-CYCLE | `sig` references form a cycle |
| E-UNKNOWN-FN | operator name not in the catalogue |
| E-ARITY | wrong number of arguments |
| E-TYPE | bool / num / time used in the wrong place |
| E-CAUSAL | future reference (negative shift, lead, centred window) |
| E-TF-LOWER | `tf` below the run interval |
| E-TF-UNKNOWN | interval code not recognised |
| E-LIMIT | a §6.8 limit exceeded |
| E-PARAM-RANGE | param value outside min/max |
| E-GRID-SIZE | sweep grid above 100,000 variants |
| E-RISK-NO-STOP | `risk_pct` sizing without a stop level |
| E-INTENT | malformed intent |
| E-PRODUCT | product / interval combination not allowed (e.g. MIS on daily bars) |
| E-LOOKBACK-DATA | the dataset is shorter than the declared lookback (a run-time error, not a compile error) |

### 7.4 Causality rules

1. `shift(x, n)` requires a literal or param `n ≥ 1` (E-CAUSAL). There is no `lead`, no centred window and no
   whole-series statistic (`mean(x)` over the full history does not exist; only `rolling_*` and `expanding_*`).
2. `tf` access only through §6.5. A `col` with a `tf` is a distinct node family whose kernels run on HTF bars.
3. Session functions are "so far" by definition. `session_close_price` does not exist; `prior_day_close` is the way
   to reach the previous session's close.
4. Position-state nodes may feed rules, but not market nodes: the partition must be acyclic. This is checked; a
   violation is E-CAUSAL with a hint.
5. Every operator in the catalogue carries a machine-checked flag `causal: true`. The fuzz harness (§12.2) verifies
   the flag, so a mis-implemented kernel is caught even though the compiler trusts the flag.

### 7.5 Lookback

- `L(col) = 0`
- `L(shift(x, n)) = L(x) + n`
- `L(rolling_*(x, w)) = L(x) + w − 1`
- `L(ema(x, n)) = L(x) + n − 1`. The TA-Lib seed = SMA of the first *n* values; no extra unstable period, which
  matches TA-Lib's default.
- `L(rsi(x, n)) = L(x) + n`; `L(atr(n)) = n`.
- Composites take the maximum of their inputs plus their own lookback.
- HTF nodes are measured in HTF bars and converted to base bars conservatively (× bars per HTF bar).
- The program lookback is the maximum over nodes that feed rules.
- The dataset loader fetches `lookback` extra bars **before** the requested range so the range starts warm. Those
  warm-up bars are marked and never traded.

### 7.6 CSE across sweep variants

After binding, each variant's nodes are hashed. The sweep driver builds **one** program holding the union of all
variants' market nodes (identical hashes merged) and evaluates it once per symbol. It then runs one engine per variant,
reading its own rule slots.

In the prototype, 256 EMA-cross variants need 32 distinct EMAs instead of 512, and signal time drops ×4.4. Position
and rule nodes are per variant.

---

## 8. Evaluator

### 8.1 Kernels

- Every operator is a struct with its state plus `step(&mut self, in: &[f64]) -> f64`, or an out-array for
  multi-output kernels.
- State layouts:
  - **ring buffers** for windows and shifts;
  - **monotonic deques** for rolling max/min, O(1) amortised;
  - **Welford** for rolling variance with periodic recompute;
  - a counter + sum for seeded EMAs.
- **Session-anchored** kernels reset on a session change, which `BarCtx.new_session` signals.
- The prototype interprets the plan as `Vec<Op>` with enum dispatch and reaches 213–284 M node-updates/s.
  Monomorphised or JIT variants are unnecessary at these targets.

### 8.2 Batch = streaming

- There is one `step`. "Batch" is a loop calling `step` for *t* = 0..n, storing the market-node outputs into arrays
  (needed for sweeps and traces). "Streaming" calls the same `step` when a new bar arrives in paper or live.
- NFR-7 is therefore a property of the design. The test (§12.1) guards against accidental divergence, such as a
  kernel that peeks at a batch array.

### 8.3 Fused loop, per coarse bar

```
P0 session boundary → P1 open events → P2 intrabar (1.2 engine, unchanged)
E  evaluator.step(t, bar_ctx, pos_state)       market nodes (or read precomputed), then position nodes
R  rules in order → intents (exits first, first matching entry wins)
P3 intents → pending orders (latency, cutoff, square-off, participation rules)
P4 mark-to-market
```

Market nodes can be precomputed in batch and merely read in the loop, which gives identical values (§8.2). Sweeps
use that. Position nodes always run in the loop.

### 8.4 Determinism

- No parallelism inside one instrument's series.
- Rayon parallelises across symbols and variants only; results are ordered by input index.
- No FMA contraction.
- Hash maps are never iterated when producing outputs.
- Golden hashes of intents and fills are compared across the Linux and Windows CI runners (NFR-10).

---

## 9. Data model (fixes A2-H4 and feeds the evaluator)

### 9.1 `Dataset`

```
Dataset
  exec:      raw bars, i64 price units (ts, o, h, l, c, v, day, minute)     ← the engine executes on these, always
  signal:    f64 columns (o, h, l, c, v, typical, hl2) on the chosen scale: raw | adjusted | total_return
  factor:    f64 per bar: signal_price = raw_price × factor (1.0 in raw mode)
  sessions:  per trading day: open_min, close_min, kind (normal | special | muhurat | partial), from the calendar
  htf:       {code: (signal f64 columns, available_at)} for every tf referenced by the program
  ca_events: [(day, kind, qty_num, qty_den, price_factor, cash_per_share_paise)]  ← for raw-execution position events
  provenance: snapshot id / manifest hash, calendar hash, adjustment, CA events applied, finality buffers
  sha256:    hash of exec arrays + factor + sessions + htf + ca_events  (fix A2-M5)
```

Built by `hbt.dataset(source, symbol, interval, start, end, adjustment, tfs)` from a snapshot, the replica, the live
store (read-only) or a frame. Adjusted W/M come from adjusted D (`adjusted_derived`, already in 1.1).

### 9.2 Levels across scales

- Percentage levels (`stop: {pct}`) are scale-free: they apply to the raw fill price.
- Absolute levels from expressions are on the signal scale: `raw = level / factor[fill_bar]`, then rounded to the tick
  adversely.
- If an ex-date falls between decision and fill (overnight), the fill bar's factor is the correct one, and the
  Dataset provides it.

### 9.3 Corporate-action position events (raw execution)

At P0 of an ex-date session, for an open position:

- **Split / bonus / consolidation.**
  - `qty' = floor_lot(qty × qty_num / qty_den)`.
  - The fractional entitlement is credited as cash at the ex-date open.
  - Stop, target and trail reference are multiplied by `price_factor` and re-rounded to the tick.
  - The trade's entry value is unchanged, so P&L is continuous.
- **Dividend** (optional, ExecSpec `dividends: credit | ignore`; default `credit` for CNC): `cash += qty ×
  cash_per_share` for longs; debit for shorts.
- **Rights / other** are not modelled. A position held across such an event raises the caveat
  `unmodelled_corporate_action`.
- Golden tests:
  - G-CA1 bonus 1:1 while long → equity continuous;
  - G-CA2 split 10→2 with a live stop → stop rescaled;
  - G-CA3 dividend credit;
  - G-CA4 fractional entitlement cash;
  - G-CA5 the adjusted-signal / raw-execution run equals a hand computation.

---

## 10. Indicators and session functions v1

The definitions are TA-Lib's where TA-Lib has the indicator (decision D-10). Parity in CI uses TA-Lib 0.8 in the test
environment only (BSD; not a runtime dependency).

| Family | Operators (outputs) | TA-Lib reference | Lookback |
|---|---|---|---|
| Moving averages | `sma`, `ema`, `wma`, `dema`, `tema`, `hma` | SMA, EMA, WMA, DEMA, TEMA; HMA = WMA(2·WMA(n/2) − WMA(n), √n), reference implementation | n−1 (DEMA 2n−2, TEMA 3n−3) |
| Momentum | `rsi`, `roc`, `mom`, `cci`, `willr`, `mfi`, `stoch` (k, d) | RSI, ROC, MOM, CCI, WILLR, MFI, STOCH | per TA-Lib |
| Trend | `macd` (macd, signal, hist), `adx` (adx, plus_di, minus_di), `linreg_slope` | MACD, ADX, PLUS_DI, MINUS_DI, LINEARREG_SLOPE | per TA-Lib |
| Volatility | `atr`, `natr`, `stddev`, `bbands` (upper, mid, lower), `keltner` (upper, mid, lower), `donchian` (upper, lower) | ATR, NATR, STDDEV (population), BBANDS, MAX/MIN; Keltner = EMA ± k·ATR | per TA-Lib |
| Volume | `obv`, `vwap` (session-anchored) | OBV; VWAP reference = Σ(typical·v)/Σv since session open | 0 |
| Statistics | `zscore`, `rolling_{mean,std,min,max,sum,quantile}`, `expanding_{max,min}` | composites; quantile = nearest-rank (documented) | w−1 |

**Session functions** (calendar-aware, "so far"):
- `minute_of_day`, `bar_in_session`, `is_first_bar`, `is_last_bar`;
- `session_open_price`, `session_high`, `session_low`;
- `opening_range_high(m)`, `opening_range_low(m)`;
- `prior_day_open|high|low|close` (through the D frame and `available_at`);
- `day_of_week`.

**Prototype verification** (`audit2/p13_talib_conventions.py`, 5,000 bars, n ∈ {9, 21, 50}): the streaming kernels
for EMA, SMA, RSI and ATR reproduce TA-Lib with an **identical NaN (warm-up) pattern** and max relative error
**≤ 4.8e-15**. The seeding conventions in §7.5 are therefore the right ones.

---

## 11. Python surfaces

### 11.1 Builder

```python
from hbt import ir, compile, dataset, run, sweep
from hbt.ir import col, param, sig, ema, atr, sma, cross_above, cross_below, rule, enter_long, exit_long, bracket

P = ir.params(fast=ir.Int(9, 3, 50), slow=ir.Int(21, 10, 200), k=ir.Float(2.0, 0.5, 5.0, step=0.25))
f, s = ema(col.close, P.fast), ema(col.close, P.slow)
strategy = ir.strategy(
    "EMA cross, ATR bracket", params=P, interval="5m", product="MIS", constraints=[P.fast < P.slow],
    rules=[rule(cross_below(f, s), exit_long()),
           rule(cross_above(f, s) & (col.close.tf("D") > sma(col.close.tf("D"), 50)),
                enter_long(size=ir.risk_pct(0.005),
                           bracket=bracket(stop=col.close - P.k * atr(14), target=ir.pct(0.02), time_stop_bars=30)))])
prog = compile(strategy)              # raises IRError with code + pointer
print(prog.explain(), prog.lookback, prog.sha256)
ds = dataset(snapshot="lab-weekly-2026-11-14", symbol="RELIANCE", exchange="NSE", interval="5m",
             start="2025-11-01", end="2026-10-31", adjustment="adjusted", tfs=["D"])
res = run(prog, ds, params={"fast": 9, "slow": 21, "k": 2.0}, costs=cost_model("EQ_INTRADAY", broker="zerodha"))
table = sweep(prog, ds, grid={"fast": range(5, 30, 2), "slow": range(20, 200, 10)}, costs=...)   # DataFrame of metrics
```

Operators overload `+ - * / < > <= >= & | ~`. `==` is deliberately *not* overloaded (use `ir.eq`) to avoid Python
truthiness surprises.

### 11.2 Python tier

```python
class MeanRevert(hbt.Strategy):
    lookback = hbt.param(int, 20, 5, 100)
    z_entry = hbt.param(float, 2.0, 1.0, 3.5)

    def init(self, ctx):
        self.z = ctx.indicator("zscore", ctx.close, self.lookback)    # Rust kernel → CausalSeries

    def on_bar(self, ctx):
        if not ctx.position and self.z[0] < -self.z_entry and ctx.minute_of_day <= ctx.time("14:55"):
            ctx.enter_long(size=hbt.pct_equity(1.0), bracket=hbt.bracket(stop=hbt.pct(0.006)))
        elif ctx.position and self.z[0] > 0:
            ctx.exit_all()
```

- **Indexing is relative.** `x[0]` is the current bar and `x[1]` the previous one. `x[-1]` raises `LookAheadError`,
  which makes the classic `close[i+1]` mistake impossible to write.
- `init` receives no raw arrays, only `ctx.indicator` (causal Rust kernels) and `ctx.frame(tf)` (via
  `available_at`).
- The escape hatch `ctx.unsafe_arrays()` marks the run `causality: unverified` and forces the fuzz check (§12.2)
  before the result can be saved.
- Cost: about 1.6 µs per bar of Python overhead (design [T2]). Python strategies run on the process pool, not in
  sweeps.

---

## 12. Verification

### 12.1 Test matrix

| Suite | Content | Gate |
|---|---|---|
| Kernel unit | every operator: hand cases, NaN/warm-up, session reset, multi-output | exact |
| TA-Lib parity | every indicator with a TA-Lib counterpart; 10 seeds × 3 lengths × 3 series shapes (trend, range, gaps) | NaN pattern equal; rel ≤ 1e-9 |
| Batch == streaming | every operator and 500 generated programs: full batch vs per-bar streaming (fresh state, bar-by-bar) | bit-identical |
| Compiler | schema / type / causality / limits: one failing fixture per error code with the expected pointer | exact |
| Canonical hash | shared test vectors: Rust == Python; key-order, whitespace and number-format invariance | exact |
| IR ≡ arrays | for the 1.2 golden scenarios: an IR program producing the same signals gives **identical fills** to the array API | exact |
| Golden IR strategies | EMA-cross MIS bracket, ORB 15m, RSI mean reversion, Donchian D CNC, VWAP reversion, prior-day breakout: hand-checked micro cases | exact |
| Property (hypothesis) | generated IR (depth ≤ 6) over generated markets: accounting identities (1.2), no entry without a decision (F3), determinism | 2 × 2,000 examples in the deep run |
| Look-ahead fuzz | §12.2 | 0 violations |
| Cross-platform | intents/fills hashes Linux == Windows | exact |
| Performance | NFR-1…6 | CI perf job (fails on > 20 % regression) |

### 12.2 Look-ahead fuzz harness

```
for program P, dataset X (n bars), cut points t_k (random + session boundaries + HTF boundaries):
    X' = X with every base bar > t_k replaced by a fresh seeded random walk (OHLC-valid, volumes random) and every HTF
         bar with available_at > decision_time(t_k) re-derived from X' (or independently perturbed)
    evaluate P on X and X' (streaming, node by node)
    require: all node values and all intents at bars ≤ t_k are bit-identical
    on failure: report the FIRST node (topological order) whose value differs → names the guilty operator
```

- **Runs on:**
  - every operator as a single-node program at parameter corners;
  - 5,000 hypothesis-generated programs;
  - every saved strategy version (1.4: on save);
  - Python-tier strategies on first run (mandatory with `unsafe_arrays`).
- **It catches:** negative shifts, centred windows, whole-series normalisation, HTF joins by `ts` instead of
  `available_at` (the 100 % leak measured in the engine review), and "session high of the whole day".
- **Self-test:** the harness must flag a planted look-ahead operator (`lead(x, 1)`, available only in the test
  build). This proves the harness itself works.

---

## 13. Performance plan

| Item | Target | Evidence / approach |
|---|---|---|
| Interpreter | ≥ 150 M node-updates/s | prototype 213–284 M/s (enum dispatch, slot array) |
| Single IR run (EMA cross, 39k bars) | ≤ 4 ms end-to-end | 0.74–0.92 ms + 2.19 ms engine |
| Sweep signal sharing | ×4 on EMA grids | prototype ×4.4 (512 → 32 EMA nodes) |
| Sweep end-to-end | ≥ 725/s (no regression vs 1.2); ≥ 3,000/s is the 1.6 target (fused metrics) | run_batch + shared market nodes |
| Streaming (paper) | 100 symbols × 40 nodes ≤ 100 µs per bar | ≈ 20 µs at 200 M/s |
| Memory | market-node arrays: n_bars × shared nodes × 8 B (39k × 289 ≈ 90 MB worst case for the 256-variant grid) → store only the nodes rules read; others stay in slots | layout pass marks "rule-visible" nodes |

---

## 14. Security and limits

- IR is data: there is no code execution, no file or network access, and the limits of §6.8 apply. The compiler and
  evaluator are panic-free on malformed input: a fuzz target (cargo-fuzz) runs on the JSON parser and compiler.
- The Python tier and the later numba tier are **trusted local code**, labelled as such in the manifest. They are
  never imported implicitly (design §23.1).
- Sweep grid caps (E-GRID-SIZE) and a CPU budget (1.4 dispatcher) prevent runaway jobs.

---

## 15. Reproducibility (manifest additions)

```json
"strategy": {"ir_sha256": "…", "ir_version": 1, "ops_version": 1, "params": {"fast": 9, "slow": 21, "k": 2.0},
             "plan_sha256": "…", "tier": "ir|python|arrays", "causality": "verified|unverified"},
"dataset":  {"sha256": "…", "source": "snapshot", "snapshot_id": "…", "adjustment": "adjusted",
             "ca_events_applied": 2, "calendar_hash": "…", "finality_buffers_s": {"intraday": 60, "daily": 900},
             "warmup_bars": 63},
"decisions": {"intents_sha256": "…", "warmup_suppressed": 63, "entry_conflicts": 0},
"engine":   {"hbt_core": "0.2.0", "exec_spec_version": 1, "exec_spec_errata": ["F1", "…", "F12"]}
```

Re-running with the same manifest must reproduce `intents_sha256` and the fills hash exactly.

---

## 16. Work breakdown

| ID | Task | Acceptance | Est (d) | Dep |
|---|---|---|---|---|
| T1.3.0 | Fix pack 1.2.1 (F1–F12, §2) | 12 regression goldens; audit-2 evidence script flips to "fixed" | 8.75 | – |
| T1.3.1 | IR v1 serde types + JSON Schema + validator + limits; canonical hash (Rust + Python vectors) | compiler fixtures per code; hash vectors | 2.5 | – |
| T1.3.2 | Resolver, type checker, causality checker, lookback, fold, CSE, partition, layout, `explain` | §12.1 compiler suite | 3 | 1 |
| T1.3.3 | Evaluator: kernels framework, slots, batch driver, streaming step, HTF as-of pointers, session table | batch == streaming on every operator | 3 | 2 |
| T1.3.4 | Indicators (~27) + session functions (10) | TA-Lib parity; session goldens incl. special/partial sessions | 4 | 3 |
| T1.3.5 | Engine: `DecisionSource`, intents with levels and risk sizing, fused loop, position-state ops, CA position events (with F4) | IR ≡ arrays; golden IR strategies; G-CA1–5 | 3 | 3, 0 |
| T1.3.6 | `Dataset` (raw + signal scale + factor + sessions + HTF + hash) from snapshot / replica / store / frame | dataset parity across sources; hash stability | 1.5 | 0 |
| T1.3.7 | Python builder DSL, `compile / run / sweep / trace`; sweep CSE across variants | examples run; ×4 sharing benchmark | 2 | 2, 5 |
| T1.3.8 | Python tier (`Strategy`, causal `ctx`, `CausalSeries`, `unsafe_arrays`) | `x[-1]` raises; tier parity with IR on the same rule | 2 | 5 |
| T1.3.9 | Look-ahead fuzz harness + planted-leak self-test; CI job (mandatory) | §12.2 | 1.5 | 3 |
| T1.3.10 | CLI (`lab ir validate/explain/hash`, `lab run`); docs; benchmarks + CI perf gate | NFR table green | 1 | all |
| | **Total** | | **32.25 d** (1.3 proper 23.5 d + fix pack 8.75 d) | |

Sequencing (one engineer): the fix pack in weeks 5–6, the IR / compiler in weeks 6–7, the evaluator and indicators
in weeks 7–9, and the engine / Python / fuzz work in weeks 9–10. That is about 1.5 weeks over the original window (the
fix pack was not planned). The paper start (T0 ≈ week 12) is kept if 1.4 integration starts on the compiled API in
week 9, in parallel. Otherwise T0 moves by one week.

---

## 17. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| The indicator warm-up convention differs from what users see on TradingView | high | TA-Lib default (D-10), shown in `explain()`; `tv` flavour in 1.6 (OD-2) |
| Position-state operators prevent sweep sharing | medium | Partition pass: only position and rule nodes are per variant; measured in the perf job |
| Float drift between platforms | low | no FMA, fixed order, resum policy; cross-platform hash gate |
| CA data incomplete → adjusted signals wrong | medium | CA-candidate audit (1.1) raises caveats; the run manifest lists events applied |
| The Python tier is abused for look-ahead | medium | no raw arrays in `init`; `unsafe_arrays` forces the fuzz check; the manifest flags `causality: unverified` |
| Scope creep (more indicators, GP) | medium | the catalogue is frozen for 1.3 at §10; additions bump `ops_version` in later phases |

---

## 18. Decisions requested from the owner

| # | Question | Recommendation |
|---|---|---|
| OD-1 | Default price scale for equity **signals** | `adjusted` (design §8.4); execution always on raw; `raw` selectable per run |
| OD-2 | Indicator flavour | TA-Lib definitions in 1.3 (D-10); TradingView flavour deferred to 1.6 |
| OD-3 | Position-state operators in IR v1 | Yes: trailing/ATR exits and "highest since entry" need them; the cost is contained by the partition |
| OD-4 | Entry conflict policy | IR: first matching rule wins (explicit order). The array API keeps "long wins" but reports `entry_conflicts`. |
| OD-5 | Python-tier causality enforcement | Strict (relative indexing, no raw arrays in `init`), with a flagged `unsafe_arrays` escape hatch |
| OD-6 | Fix pack before the IR | Yes: it blocks correct IR results on daily data, limit entries and CA |
| OD-7 | ExecSpec errata in place vs a version bump | Errata in v1 (no persisted runs yet); strict bumping from 1.4 |

---

## 19. Changes to the main design document (to apply on approval)

- **§8.5:** replace the "available_at ≤ ts[t] (open)" rule with §6.5 of this document.
- **§9.2 / Appendix C:** add intents with levels, `risk_pct`, rule ordering, `constraints`, `out` selectors and the
  error codes; update the operator list to §10.
- **Appendix B:** add ExecSpec v1 errata F1–F12 and the `dividends` field.
- **§17:** add the manifest fields of §15.
- **Appendix F:** add the `hbt-ir` crate, the `kernels` and `eval` modules, and `dataset.py` / `strategy.py` / `ir.py`.

## Appendix A: example strategies in IR (acceptance fixtures)

| Name | Interval | Exercises |
|---|---|---|
| `ema_cross_atr_bracket` | 5m MIS | EMA, ATR price-level stop, `risk_pct`, D trend filter via `available_at` |
| `orb_15` | 5m MIS | `opening_range_high/low(15)`, `is_last_bar`, time stop, square-off |
| `rsi_mean_revert` | 15m MIS | RSI, `bars_in_trade`, exit_all |
| `donchian_breakout_daily` | D CNC | Donchian, stop entry (F2), CA events across a bonus (F4), dividend credit |
| `vwap_reversion` | 5m MIS | session-anchored VWAP, `zscore`, limit entry |
| `prior_day_breakout` | 15m MIS | `prior_day_high/low`, special-session handling |

## Appendix B: evidence

| File | What it shows |
|---|---|
| `audit2/p13_evaluator_proto/` (+ `result.txt`) | Streaming DAG interpreter: 213–284 M node-updates/s; EMA-cross 39k bars 0.74–0.92 ms; 256-variant sharing ×4.4 |
| `audit2/p13_talib_conventions.py` (+ `.json`) | Streaming EMA/SMA/RSI/ATR == TA-Lib: NaN pattern identical, rel ≤ 4.8e-15 |
| `audit2/e_core_evidence.py` (+ `.json`) | The core defects the fix pack removes (X-01…X-13) |

## 20. Implementation notes (as built)

| Topic | As built |
|---|---|
| Decisions | OD-1 adjusted signals by default for equity / raw execution always; OD-2 TA-Lib definitions; OD-3 position operators in v1; OD-4 first matching entry wins (arrays keep "long wins" and count conflicts); OD-5 strict Python tier with `unsafe_arrays`; OD-6 fix pack first; OD-7 v1 errata (Appendix B of the main design) |
| Catalogue | 86 operators: 25 indicators (incl. multi-output BBANDS, MACD, STOCH, ADX / ±DI, Keltner, Donchian), 14 session functions, 6 position operators, 17 series and 24 arithmetic / comparison / logic operators. `prior_day_*` is computed from the run interval's own previous session (no D frame needed). `minute_of_day` is the bar's OPEN minute. |
| Sweeps | Beyond §7.6: a market node read by ONE variant (its cross of two shared EMAs) is private and stepped in that variant's loop; only nodes read by two or more variants, and every higher-frame node, are precomputed. 256 EMA variants: 33 shared nodes instead of 513 node evaluations per bar. |
| Numerics | Windowed sums use shifted data (re-centred every 256 steps): `stddev` went from 7e-9 to ≤ 1e-12 relative vs TA-Lib. |
| Look-ahead harness | Frames that are roll-ups of the base bars are RE-DERIVED from the perturbed bars; this is what catches a higher frame joined by `ts` (planted-leak self-test). |
| Costs | `cost_model()` refuses to guess the brokerage (broker tariffs bundled from `broker_tariffs.toml`, `verified: false`); DP charge per scrip per sell day. |
| Bindings | Datasets and bars are borrowed from numpy (no copy); `hbt_core.operators()` exposes the catalogue (the Python builder is tested against it). |
| Performance (39,150 5m bars, 4-core sandbox) | EMA-cross signals 2.8 ms; IR run incl. pandas results 9.0 ms; evaluator 112 M node-updates/s (target 150 M); sweep 603 runs/s incl. all signal evaluation (target ≥ 725: array `run_batch` 707/s excluding signal computation); streaming step 100 symbols x 32 nodes 35 µs (target ≤ 100 µs); compile 4,000 nodes 20 ms (target 10k ≤ 20 ms). Gaps carried into 1.6. |
