"""
hbt - Historify Lab, Python layer. The execution core is Rust (module ``hbt_core``, built from ``historify/rust``);
this package turns Historify data and configuration into core inputs and results into pandas.

    from hbt import backtest, run, sweep, Dataset, exec_spec, order_plan, cost_model     # runs
    from hbt import ir                                                                  # Strategy IR builder
    from hbt import Strategy, param                                                     # Python tier
"""
from hbt import ir
from hbt.costs import brokers, cost_model
from hbt.data import bars_from_frame, bars_from_snapshot, bars_from_store
from hbt.dataset import Dataset, core_events
from hbt.engine import PortfolioResult, Result, backtest, grid, periods_per_year, run, run_portfolio, signals, sweep
from hbt.spec import exec_spec, instrument, order_plan
from hbt.strategy import CausalSeries, LookAheadError, Strategy, param

__all__ = ["CausalSeries", "Dataset", "LookAheadError", "PortfolioResult", "Result", "Strategy", "backtest",
           "bars_from_frame", "bars_from_snapshot", "bars_from_store", "brokers", "core_events", "cost_model",
           "exec_spec", "grid", "instrument", "ir", "order_plan", "param", "periods_per_year", "run", "run_portfolio",
           "signals", "sweep"]
__version__ = "0.2.0"
