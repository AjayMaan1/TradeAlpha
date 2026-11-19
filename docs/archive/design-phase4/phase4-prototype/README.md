# Phase 3/4 design prototype (Charts, Scheduler, UI)

Static, clickable mockups built with the **same stack proposed for the implementation**. It is a design artefact,
not production code, but the layout, components and chart logic are intended to be carried over.

| Open | Shows |
|---|---|
| `html/dashboard.html` | Dashboard (real-metrics layout) · `dashboard-auth-expired.html`: broker session expired state |
| `html/watchlists.html` | Multiple watchlists, live quotes, coverage chips, validated symbol search |
| `html/charts.html` | **Live Lightweight Charts 5.2**: panes, EMA/RSI computed in the browser, IST axis, gap marker, coverage strip (`charts-dark.html` = dark theme). Click 5m / D to switch datasets |
| `html/data.html` | Data Manager: coverage matrix, bulk actions, per-session calendar |
| `html/jobs.html` | Job engine UI: live progress, per-chunk tasks, queue, failed tasks |
| `html/scheduler.html` | Schedules + run history (skips, login waits, catch-ups) · `scheduler-editor.html`: editor drawer with preview |
| `html/settings.html` | Connection, broker limits/budget, storage, calendar |

Open the files directly from disk (no server needed); all assets are local (no CDN).
`screenshots/` has a 1440×900 render of every page; `diagrams/` has PNG renders of the HLD's Mermaid diagrams.

Assumed "now" in the mockups: **Fri 28 Mar 2025, 15:48 IST** (NSE closed; post-close job running).
Sample bars are a deterministic random walk on the real NSE calendar (`app.core.calendar`), in the proposed
`GET /api/v2/bars` payload shape (`html/assets/data/*.json`), with one missing session (26 Mar).

## Rebuild (dev-time only)
```bash
cd design/phase4-prototype
npm ci                 # tailwindcss 4, daisyUI 5, @alpinejs/csp, lightweight-charts 5.2, lucide-static, fontsource
python build.py        # renders src/templates/*.j2 -> html/, compiles assets/app.css, vendors JS/fonts/icons
```
Licences of the bundled assets: Tailwind/daisyUI/Alpine (MIT), Lightweight Charts (Apache-2.0, TradingView attribution
kept on), Lucide (ISC), Inter/JetBrains Mono (OFL-1.1). All compatible with AGPL-3.0.
