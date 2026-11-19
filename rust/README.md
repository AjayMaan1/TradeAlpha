# Historify Lab — Rust core (`hbt-ir`, `hbt-core`, `hbt-py`)

Phases 1.2 (execution core) and 1.3 (Strategy IR v1) of the backtesting design (`PHASE_1_3_STRATEGY_IR_DESIGN.md`,
`HISTORIFY_BACKTEST_ENGINE_DESIGN.md`). AGPL-3.0-only like the rest of Historify.

| crate | what it is |
|---|---|
| `hbt-ir` | Strategy IR v1: parser / schema check (`ast`), operator catalogue (`ops`, 86 operators), compiler (`compile`: types, causality, params, folding, CSE across sweep variants, lookback, position partition -> `Plan`), canonical JSON + sha256 (`canon`), pseudo-code (`explain`), error codes (`error`). No market data. |
| `hbt-core` | fixed-point money, Indian costs (+ DP charge), ExecSpec / OrderPlan, `decision` (DecisionSource, intents), `engine` (P0–P4 bar loop, 1m segments, corporate-action position events), `kernels` (one streaming kernel per operator), `eval` (MarketEval with higher frames through `available_at`, IrSource, sweep layout), `metrics`. |
| `hbt-py` | PyO3 module `hbt_core`: `run` / `run_batch` (boolean arrays), `compile` -> `Program`, `run_ir`, `sweep_ir`, `eval_signals`, `stream_signals`, `run_py` (Python tier), `operators`, `bars_from_arrow`. Numpy inputs are borrowed, not copied; the GIL is released while the core runs (except the Python tier, which calls back per bar). |

The Python layer users call lives in `historify/lab/hbt` (`ir`, `Dataset`, `run`, `sweep`, `signals`, `Strategy`,
`fuzz`, `backtest`). Tests: `historify/tests/lab`, `hbt-ir/tests`, `hbt-core/tests`, unit + proptest modules.

## Build (developer machine)

```bash
# toolchain is pinned by rust-toolchain.toml (1.99.1); rustup installs it on first use
cd historify/rust
cargo test -p hbt-ir -p hbt-core --release        # Rust unit + property + integration tests
cargo clippy --workspace --all-targets --release -- -D warnings
cargo run --release -p hbt-core --example eval_bench   # evaluator / engine micro-benchmark
cd hbt-py && maturin develop --release            # installs hbt_core into the active virtualenv
cd ../.. && pip install TA-Lib==0.8.2 jsonschema    # test-only oracles
pytest tests/lab -m "perf or not perf"            # HBT_FUZZ_EXAMPLES=2000 for a deep look-ahead run
```

`maturin build --release -m rust/hbt-py/Cargo.toml` produces one **abi3** wheel (CPython ≥ 3.12) per OS/arch; CI
(`lab-core`) builds and tests it on Linux and Windows, and `lab-determinism` requires identical intent / fill / equity
hashes on both. Users never need a Rust toolchain: the app runs without `hbt_core`, and `tests/lab` is not collected
when it is missing.

## Invariants (do not break without a design decision)

* **Money is integer paise** (`i64`), prices integer price units (`Instrument.price_scale`, 100 = paise). Charges per
  fill, per charge, half-up to the paisa. No float touches cash.
* **Execution on raw prices, always.** Signals may read adjusted prices (`Dataset.sig`, f64); levels computed on that
  scale are converted back with the fill bar's factor. Corporate actions are applied to the position on the ex-date.
* **Conservative defaults (D-09 + v1 errata F1–F12)**: next-open fills, pessimistic intrabar order, bracket live on the
  entry bar, chronological same-bar priority, adverse tick rounding, gap-through stops at the open, zero-volume and
  band-locked bars do not fill; session rules on intraday bars only.
* **Causality by construction**: every operator is a streaming kernel (`step` consumes one bar); higher frames are read
  only through `available_at`; `shift` needs n ≥ 1. Batch and streaming are the same code. The look-ahead harness
  (`hbt.fuzz`) must stay green for every operator.
* **Every semantic choice is in ExecSpec / the IR** and hashed into the run manifest; a semantic change bumps
  `exec_spec_version` / `ir_version` (additive operators bump `ops_version`).
* **Determinism**: same inputs → byte-identical outputs (sweeps return results in input order whatever the rayon
  scheduling; no FMA; no parallel reductions inside one series).
* **PyO3 / rust-numpy / pyo3-arrow / arrow move in lockstep** (see `Cargo.toml`).
