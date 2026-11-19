/*
 * Charts (HLD §5): paged bars from /api/v2/bars, native panes, client-side indicators, IST axis, gap markers,
 * follow-live via /api/v2/follow + incremental `since=` polling.
 */
const IST_OFFSET_S = 19800;
const toChartTime = (epoch) => epoch + IST_OFFSET_S;         // the ONE place where time is converted for display
const INTRADAY = ['1m', '3m', '5m', '10m', '15m', '30m', '1h'];
const PAGE = 1500;

/* short labels keep a busy chart readable; the key is in the Indicators menu */
const PATTERN_ABBR = { hammer: 'H', shooting_star: 'SS', bull_engulfing: 'BE', bear_engulfing: 'BE', morning_star: 'MS',
                       evening_star: 'ES', three_white_soldiers: '3W', three_black_crows: '3B' };

function ema(values, period) {
  const k = 2 / (period + 1); const out = new Array(values.length).fill(null); let prev = null;
  for (let i = period - 1; i < values.length; i++) {
    if (prev === null) { let s = 0; for (let j = i - period + 1; j <= i; j++) s += values[j]; prev = s / period; }
    else prev = values[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

function rsi(values, period) {
  const out = new Array(values.length).fill(null); let gain = 0, loss = 0;
  for (let i = 1; i < values.length; i++) {
    const d = values[i] - values[i - 1]; const g = Math.max(d, 0), l = Math.max(-d, 0);
    if (i <= period) { gain += g; loss += l; if (i === period) { gain /= period; loss /= period; out[i] = 100 - 100 / (1 + gain / (loss || 1e-9)); } }
    else { gain = (gain * (period - 1) + g) / period; loss = (loss * (period - 1) + l) / period; out[i] = 100 - 100 / (1 + gain / (loss || 1e-9)); }
  }
  return out;
}

document.addEventListener('alpine:init', () => {
  Alpine.data('chartPage', () => {
    // chart objects live outside Alpine's reactive proxy
    const g = { chart: null, s: {}, markers: null, data: null, timers: [], index: new Map() };

    return {
      ...H.helpers,
      intervals: ['1m', '3m', '5m', '15m', '30m', '1h'], dailies: ['D', 'W', 'M'],
      ranges: [['1D', 86400], ['5D', 5 * 86400], ['1M', 31 * 86400], ['3M', 92 * 86400], ['1Y', 366 * 86400], ['All', 0]],
      instrument: null, interval: '5m', series: null, loading: false, empty: false, error: '',
      hasMore: false, nextTo: null, loadingOlder: false, loadedBars: 0,
      ind: Object.assign({ ema20: true, ema50: true, rsi: true, vol: true, patterns: false, supertrend: false },
                         JSON.parse(localStorage.getItem('historify-indicators') || '{}')),
      patternCounts: null,
      legend: { o: '', h: '', l: '', c: '', chg: '', up: true, v: '', e20: '', e50: '', r: '' },
      follow: false, followInfo: '', etag: null, source: '', sourcePref: 'auto', incomplete: [],
      q: '', results: [], showResults: false, dlJob: null,

      async init() {
        const p = H.params();
        this.interval = p.get('tf') || localStorage.getItem('historify-chart-tf') || '5m';
        let id = parseInt(p.get('i') || '0', 10);
        if (!id) {
          const lists = await H.api('/watchlists');
          for (const wl of lists) {
            if (!wl.count) continue;
            const items = await H.api('/watchlists/' + wl.id + '/items');
            if (items.length) { id = items[0].id; break; }
          }
        }
        window.addEventListener('historify:theme', () => this.applyTheme());
        window.addEventListener('resize', () => { if (g.chart) g.chart.applyOptions({}); });
        if (id) await this.open(id); else { this.empty = true; this.error = 'Add a symbol to a watchlist, then open its chart.'; }
      },
      destroy() { this.stopFollow(false); if (g.chart) g.chart.remove(); },

      // ------------------------------------------------------------------ loading
      async open(id) {
        this.stopFollow(true);
        this.loading = true; this.error = ''; this.empty = false;
        try {
          this.series = await H.api('/series/' + id + '/' + this.interval + '?days=90');
          this.instrument = this.series.instrument;
          const url = '/charts?i=' + id + '&tf=' + this.interval;
          window.history.replaceState(null, '', url);
          localStorage.setItem('historify-chart-tf', this.interval);
          const r = await H.api('/bars?instrument_id=' + id + '&interval=' + this.interval + '&limit=' + PAGE + '&tail=1' + this.srcParam(), { withEtag: true });
          this.source = r.data.source; this.incomplete = r.data.incomplete_sessions || [];
          this.etag = r.etag;
          g.data = r.data.bars;
          this.hasMore = r.data.has_more; this.nextTo = r.data.next_to;
          this.empty = g.data.t.length === 0;
          this.$nextTick(() => this.render());
        } catch (e) { this.error = e.message; }
        this.loading = false;
      },
      async loadOlder() {
        if (!this.hasMore || this.loadingOlder || !this.instrument) return;
        this.loadingOlder = true;
        try {
          const r = await H.api('/bars?instrument_id=' + this.instrument.id + '&interval=' + this.interval + '&limit=' + PAGE + '&tail=1&to=' + this.nextTo + this.srcParam());
          this.incomplete = Array.from(new Set(this.incomplete.concat(r.incomplete_sessions || [])));
          const b = r.bars; const added = b.t.length;
          if (added) {
            const range = g.chart.timeScale().getVisibleLogicalRange();
            for (const k of ['t', 'o', 'h', 'l', 'c', 'v']) g.data[k] = b[k].concat(g.data[k]);
            this.setAll();
            if (range) g.chart.timeScale().setVisibleLogicalRange({ from: range.from + added, to: range.to + added });
          }
          this.hasMore = r.has_more; this.nextTo = r.next_to;
        } catch (e) { H.toast(e.message, 'error'); }
        this.loadingOlder = false;
      },

      // ------------------------------------------------------------------ rendering
      colors() {
        const dark = document.documentElement.getAttribute('data-theme') === 'historify-dark';
        return dark ? { bg: '#151922', text: '#c9ced8', grid: 'rgba(148,163,184,0.08)', border: '#262c38' }
                    : { bg: '#ffffff', text: '#3b4252', grid: 'rgba(15,23,42,0.06)', border: '#e6e8ec' };
      },
      applyTheme() {
        if (!g.chart) return;
        const C = this.colors();
        g.chart.applyOptions({ layout: { background: { type: 'solid', color: C.bg }, textColor: C.text, panes: { separatorColor: C.border } },
                               grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
                               rightPriceScale: { borderColor: C.border }, timeScale: { borderColor: C.border } });
      },
      render() {
        const el = document.getElementById('chart');
        if (g.chart) { g.chart.remove(); g.chart = null; }
        if (this.empty || !el) return;
        const LW = window.LightweightCharts; const C = this.colors();
        const intraday = INTRADAY.includes(this.interval);
        g.chart = LW.createChart(el, {
          autoSize: true,
          layout: { attributionLogo: false, background: { type: 'solid', color: C.bg }, textColor: C.text, fontFamily: 'Inter Variable, sans-serif', fontSize: 11,
                    panes: { separatorColor: C.border, enableResize: true } },
          grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
          rightPriceScale: { borderColor: C.border },
          timeScale: { borderColor: C.border, timeVisible: intraday, secondsVisible: false, rightOffset: 6 },
          crosshair: { mode: LW.CrosshairMode.Normal },
        });
        g.s.candles = g.chart.addSeries(LW.CandlestickSeries, { upColor: '#16a34a', downColor: '#dc2626', borderVisible: false, wickUpColor: '#16a34a', wickDownColor: '#dc2626' }, 0);
        g.s.candles.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.24 } });
        g.s.vol = g.chart.addSeries(LW.HistogramSeries, { priceScaleId: 'vol', priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false }, 0);
        g.chart.priceScale('vol', 0).applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
        const line = (color) => g.chart.addSeries(LW.LineSeries, { color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 0);
        g.s.e20 = line('#6366f1'); g.s.e50 = line('#f59e0b');
        g.s.st = g.chart.addSeries(LW.LineSeries, { color: '#0ea5e9', lineWidth: 2, lineStyle: LW.LineStyle.Solid, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }, 0);
        g.s.rsi = g.chart.addSeries(LW.LineSeries, { color: '#8b5cf6', lineWidth: 1.5, priceLineVisible: false }, 1);
        for (const level of [70, 30]) g.s.rsi.createPriceLine({ price: level, color: C.text, lineWidth: 1, lineStyle: LW.LineStyle.Dashed, axisLabelVisible: true, title: '' });
        g.markers = LW.createSeriesMarkers(g.s.candles, []);
        this.setAll();
        this.applyIndicators();
        const n = g.data.t.length;
        g.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - (intraday ? 320 : 260)), to: n + 4 });
        g.chart.timeScale().subscribeVisibleLogicalRangeChange((range) => { if (range && range.from < 30) this.loadOlder(); });
        g.chart.subscribeCrosshairMove((p) => this.setLegend(p.time !== undefined && g.index.has(p.time) ? g.index.get(p.time) : g.data.t.length - 1));
      },
      setAll() {
        const b = g.data; const t = b.t.map(toChartTime);
        g.index = new Map(t.map((time, i) => [time, i]));
        g.s.candles.setData(t.map((time, i) => ({ time, open: b.o[i], high: b.h[i], low: b.l[i], close: b.c[i] })));
        g.s.vol.setData(t.map((time, i) => ({ time, value: b.v[i], color: b.c[i] >= b.o[i] ? 'rgba(22,163,74,0.35)' : 'rgba(220,38,38,0.35)' })));
        const e20 = ema(b.c, 20), e50 = ema(b.c, 50), r = rsi(b.c, 14);
        g.ind = { e20, e50, r };
        g.s.e20.setData(t.map((time, i) => (e20[i] === null ? { time } : { time, value: e20[i] })));
        g.s.e50.setData(t.map((time, i) => (e50[i] === null ? { time } : { time, value: e50[i] })));
        g.s.rsi.setData(t.map((time, i) => (r[i] === null ? { time } : { time, value: r[i] })));
        this.loadedBars = b.t.length;
        this.loadStructure();
        this.setMarkers();
        this.setLegend(b.t.length - 1);
      },
      setMarkers() {
        if (!g.markers || !this.series) return;
        const t = g.data.t; const out = [];
        for (const day of (INTRADAY.includes(this.interval) ? this.series.missing_sessions || [] : [])) {
          const dayEnd = Date.parse(day + 'T23:59:59+05:30') / 1000;
          const idx = t.findIndex((ts) => ts > dayEnd);
          if (idx > 0) out.push({ time: toChartTime(t[idx]), position: 'aboveBar', color: '#d97706', shape: 'arrowDown', text: 'gap: ' + day.slice(5) });
        }
        if (this.ind.patterns && g.patterns) {
          const sty = { bull: ['belowBar', '#16a34a', 'arrowUp'], bear: ['aboveBar', '#dc2626', 'arrowDown'], neutral: ['aboveBar', '#64748b', 'circle'] };
          for (const m of g.patterns) {
            const s = sty[m.side];
            out.push({ time: toChartTime(m.t), position: s[0], color: s[1], shape: s[2], text: PATTERN_ABBR[m.name] || m.name });
          }
          out.sort((a, b) => a.time - b.time);
        }
        g.markers.setMarkers(out);
      },
      async loadStructure() {
        // candlestick patterns + supertrend from the server (the IR operators' definitions, parity-tested)
        if (!this.ind.patterns && !this.ind.supertrend) { g.patterns = null; if (g.s.st) g.s.st.setData([]); return; }
        const b = g.data; const n = b.t.length; const lo = Math.max(0, n - 5000);
        try {
          const r = await H.api('/patterns', { method: 'POST', body: { t: b.t.slice(lo), o: b.o.slice(lo), h: b.h.slice(lo), l: b.l.slice(lo), c: b.c.slice(lo),
            names: ['hammer', 'shooting_star', 'bull_engulfing', 'bear_engulfing', 'morning_star', 'evening_star', 'three_white_soldiers', 'three_black_crows'],
            supertrend: this.ind.supertrend ? { n: 10, mult: 3 } : null } });
          g.patterns = this.ind.patterns ? r.markers : null;
          this.patternCounts = r.counts;
          if (r.supertrend && g.s.st) {
            const t = b.t.slice(lo).map(toChartTime);
            g.s.st.setData(t.map((time, i) => (r.supertrend.line[i] === null ? { time } : { time, value: r.supertrend.line[i], color: r.supertrend.dir[i] > 0 ? '#16a34a' : '#dc2626' })));
          } else if (g.s.st) { g.s.st.setData([]); }
          this.setMarkers();
        } catch (e) { /* overlays are optional */ }
      },
      setLegend(i) {
        const b = g.data; if (!b || i < 0 || i >= b.t.length) return;
        const prev = i > 0 ? b.c[i - 1] : b.o[i]; const chg = ((b.c[i] - prev) / prev) * 100;
        const f = (x) => (x === null || x === undefined ? '-' : x.toFixed(2));
        this.legend = { o: f(b.o[i]), h: f(b.h[i]), l: f(b.l[i]), c: f(b.c[i]), chg: (chg >= 0 ? '+' : '') + chg.toFixed(2) + '%', up: chg >= 0,
                        v: H.fmt.vol(b.v[i]), e20: f(g.ind.e20[i]), e50: f(g.ind.e50[i]), r: f(g.ind.r[i]), when: H.fmt.dateTime(b.t[i]) };
      },
      applyIndicators() {
        if (!g.chart) return;
        g.s.e20.applyOptions({ visible: !!this.ind.ema20 });
        g.s.e50.applyOptions({ visible: !!this.ind.ema50 });
        g.s.vol.applyOptions({ visible: !!this.ind.vol });
        g.s.rsi.applyOptions({ visible: !!this.ind.rsi });
        if (g.s.st) g.s.st.applyOptions({ visible: !!this.ind.supertrend });
        const panes = g.chart.panes();
        if (panes.length > 1) { panes[0].setStretchFactor(this.ind.rsi ? 0.76 : 1); panes[1].setStretchFactor(this.ind.rsi ? 0.24 : 0.0001); }
        localStorage.setItem('historify-indicators', JSON.stringify(this.ind));
      },
      toggleInd(key) {
        this.ind[key] = !this.ind[key]; this.applyIndicators();
        if (key === 'patterns' || key === 'supertrend') this.loadStructure();
      },
      indOn(key) { return !!this.ind[key]; },

      // ------------------------------------------------------------------ toolbar
      ivClass(iv) { return iv === this.interval ? 'btn-primary' : ''; },
      async pick(iv) { if (iv === this.interval || !this.instrument) return; this.interval = iv; await this.open(this.instrument.id); },
      async setRange(secs) {
        if (!g.chart || !g.data.t.length) return;
        const last = g.data.t[g.data.t.length - 1];
        if (!secs) { while (this.hasMore) await this.loadOlder(); g.chart.timeScale().fitContent(); return; }
        let guard = 0;
        while (this.hasMore && g.data.t[0] > last - secs && guard++ < 20) await this.loadOlder();
        g.chart.timeScale().setVisibleRange({ from: toChartTime(Math.max(g.data.t[0], last - secs)), to: toChartTime(last) });
      },
      screenshot() {
        if (!g.chart) return;
        const url = g.chart.takeScreenshot(true, false).toDataURL('image/png');
        H.download(url.replace('data:image/png', 'data:application/octet-stream'));
      },
      get isDerived() { return !!(this.series && this.series.source === 'derived_from_D'); },
      get isRolled() { return this.source === 'derived_from_1m' || (this.sourcePref === 'broker' && this.series && this.series.source === 'derived_from_1m'); },
      get sourceLabel() { return { derived_from_1m: 'rolled up from 1m', derived_from_D: 'derived from D', broker: 'broker candles' }[this.source] || ''; },
      srcParam() { return this.sourcePref === 'broker' ? '&source=broker' : ''; },
      setSource(v) { this.sourcePref = v; if (this.instrument) this.open(this.instrument.id); },
      get canFollow() { return this.instrument && !this.isDerived; },
      get title() { return this.instrument ? this.instrument.symbol : ''; },

      // ------------------------------------------------------------------ instrument search
      search: H.debounce(async function () {
        const q = this.q.trim();
        if (!q) { this.results = []; return; }
        try { this.results = (await H.api('/instruments?limit=10&q=' + encodeURIComponent(q))).results; } catch (e) { this.results = []; }
        this.showResults = true;
      }, 200),
      resultKey(r) { return r.symbol + ':' + r.exchange; },
      async choose(r) {
        let id = r.id;
        if (!id) {
          try { id = (await H.api('/instruments', { method: 'POST', body: { symbol: r.symbol, exchange: r.exchange } })).id; }
          catch (e) { H.toast(e.message, 'error'); return; }
        }
        this.q = ''; this.results = []; this.showResults = false;
        await this.open(id);
      },

      // ------------------------------------------------------------------ follow live
      async toggleFollow() { if (this.follow) this.stopFollow(true); else await this.startFollow(); },
      async startFollow() {
        if (!this.canFollow) return;
        try {
          await H.api('/follow', { method: 'POST', body: { instrument_id: this.instrument.id, interval: this.interval } });
        } catch (e) { H.toast(e.message, 'error'); return; }
        this.follow = true;
        this.followInfo = 'following · updates after each bar closes while the exchange is open';
        const beat = setInterval(() => H.api('/follow', { method: 'POST', body: { instrument_id: this.instrument.id, interval: this.interval } }).catch(() => {}), 60000);
        const poll = setInterval(() => this.pollSince(), INTRADAY.includes(this.interval) ? 15000 : 60000);
        g.timers = [beat, poll];
      },
      stopFollow(notify) {
        g.timers.forEach(clearInterval); g.timers = [];
        if (this.follow && notify && this.instrument) H.api('/follow', { method: 'DELETE', body: { instrument_id: this.instrument.id, interval: this.interval } }).catch(() => {});
        this.follow = false; this.followInfo = '';
      },
      async pollSince() {
        if (!g.data || !g.data.t.length || document.hidden) return;
        const last = g.data.t[g.data.t.length - 1];
        const r = await H.api('/bars?instrument_id=' + this.instrument.id + '&interval=' + this.interval + '&since=' + (last - 1) + '&limit=500' + this.srcParam(), { withEtag: true, etag: this.etag }).catch(() => null);
        if (!r || r.notModified) return;
        this.etag = r.etag;
        const b = r.data.bars; let changed = false;
        for (let i = 0; i < b.t.length; i++) {
          const pos = g.data.t.length - 1;
          if (b.t[i] === g.data.t[pos]) { for (const k of ['o', 'h', 'l', 'c', 'v']) g.data[k][pos] = b[k][i]; changed = true; }
          else if (b.t[i] > g.data.t[pos]) { for (const k of ['t', 'o', 'h', 'l', 'c', 'v']) g.data[k].push(b[k][i]); changed = true; }
        }
        if (changed) this.setAll();
      },

      // ------------------------------------------------------------------ data actions
      async download(mode, years) {
        if (!this.instrument) return;
        const iv = this.isDerived ? 'D' : this.interval;
        const body = { instrument_ids: [this.instrument.id], intervals: [iv], mode: mode };
        if (mode === 'fixed') { const end = new Date(H.serverNow()); const start = new Date(end.getTime() - years * 365.25 * 86400000); body.start = start.toISOString().slice(0, 10); body.end = end.toISOString().slice(0, 10); }
        try {
          const r = await H.api('/jobs/download', { method: 'POST', body: body });
          if (!r.tasks) { H.toast('Already up to date', 'success'); return; }
          this.dlJob = r.job_id; H.toast('Download started (' + r.tasks + ' task(s))', 'success');
          const wait = setInterval(async () => {
            const j = await H.api('/jobs/' + this.dlJob).catch(() => null);
            if (j && ['succeeded', 'partial', 'failed', 'cancelled'].includes(j.status)) { clearInterval(wait); this.dlJob = null; await this.open(this.instrument.id); }
          }, 1500);
        } catch (e) { H.toast(e.message, 'error'); }
      },
      get stripDays() { return this.series ? this.series.days.filter((d) => d.trading).slice(-60) : []; },
      dayCls(d) { return d.before_first ? 'bg-base-300/50' : d.bars >= d.expected ? 'bg-primary/70' : 'bg-error'; },
      dayTitle(d) { return d.date + ': ' + d.bars + ' / ' + d.expected + ' bars'; },
      freshness() {
        if (!this.series || !this.series.last_final_ts) return '-';
        return 'final to ' + (INTRADAY.includes(this.interval) ? H.fmt.dateTime(this.series.last_final_ts) : H.fmt.date(this.series.last_final_ts));
      }
    };
  });
});
