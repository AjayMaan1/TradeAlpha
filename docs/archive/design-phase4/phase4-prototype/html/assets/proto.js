/*
 * Historify Phase 4 prototype - Alpine.js (CSP build) components.
 * The CSP build forbids inline JS in templates: every behaviour lives in registered components (strict CSP, no eval).
 */
const IST_OFFSET_S = 19800;

/* One place that converts canonical UTC epochs to chart time.
 * Lightweight Charts renders timestamps as UTC; shifting by +05:30 makes every label IST wall-clock,
 * and daily bars (stored at 00:00 IST) land exactly on their trading date. */
const toChartTime = (epoch) => epoch + IST_OFFSET_S;

function ema(values, period) {
  const k = 2 / (period + 1);
  const out = new Array(values.length).fill(null);
  let prev = null;
  for (let i = 0; i < values.length; i++) {
    if (i < period - 1) continue;
    if (prev === null) {
      let s = 0;
      for (let j = i - period + 1; j <= i; j++) s += values[j];
      prev = s / period;
    } else {
      prev = values[i] * k + prev * (1 - k);
    }
    out[i] = prev;
  }
  return out;
}

function rsi(values, period) {
  const out = new Array(values.length).fill(null);
  let gain = 0, loss = 0;
  for (let i = 1; i < values.length; i++) {
    const d = values[i] - values[i - 1];
    const g = Math.max(d, 0), l = Math.max(-d, 0);
    if (i <= period) {
      gain += g; loss += l;
      if (i === period) { gain /= period; loss /= period; out[i] = 100 - 100 / (1 + gain / (loss || 1e-9)); }
    } else {
      gain = (gain * (period - 1) + g) / period;
      loss = (loss * (period - 1) + l) / period;
      out[i] = 100 - 100 / (1 + gain / (loss || 1e-9));
    }
  }
  return out;
}

const fmtVol = (v) => (v >= 1e7 ? (v / 1e7).toFixed(2) + " Cr" : v >= 1e5 ? (v / 1e5).toFixed(2) + " L" : v.toLocaleString("en-IN"));

