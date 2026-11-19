"""
Build the Phase 4 design prototype (static HTML, no server needed; open html/*.html in a browser).

    cd design/phase4-prototype && npm ci && python build.py

Needs (dev-time only): tailwindcss 4 + daisyUI 5 + @alpinejs/csp + lightweight-charts 5.2 + lucide-static + fontsource
in NODE_MODULES. End users never need Node: the compiled CSS/JS are committed under html/assets.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "historify"))
from app.core.calendar import MarketCalendar  # noqa: E402  (reuse the real calendar for sample data)

NM = Path(os.environ.get("NODE_MODULES", HERE / "node_modules"))   # run `npm ci` in this folder first
OUT = HERE / "html"
ASSETS = OUT / "assets"
ICONS = HERE / "src" / "icons"
IST = timezone(timedelta(hours=5, minutes=30))

ICON_NAMES = ["layout-dashboard", "list", "chart-candlestick", "database", "list-checks", "file-up", "file-down",
              "calendar-clock", "settings", "search", "sun", "moon", "play", "pause", "square", "rotate-ccw", "plus",
              "pencil", "trash-2", "chevron-down", "circle-check", "triangle-alert", "circle-x", "clock", "plug",
              "gauge", "download", "camera", "maximize-2", "activity", "zap", "wrench", "info", "x", "history",
              "layers", "filter", "refresh-cw", "link-2-off"]


# ---------------------------------------------------------------- sample data (API v2 /bars shape)
def _walk(n, start, vol, seed):
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(rng.normal(0.0002, vol, n)))
    open_ = np.concatenate([[start], close[:-1]]) * (1 + rng.normal(0, vol / 4, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 1.5, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 1.5, n)))
    return [np.round(x, 2) for x in (open_, high, low, close)], rng


def _write(payload, name):
    """JSON = the proposed GET /api/v2/bars response; .js = same payload for file:// use (no fetch from disk)."""
    body = json.dumps(payload, separators=(",", ":"))
    (ASSETS / "data" / f"{name}.json").write_text(body)
    key = payload["interval"]
    (ASSETS / "data" / f"{name}.js").write_text(
        f"window.HISTORIFY_SAMPLE = window.HISTORIFY_SAMPLE || {{}}; window.HISTORIFY_SAMPLE[{json.dumps(key)}] = {body};")


def make_data():
    cal = MarketCalendar.default()
    (ASSETS / "data").mkdir(parents=True, exist_ok=True)
    # 5-minute, ~40 sessions, one session deliberately missing to demonstrate gap handling
    days = cal.trading_days("NSE", date(2025, 1, 27), date(2025, 3, 28))
    missing = date(2025, 3, 26)
    ts = []
    for d in days:
        if d == missing:
            continue
        t0 = datetime.combine(d, time(9, 15), IST)
        ts += [int((t0 + timedelta(minutes=5 * i)).timestamp()) for i in range(75)]
    (o, h, lo, c), rng = _walk(len(ts), 768.0, 0.0016, 7)
    idx = np.arange(len(ts)) % 75
    vol = (rng.integers(40_000, 90_000, len(ts)) * (1.8 - np.sin(np.pi * idx / 74) * 1.1)).astype(int)
    five = {"instrument": {"id": 12, "symbol": "SBIN", "exchange": "NSE", "name": "State Bank of India"},
            "interval": "5m", "tz": "Asia/Kolkata",
            "coverage": {"first_ts": ts[0], "last_ts": ts[-1], "bars": len(ts), "expected_sessions": len(days),
                         "missing_sessions": [missing.isoformat()], "last_sync": "2025-03-28T15:46:12+05:30"},
            "bars": {"t": ts, "o": o.tolist(), "h": h.tolist(), "l": lo.tolist(), "c": c.tolist(), "v": vol.tolist()}}
    _write(five, "SBIN_NSE_5m")
    # daily, ~3 years
    ddays = cal.trading_days("NSE", date(2022, 1, 3), date(2025, 3, 28))
    dts = [int(datetime.combine(d, time(0, 0), IST).timestamp()) for d in ddays]
    (o, h, lo, c), rng = _walk(len(dts), 470.0, 0.014, 11)
    dv = rng.integers(9_000_000, 28_000_000, len(dts)).tolist()
    daily = {"instrument": five["instrument"], "interval": "D", "tz": "Asia/Kolkata",
             "coverage": {"first_ts": dts[0], "last_ts": dts[-1], "bars": len(dts), "expected_sessions": len(ddays),
                          "missing_sessions": [], "last_sync": "2025-03-28T15:46:12+05:30"},
             "bars": {"t": dts, "o": o.tolist(), "h": h.tolist(), "l": lo.tolist(), "c": c.tolist(), "v": dv}}
    _write(daily, "SBIN_NSE_D")
    strip = [{"day": d.strftime("%d %b"), "missing": d == missing, "loaded": i >= len(days) - 5} for i, d in enumerate(days)]
    return {"bars_5m": len(ts), "bars_d": len(dts), "strip": strip}


