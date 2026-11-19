# Overview diagrams and summary images

| File | Content |
|---|---|
| `hld-data-flow.png` | Components and protocols: browser ↔ API (HTTP polling), engine ↔ OpenAlgo (HTTP), storage |
| `hld-fetch-sequence.png` | One post-close download step by step: plan → fetch → validate → revisions → upsert → ledger |
| `hld-rollup.png` | What is stored (1m, D from the broker) and what is computed on read (3m…1h from 1m, W/M from D) |
| `hld-maintenance.png` | The weekly maintenance steps |
| `data-sources.png`, `checkpoint-flow.png`, `trading-day.png` | Interval sources, incremental decision flow, a trading day with the default schedules |
| `ui-overview.png`, `metrics-summary.png` | UI contact sheet, before/after metrics |
