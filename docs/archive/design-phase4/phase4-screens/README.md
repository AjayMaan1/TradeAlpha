# Screenshots of the real v2 application

Captured from the running app (not the prototype) by `shoot.py`: it serves Historify against the mock OpenAlgo
broker (Angel One profile, deterministic synthetic prices), seeds watchlists, downloads and schedules through the
public services, and screenshots every page with headless Chromium. No user data is involved.

```bash
cd historify && python ../design/phase4-screens/shoot.py --out ../design/phase4-screens
```

| File | Shows |
|---|---|
| `dashboard.png`, `dashboard-dark.png` | KPIs, active (paused) backfill, needs-attention, upcoming / recent runs (incl. a skipped holiday) |
| `watchlists.png` | two lists, cached quotes, coverage chips |
| `charts.png`, `charts-5m-dark.png` | daily chart with EMA/RSI/volume panes; 5m in dark theme with paged history |
| `data.png`, `data-gap-drawer.png` | coverage matrix; series drawer with the session heat-map of a truncated series |
| `jobs.png`, `jobs-new-download.png` | a paused 1-year 1m backfill (newest chunks first); the new-download dialog |
| `scheduler.png`, `scheduler-editor.png` | schedules incl. MCX DST-aware close; editor with live preview + holiday skips + budget estimate |
| `import.png`, `export.png` | column mapping with per-row checks; export form |
| `settings.png`, `settings-calendar.png` | connection (write-only key), limits; market calendar month view |
| `setup.png` | first-run setup on an unconfigured install |
