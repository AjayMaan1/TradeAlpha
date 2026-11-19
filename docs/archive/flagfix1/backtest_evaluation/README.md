# Backtest engine evaluation: harness, prototype, raw results

Evidence for [`../BACKTESTING_ENGINE_EVALUATION.md`](../BACKTESTING_ENGINE_EVALUATION.md) (engine review) and
[`../HISTORIFY_BACKTEST_ENGINE_DESIGN.md`](../HISTORIFY_BACKTEST_ENGINE_DESIGN.md) (build design). Nothing in here is wired
into the Historify app, and the app's code was not modified.

| Folder | Contents |
|---|---|
| `harness/` | One script per experiment (E1-E8): store builder, data-access timings, parity runs per engine, sweeps, cost-model probes |
| `prototype/hbt/` | A prototype of the recommended Historify-native backtest core: data adapters, time-versioned Indian costs, numba execution kernel, calendar-correct metrics and the deflated Sharpe ratio |
| `prototype/validate_parity.py` | Checks that the prototype reproduces the independent reference implementations exactly |
| `prototype/strategy_search_demo.py` | A 12,200-backtest grid, then walk-forward, then the deflated Sharpe ratio |
| `tech/` | Technology experiments T1–T10 for the design doc: Rust vs numba kernel, strategy tiers, Arrow data plane, indicators, IR serialisation, target scale, portfolio, 1m bar magnifier, zero-copy DuckDB → Rust |
| `tech/hbt_core_rs/`, `tech/hbt_arrow_probe_rs/` | Rust sources (PyO3 0.30, rayon, pyo3-arrow 0.20): the kernel port and the Arrow PyCapsule probe |
| `results/` | Raw JSON and logs behind every number in both reports |

## Reproduce (Linux, uv, Python 3.14 = Historify's recommended runtime)

```bash
# 1. Historify env + a realistic store (50 NSE equities + index; D 2015-2024, 1m 2023-2024; one unadjusted split)
uv venv ~/venvs/h314 --python 3.14 && VIRTUAL_ENV=~/venvs/h314 uv pip install -r historify-master/historify/requirements-dev.txt
~/venvs/h314/bin/python backtest_evaluation/harness/build_store.py /tmp/eval/inst          # ~4 min, 9.9 M bars
~/venvs/h314/bin/python backtest_evaluation/harness/e2_clean.py                            # creates snapshot 'eval-50'
# 2. one venv per engine, each = Historify's lock + the engine (install matrix, also records failures)
bash backtest_evaluation/harness/install_matrix.sh 3.14
# 3. parity + speed (scripts expect the harness files in /tmp/eval; copy them there first)
cp backtest_evaluation/harness/*.py /tmp/eval/
~/venvs/h314/bin/python /tmp/eval/run_reference.py
~/venvs/c_3.14_vectorbt/bin/python /tmp/eval/run_vectorbt.py     # likewise run_backtesting / run_backtrader / run_bt / run_nautilus
# 4. prototype
cd backtest_evaluation/prototype && ~/venvs/c_3.14_numba/bin/python validate_parity.py && ~/venvs/c_3.14_numba/bin/python strategy_search_demo.py
```

Timings come from a shared 4-core / 15 GB sandbox and vary by up to about 2x from run to run. Compare the orders of
magnitude, not the decimals. All prices are synthetic (GBM with regime drift), so nothing here says anything about
real strategy profitability.

### Technology experiments (T1–T10)

```bash
# Rust toolchain: rustup-init from static.rust-lang.org; then, in a venv with numpy + maturin:
cd backtest_evaluation/tech/hbt_core_rs && RUSTFLAGS="-C target-cpu=x86-64-v3" maturin build --release -i python
uv pip install target/wheels/hbt_core-*.whl            # 293 KB; add features=["abi3-py312"] for one wheel for 3.12-3.14
cd .. && python t1_rust_vs_numba.py && python t2_strategy_tiers.py && python t3_data_plane.py && python t4_indicators.py \
      && python t5_expr_serialization.py && python t6_scale.py && python t7_portfolio.py && python t9_bar_magnifier.py
cd hbt_arrow_probe_rs && maturin build --release -i python && uv pip install target/wheels/*.whl && cd .. && python t10_arrow_zero_copy.py
```
T3, T4, T7 and T9 read `/tmp/eval/t3/m1.arrow`, which `t3_data_plane.py` writes. Free-threaded CPython (3.14t) was
not tested, because the interpreter could not be downloaded in the sandbox.
