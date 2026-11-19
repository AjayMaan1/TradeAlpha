---
trigger: always_on
---

Rules for code changes in TradeAlpha (see ARCHITECTURE.md and docs/architecture/ for the full design):

1) Follow the bounded contexts: platform, marketdata, altdata, features, research, ideas, lifecycle, execution,
   portfolio, interfaces, apps. Prefer importing another context through its `api.py` where one exists.
2) Respect the dependency direction (interfaces/apps → ideas, lifecycle, execution, portfolio → research →
   features → marketdata, altdata → platform). CI checks it (`tools/check_boundaries.py`).
3) Never change execution/, */risk, */safety, lifecycle/gates, platform/auth or migrations without an ADR and review.
4) Everything asynchronous is a job (no threads started from HTTP handlers).
5) Every dataset/feature declares its point-in-time availability; nothing may read the future.
6) Only IR strategies are deployable; Python strategies are research-only.
7) UI: Flask + Jinja page shells, Alpine.js CSP build, Tailwind/daisyUI compiled locally (ui/build.py) - no CDN, no
   inline JS/CSS (strict Content-Security-Policy).
8) Metadata in SQLite (SQLAlchemy + Alembic), bars and analytics in DuckDB/Parquet. No bars in SQLite.
9) Secrets only in the encrypted settings store or .env; never in code or logs.
10) Licence AGPL-3.0; new dependencies must be AGPL-compatible (CI checks).
11) Every change keeps the test suite green and adds tests; safety invariants live in tests/invariants.