document.addEventListener("alpine:init", () => {
  window.Alpine.data("chartPage", () => ({
    interval: "5m",
    legend: { o: "", h: "", l: "", c: "", chg: "", v: "", e20: "", e50: "" },
    legendUp: true,
    stats: { total: "", loaded: "" },
    chart: null,

    init() {
      this.render();
    },

    pickInterval(evt) {
      const iv = evt.currentTarget.dataset.iv;
      if (!window.HISTORIFY_SAMPLE[iv]) return;            // prototype ships 5m and D only
      this.interval = iv;
      this.render();
    },

    get legendTone() {
      return this.legendUp ? "text-success" : "text-error";
    },

    render() {
      const data = window.HISTORIFY_SAMPLE[this.interval];
      const el = document.getElementById("chart");
      if (this.chart) { this.chart.remove(); }
      const dark = document.documentElement.dataset.theme === "historify-dark";
      const C = dark
        ? { bg: "#151922", text: "#c9ced8", grid: "rgba(148,163,184,0.08)", border: "#262c38" }
        : { bg: "#ffffff", text: "#3b4252", grid: "rgba(15,23,42,0.06)", border: "#e6e8ec" };
      const LW = window.LightweightCharts;
      const intraday = this.interval !== "D" && this.interval !== "W" && this.interval !== "M";
      const chart = LW.createChart(el, {
        autoSize: true,
        layout: { background: { type: "solid", color: C.bg }, textColor: C.text, fontFamily: "Inter Variable, sans-serif",
                  fontSize: 11, panes: { separatorColor: C.border, enableResize: true } },
        grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
        rightPriceScale: { borderColor: C.border },
        timeScale: { borderColor: C.border, timeVisible: intraday, secondsVisible: false, rightOffset: 6 },
        crosshair: { mode: LW.CrosshairMode.Normal },
      });
      this.chart = chart;
      document.querySelectorAll("[data-iv]").forEach((btn) => {
        btn.classList.toggle("btn-primary", btn.dataset.iv === this.interval);
      });

      const b = data.bars;
      const t = b.t.map(toChartTime);
      const candles = chart.addSeries(LW.CandlestickSeries, {
        upColor: "#16a34a", downColor: "#dc2626", borderVisible: false, wickUpColor: "#16a34a", wickDownColor: "#dc2626",
      }, 0);
      candles.setData(t.map((time, i) => ({ time, open: b.o[i], high: b.h[i], low: b.l[i], close: b.c[i] })));
      candles.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.24 } });

      const volume = chart.addSeries(LW.HistogramSeries, {
        priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false,
      }, 0);
      chart.priceScale("vol", 0).applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      volume.setData(t.map((time, i) => ({ time, value: b.v[i],
        color: b.c[i] >= b.o[i] ? "rgba(22,163,74,0.35)" : "rgba(220,38,38,0.35)" })));

      const e20 = ema(b.c, 20), e50 = ema(b.c, 50);
      const line = (color) => chart.addSeries(LW.LineSeries, { color, lineWidth: 2, priceLineVisible: false,
                                                              lastValueVisible: false, crosshairMarkerVisible: false }, 0);
      line("#6366f1").setData(t.map((time, i) => (e20[i] === null ? { time } : { time, value: e20[i] })));
      line("#f59e0b").setData(t.map((time, i) => (e50[i] === null ? { time } : { time, value: e50[i] })));

      const r = rsi(b.c, 14);
      const rsiSeries = chart.addSeries(LW.LineSeries, { color: "#8b5cf6", lineWidth: 1.5, priceLineVisible: false }, 1);
      rsiSeries.setData(t.map((time, i) => (r[i] === null ? { time } : { time, value: r[i] })));
      for (const level of [70, 30]) {
        rsiSeries.createPriceLine({ price: level, color: C.text, lineWidth: 1, lineStyle: LW.LineStyle.Dashed,
                                    axisLabelVisible: true, title: "" });
      }
      chart.panes()[0].setStretchFactor(0.76);
      chart.panes()[1].setStretchFactor(0.24);

      // gap markers: first bar after each missing session
      const missing = data.coverage.missing_sessions;
      if (missing.length && intraday) {
        const markers = missing.map((day) => {
          const dayStart = Date.parse(day + "T00:00:00+05:30") / 1000;
          const idx = b.t.findIndex((ts) => ts > dayStart);
          return { time: t[idx], position: "aboveBar", color: "#d97706", shape: "arrowDown", text: "gap: " + day.slice(5) + " missing" };
        });
        LW.createSeriesMarkers(candles, markers);
      }

      // legend follows the crosshair; defaults to the last bar
      const setLegend = (i) => {
        const prev = i > 0 ? b.c[i - 1] : b.o[i];
        const chg = ((b.c[i] - prev) / prev) * 100;
        this.legendUp = chg >= 0;
        this.legend = { o: b.o[i].toFixed(2), h: b.h[i].toFixed(2), l: b.l[i].toFixed(2), c: b.c[i].toFixed(2),
                        chg: (chg >= 0 ? "+" : "") + chg.toFixed(2) + "%", v: fmtVol(b.v[i]),
                        e20: e20[i] === null ? "-" : e20[i].toFixed(2), e50: e50[i] === null ? "-" : e50[i].toFixed(2) };
      };
      const index = new Map(t.map((time, i) => [time, i]));
      setLegend(t.length - 1);
      chart.subscribeCrosshairMove((p) => setLegend(p.time !== undefined && index.has(p.time) ? index.get(p.time) : t.length - 1));

      // initial window: newest ~N bars (the API returns the newest page first; older pages load on scroll)
      const shown = intraday ? 320 : 260;
      chart.timeScale().setVisibleLogicalRange({ from: t.length - shown, to: t.length + 4 });
      this.stats = { total: data.coverage.bars.toLocaleString("en-IN"),
                     loaded: Math.min(t.length, 1500).toLocaleString("en-IN") + " (pages of 1,500)" };
    },
  }));

  window.Alpine.data("schedulerEditor", () => ({
    open: false,
    init() { this.open = document.body.dataset.editor === "open"; },
    show() { this.open = true; },
    hide() { this.open = false; },
  }));
});