# ---------------------------------------------------------------- assets
def copy_assets():
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "fonts").mkdir(exist_ok=True)
    shutil.copy(NM / "lightweight-charts/dist/lightweight-charts.standalone.production.js", ASSETS)
    shutil.copy(NM / "@alpinejs/csp/dist/cdn.min.js", ASSETS / "alpine-csp.min.js")
    shutil.copy(NM / "@fontsource-variable/inter/files/inter-latin-wght-normal.woff2", ASSETS / "fonts")
    shutil.copy(NM / "@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2", ASSETS / "fonts")
    shutil.copy(HERE / "src" / "proto.js", ASSETS)
    ICONS.mkdir(parents=True, exist_ok=True)
    for name in ICON_NAMES:
        src = NM / "lucide-static/icons" / f"{name}.svg"
        if src.exists():
            shutil.copy(src, ICONS / f"{name}.svg")


def icon(name, cls="icon"):
    svg = (ICONS / f"{name}.svg").read_text()
    svg = svg[svg.index("<svg"):]
    svg = svg.replace("<svg", f'<svg class="{cls}" aria-hidden="true"', 1)
    for attr in ('width="24"', 'height="24"'):
        svg = svg.replace(attr, "", 1)
    return Markup(" ".join(svg.split()))


# ---------------------------------------------------------------- pages
PAGES = [  # (output file, template, context)
    ("dashboard.html", "dashboard.j2", {}),
    ("dashboard-auth-expired.html", "dashboard.j2", {"auth_expired": True}),
    ("watchlists.html", "watchlists.j2", {}),
    ("charts.html", "charts.j2", {}),
    ("charts-dark.html", "charts.j2", {"theme": "historify-dark"}),
    ("data.html", "data.j2", {}),
    ("jobs.html", "jobs.j2", {}),
    ("scheduler.html", "scheduler.j2", {}),
    ("scheduler-editor.html", "scheduler.j2", {"editor_open": True}),
    ("settings.html", "settings.j2", {}),
]


def render(strip):
    env = Environment(loader=FileSystemLoader(HERE / "src" / "templates"), autoescape=True)
    env.globals["icon"] = icon
    env.globals["strip"] = strip
    for out, tpl, ctx in PAGES:
        html = env.get_template(tpl).render(**{"theme": "historify-light", **ctx})
        (OUT / out).write_text(html)


def build_css():
    cli = NM / ".bin" / "tailwindcss"
    subprocess.run([str(cli), "-i", str(HERE / "src" / "input.css"), "-o", str(ASSETS / "app.css"), "--minify"],
                   check=True, cwd=HERE / "src", capture_output=True)


if __name__ == "__main__":
    stats = make_data()
    copy_assets()
    render(stats.pop("strip"))
    build_css()
    print("built", len(PAGES), "pages;", stats, "; css", (ASSETS / "app.css").stat().st_size // 1024, "KB")
