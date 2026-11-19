# Broker evaluation kit

Supports [`../BROKER_COST_AND_DATA_COMPARISON.md`](../BROKER_COST_AND_DATA_COMPARISON.md).

| File | Purpose |
|---|---|
| `broker_tariffs.toml` | **Edit me.** Per-broker tariffs, data limits and API capabilities, each with a confidence code (V/H/M/L/?) |
| `statutory_charges.toml` | STT / stamp / exchange / SEBI / GST, versioned by effective date (identical for all brokers) |
| `scenarios.toml` | **Edit me.** Your trading profiles and the research data target |
| `broker_compare.py` | Calculator: writes `results/cost_summary.md`, `cost_by_scenario.md`, `data_backfill.md`, `capability_matrix.md` |
| `broker_data_probe.py` | Measures a broker's history depth, per-request limits and candle conventions **through your OpenAlgo** (read-only, paced, rate-limit-safe) |
| `broker_data_compare.py` | Bar-by-bar comparison of two probe results (same candles?) |
| `mock_broker.py` | Starts Historify's mock OpenAlgo with known limits (used to validate the probe) |

```bash
python3 broker_compare.py                         # Python >= 3.11 (tomllib); no third-party packages
OPENALGO_API_KEY=... python broker_data_probe.py --label <broker> --out results/probe_<broker>.json   # needs httpx
python broker_data_compare.py results/probe_a.json results/probe_b.json
```

Validation run (mock brokers with known limits): `results/probe_mock_A.json` and `results/probe_mock_B.json`.
