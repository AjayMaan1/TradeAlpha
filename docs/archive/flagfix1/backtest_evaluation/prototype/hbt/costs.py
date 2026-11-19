"""
Time-versioned, side-aware Indian transaction costs -> per-bar rate vectors the numba kernel consumes.

Why a schedule: statutory rates change (e.g. STT on F&O and exchange transaction charges were revised on
1 Oct 2024). A 10-year backtest must charge each fill the rates in force on its trade date.

RATES BELOW ARE ILLUSTRATIVE PLACEHOLDERS - verify against NSE/SEBI circulars and your broker's tariff before use.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np


@dataclass(frozen=True)
class CostRates:
    effective_from: date
    brokerage_pct: float          # of traded value ...
    brokerage_cap: float          # ... capped at this many INR per order (discount-broker style)
    stt_buy: float                # securities transaction tax, of value
    stt_sell: float
    stamp_buy: float              # stamp duty (buyer only)
    exchange_txn: float           # exchange transaction charge, both sides
    sebi: float                   # SEBI turnover fee, both sides
    gst: float = 0.18             # on brokerage + exchange txn + SEBI fee


# segment -> ascending list of rate versions (placeholders; keep in the meta DB in production)
SCHEDULES: dict[str, list[CostRates]] = {
    "EQ_INTRADAY": [
        CostRates(date(2015, 1, 1), 0.0003, 20.0, 0.0, 0.00025, 0.00003, 0.0000325, 0.000001),
        CostRates(date(2024, 10, 1), 0.0003, 20.0, 0.0, 0.00025, 0.00003, 0.0000297, 0.000001),
    ],
    "EQ_DELIVERY": [
        CostRates(date(2015, 1, 1), 0.0, 0.0, 0.001, 0.001, 0.00015, 0.0000325, 0.000001),
        CostRates(date(2024, 10, 1), 0.0, 0.0, 0.001, 0.001, 0.00015, 0.0000297, 0.000001),
    ],
    "FUT": [
        CostRates(date(2015, 1, 1), 0.0003, 20.0, 0.0, 0.000125, 0.00002, 0.000019, 0.000001),
        CostRates(date(2024, 10, 1), 0.0003, 20.0, 0.0, 0.0002, 0.00002, 0.0000173, 0.000001),
    ],
    # symmetric flat fee, used only to prove parity with other engines
    "FLAT_0.03PCT": [CostRates(date(1990, 1, 1), 0.0, 0.0, 0.0003, 0.0003, 0.0, 0.0, 0.0, 0.0)],
    "FLAT_0.1PCT": [CostRates(date(1990, 1, 1), 0.0, 0.0, 0.001, 0.001, 0.0, 0.0, 0.0, 0.0)],
}


def rate_vectors(segment: str, trade_days: np.ndarray) -> np.ndarray:
    """trade_days: IST day numbers (days since 1970-01-01) per bar. Returns float64[n, 4]:
    buy_pct, sell_pct (incl. GST on exchange/SEBI), brokerage_pct, brokerage_cap (GST applied in the kernel)."""
    versions = SCHEDULES[segment]
    starts = np.array([(v.effective_from - date(1970, 1, 1)).days for v in versions])
    k = np.searchsorted(starts, trade_days, side="right") - 1
    if (k < 0).any():
        raise ValueError(f"no {segment} cost schedule for the earliest bars")
    tab = np.array([[v.stt_buy + v.stamp_buy + (v.exchange_txn + v.sebi) * (1 + v.gst),
                     v.stt_sell + (v.exchange_txn + v.sebi) * (1 + v.gst),
                     v.brokerage_pct * (1 + v.gst), v.brokerage_cap * (1 + v.gst)] for v in versions])
    return tab[k]
