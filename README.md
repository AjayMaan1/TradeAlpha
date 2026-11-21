# TradeAlpha Data Engine — High-Integrity Market Data & History Operations

**TradeAlpha Data Engine** downloads, stores, verifies, and charts historical OHLCV and market data for Indian financial markets (NSE, BSE, NFO, MCX, CDS, indices) via your [OpenAlgo](https://openalgo.in) server. It operates as a local, high-performance market data management system and time-series backtest store.

![Dashboard](design/phase4-screens/dashboard.png)

## Core Capabilities & Data Operations

- **Automated Download Engine** — Background ingestion chunked to broker rate limits (Angel One / OpenAlgo), paced to API quotas (3/s, 180/min, 5,000/h), automatically paused on session expiration, and resumed after restart without data loss. Incremental updates are **interval-aware** and only fetch missing candles.
- **Backtesting-Grade Data Integrity & Health** — Session ledger per symbol × interval × trading day. Ingestion validation enforces the exchange session grid: off-grid, pre-open, post-close, and closed-day candles are quarantined rather than silently stored. Includes broker revision detection and weekly consistency audits.
- **Immutable Dataset Snapshots** — Exports immutable, checksummed Parquet datasets with cryptographic manifests (`SHA-256`), ensuring quantitative backtests can be reproduced deterministically on exact point-in-time data.
- **Multi-Interval Roll-Ups** — Daily bars reflect official exchange closing prices; intraday intervals (5m, 15m, 1h) are rolled up from 1m with session-anchored boundaries (09:15 IST NSE). Includes a built-in roll-up auditor to verify derived bars against broker feeds.
- **Market-Aware Scheduler** — Session-aligned triggers (e.g., NSE close + 15 min = 15:45 IST on trading days), intraday update cycles, and holiday calendars. Missed jobs while the server was offline are automatically caught up on launch.
- **Data Manager & Coverage Heatmaps** — Visual coverage matrix of every symbol × interval compared against the exchange calendar to surface gaps, stale feeds, or missing bars with one-click backfill and repair.
- **TradingView Charts & Follow Live** — Responsive TradingView Lightweight Charts with EMA, RSI, volume, lazy-loading history, and real-time live bar updates as each candle closes.
- **Import / Export Engine** — High-speed import and export supporting CSV, Excel, and Parquet directly from the underlying DuckDB and SQLite storage engines.

| | |
|---|---|
| ![Charts](design/phase4-screens/charts.png) | ![Data Manager](design/phase4-screens/data-gap-drawer.png) |
| ![Scheduler](design/phase4-screens/scheduler-editor.png) | ![Jobs](design/phase4-screens/jobs.png) |

---

## Quickstart & Installation

**Prerequisites:** Python **3.12+**

```bash
# Clone the repository
git clone https://github.com/AjayMaan1/TradeAlpha.git
cd TradeAlpha

# Set up virtual environment
python3 -m venv venv
source venv/bin/activate       # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
pip install -e .

# Run the local web interface
python run.py                  # On Windows: start.bat
```

Open your browser to <http://127.0.0.1:5001>. On first launch, follow the setup screen to connect your OpenAlgo instance (host + API key).

> **Security Defaults:** TradeAlpha binds strictly to `127.0.0.1`, runs without debugger exposure, and serves with a strict Content-Security-Policy. Broker credentials and tokens are encrypted locally. See [SECURITY.md](SECURITY.md).

If OpenAlgo enforces CORS, add TradeAlpha's origin to OpenAlgo's `.env`:
```env
CORS_ALLOWED_ORIGINS = 'http://127.0.0.1:5000,http://127.0.0.1:5001'
```

---

## Configuration

All runtime configurations can be managed via the **Settings** web UI or configured via environment variables (`.env`, see [.env.sample](.env.sample)):

| Variable | Default | Description |
| :--- | :---: | :--- |
| `TRADEALPHA_HOST` / `TRADEALPHA_PORT` | `127.0.0.1` / `5001` | Local web app bind address |
| `TRADEALPHA_PROVIDER_PROFILE` | `angelone` | Broker limits profile (`angelone`, `generic`) |
| `TRADEALPHA_RATE_BUDGET` | `0.8` | Share of available broker rate limits allocated to downloads |
| `TRADEALPHA_ENGINE` | `v2` | High-integrity DuckDB + unified scheduler engine |
| `TRADEALPHA_INSTANCE_PATH` | `instance` | Local database storage path |
| `TRADEALPHA_SECRET_BACKEND` | `file` | Credential storage backend (`file` or keyring) |

---

## System Architecture

```
Browser UI (Jinja templates + Alpine.js CSP + Tailwind + Lightweight Charts)
   │  JSON API: /api/v2/*
TradeAlpha Application Server
   ├─ JobService + DownloadEngine  Rate-limited worker pool → Single writer queue
   ├─ SchedulerService             Market calendar-aware triggers (APScheduler)
   ├─ FollowService                Real-time chart streaming as candles close
   ├─ BackgroundRunner             Parquet snapshot exports & bulk data backfills
   ├─ QuoteService                 Cached REST quote warmer (5-second cache)
   ├─ Meta Store (SQLite / Alembic) Instruments, watchlists, sync states, schedules
   └─ Bar Store (DuckDB)           High-throughput partitioned OHLCV bar store
```

---

## Data Management & CLI Operations

CLI utilities for headless environments and batch maintenance:

```bash
# Storage statistics and health checks
flask --app run.py tradealpha storage-stats
flask --app run.py tradealpha selftest

# Roll-up and data integrity audits
flask --app run.py tradealpha rollup-audit [--apply]
flask --app run.py tradealpha integrity-audit
flask --app run.py tradealpha maintenance

# Create and verify immutable backtest snapshots
flask --app run.py tradealpha snapshot create NIFTY50_2024 --intervals D,5m --start 2024-01-01
flask --app run.py tradealpha snapshot verify <SNAPSHOT_ID>
```

Reading a snapshot directly in backtests (zero overhead, standard Python):
```python
import duckdb
import pandas as pd

# Direct DuckDB query
df = duckdb.sql("SELECT * FROM 'data/SBIN_NSE_5m.parquet'").df()

# Or standard pandas parquet reader
df = pd.read_parquet("data/SBIN_NSE_5m.parquet")
```

---

## Development & Verification

TradeAlpha uses a multi-tier test and verification pipeline:

```bash
# Run full automated verification suite (lint, unit, contract, integration, repo)
bash scripts/verify.sh

# Run individual test layers
pytest tests/unit
pytest tests/integration
pytest tests/e2e
```

---

## License

Apache-2.0 — see [LICENSE](LICENSE). Charts powered by [TradingView Lightweight Charts™](https://www.tradingview.com/).
