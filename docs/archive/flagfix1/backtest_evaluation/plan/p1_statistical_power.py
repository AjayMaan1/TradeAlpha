"""P1: what 1 year of backtest data and 3 months of paper trading can (and cannot) prove. Daily P&L, normal approx.
MinTRL: Bailey & Lopez de Prado (2012) minimum track record length, 95 % one-sided, skew 0, kurtosis 3."""
import json, math
from statistics import NormalDist
N = NormalDist(); Z = N.inv_cdf(0.95); DAYS = 248
def min_trl(sr_ann, skew=0.0, kurt=3.0):
    s = sr_ann / math.sqrt(DAYS)
    return 1 + (1 - skew * s + (kurt - 1) / 4 * s * s) * (Z / s) ** 2
def detectable_sr(T):                       # smallest annual SR significant at 95 % with T daily observations
    return Z / math.sqrt(T - 1) * math.sqrt(DAYS)
def p_detect_dead_edge(sr_backtest, T):     # paper P&L falls below the backtest's 5th-percentile band when true SR = 0
    return N.cdf(math.sqrt(T) * sr_backtest / math.sqrt(DAYS) - Z)
def breadth(sr_single, n, rho):             # portfolio of n equally weighted streams with pairwise correlation rho
    return sr_single * math.sqrt(n / (1 + (n - 1) * rho))
res = {"min_trl_days": {sr: round(min_trl(sr)) for sr in (1.0, 1.5, 2.0, 3.0)},
       "detectable_sr": {"1y (248 d)": round(detectable_sr(248), 2), "1y+3m (310 d)": round(detectable_sr(310), 2),
                         "3m paper (62 d)": round(detectable_sr(62), 2)},
       "p_3m_paper_detects_dead_edge": {sr: round(p_detect_dead_edge(sr, 62), 2) for sr in (1.0, 1.5, 2.0, 3.0, 4.0)},
       "breadth_sr": {f"n={n}, rho={r}": round(breadth(1.0, n, r), 2) for n in (1, 10, 50) for r in (0.1, 0.3)}}
print("Minimum track record (daily P&L) for SR > 0 at 95 %:")
for sr, d in res["min_trl_days"].items(): print(f"   annual Sharpe {sr:3.1f}: {d:5d} trading days  (~{d/DAYS:.1f} y)")
print("Smallest annual Sharpe that is statistically significant:", res["detectable_sr"])
print("Chance that 3 months of paper trading flags a strategy whose edge is actually ZERO (by backtest Sharpe):", res["p_3m_paper_detects_dead_edge"])
print("Portfolio Sharpe from streams of Sharpe 1.0 (breadth):", res["breadth_sr"])
json.dump(res, open("/workspace/backtest_evaluation/results/p1_statistical_power.json", "w"), indent=1)
