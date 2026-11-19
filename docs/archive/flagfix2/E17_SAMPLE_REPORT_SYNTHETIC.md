# E17 - India VIX vs Nifty intraday behaviour

> **SYNTHETIC DATA - method validation only, not a market result.**

Generated 2026-11-14T05:09:41.131837+00:00 · data 2022-03-28 → 2024-06-28 · 590 sessions · sources {'1m': 590}

**Verdict: SUPPORTED** - Nifty's intraday range rises monotonically with the prior-close VIX percentile (Spearman 0.553, p=0.0005): use the VIX regime for size and stop width. Trend-day frequency does not.

| VIX tercile (prev close, 1-yr pct) | Days | VIX range | Range % mean | Range % median | |O→C| % | Gap % | Trend-day rate | ORB follow-through | Range ÷ expected |
|---|---|---|---|---|---|---|---|---|---|
| low | 138 | 10.353–17.54 | 1.286 | 1.219 | 0.584 | 0.193 | 0.42 | 0.486 (138 days) | 1.509 |
| mid | 213 | 12.784–21.134 | 1.71 | 1.64 | 0.819 | 0.241 | 0.404 | 0.469 (213 days) | 1.561 |
| high | 239 | 15.166–30.173 | 2.162 | 2.015 | 1.08 | 0.337 | 0.477 | 0.548 (239 days) | 1.46 |

| Test | Statistic | p (permutation) | Monotonic across terciles |
|---|---|---|---|
| range | Spearman 0.553 | 0.0005 | True |
| abs_open_to_close | Spearman 0.275 | 0.0005 | True |
| gap | Spearman 0.25 | 0.0005 | True |
| trend_day | high−low 0.057 (95% CI -0.041…0.161) | 0.3293 | False |
| orb_follow_through | high−low 0.063 (95% CI -0.04…0.17) | 0.2909 | False |

Calibration: median(range ÷ VIX-implied daily σ) = 1.501; share of days with |O→C| beyond 1σ = 0.298 (≈ 0.32 if VIX were an unbiased σ of the open-to-close move).

Design is pre-registered in app/lab/experiments/e17.py (prior-close VIX, trailing-252 percentile, fixed terciles, seeded permutation tests).
