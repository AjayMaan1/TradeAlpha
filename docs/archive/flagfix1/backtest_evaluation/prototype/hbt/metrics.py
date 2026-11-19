"""Calendar-correct performance statistics and multiple-testing control for strategy search."""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

from hbt.kernel import R_RET_M2, R_RET_MEAN, R_RET_N

_N = NormalDist()
EULER = 0.5772156649


def bars_per_year(sessions_per_year: float, bars_per_session: float) -> float:
    """NSE 5m: ~248 sessions x 75 bars = 18,600 (a 24x365 default would use 105,120 and inflate Sharpe 2.4x)."""
    return sessions_per_year * bars_per_session


def sharpe(res: np.ndarray, periods_per_year: float, annualize: bool = True) -> np.ndarray:
    """From kernel result vectors (..., N_RESULT). Per-bar Sharpe, optionally annualised."""
    n = res[..., R_RET_N]
    var = np.where(n > 1, res[..., R_RET_M2] / np.maximum(n - 1, 1), np.nan)
    sr = np.where(var > 0, res[..., R_RET_MEAN] / np.sqrt(var), 0.0)
    return sr * math.sqrt(periods_per_year) if annualize else sr


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """E[max SR] of n_trials strategies with zero true skill (Bailey & Lopez de Prado, 2014)."""
    if n_trials < 2:
        return 0.0
    return math.sqrt(sr_variance) * ((1 - EULER) * _N.inv_cdf(1 - 1 / n_trials)
                                     + EULER * _N.inv_cdf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(sr_best: float, n_obs: int, n_trials: int, sr_variance: float, skew: float = 0.0,
                    kurt: float = 3.0) -> float:
    """Probability that the best per-period Sharpe is real after n_trials tries (DSR). > 0.95 ~ significant."""
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    den = math.sqrt(max(1e-12, 1 - skew * sr_best + (kurt - 1) / 4 * sr_best ** 2))
    return _N.cdf((sr_best - sr0) * math.sqrt(max(1, n_obs - 1)) / den)
