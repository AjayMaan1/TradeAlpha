//! Streaming kernels: one per IR operator (design §8.1). A kernel is a small state machine; `step` consumes the
//! inputs of ONE bar and produces that bar's output(s). Batch evaluation is the same `step` run over the whole array,
//! so batch == streaming by construction.
//!
//! NaN policy (design §6.4):
//! * positional operators (shift, diff, rolling_*, cross_*, ...) keep bar alignment: a NaN inside a window gives NaN
//! * indicators (sma, ema, rsi, ...) follow TA-Lib: leading NaNs are skipped (the indicator starts at the first valid
//!   input); a later NaN input gives a NaN output and leaves the state unchanged
//! * comparisons with NaN are false; booleans are never NaN
//!
//! Definitions and warm-up follow TA-Lib where TA-Lib has the indicator (decision D-10); parity is tested against
//! TA-Lib 0.8 in tests/lab/test_indicators.py.

use std::collections::VecDeque;

use hbt_ir::compile::NodeSpec;
use hbt_ir::ops::{Column, Op};

const NAN: f64 = f64::NAN;
/// running sums are recomputed from the window every RESUM steps (bounded drift on long series)
const RESUM: u32 = 256;

#[inline]
fn b(x: bool) -> f64 {
    if x { 1.0 } else { 0.0 }
}

#[inline]
fn truthy(x: f64) -> bool {
    x != 0.0 && !x.is_nan()
}

/// The bar a kernel sees: its frame's OHLCV plus session context (run interval only) and extra columns.
#[derive(Clone, Copy)]
pub struct BarIn<'a> {
    pub o: f64,
    pub h: f64,
    pub l: f64,
    pub c: f64,
    pub v: f64,
    pub new_session: bool,
    /// minute of day of the bar's open (IST)
    pub minute: i32,
    pub bar_mins: i32,
    pub sess_open: i32,
    pub sess_close: i32,
    pub day: i64,
    pub extras: &'a [f64],
}

/// Position state in SIGNAL scale (rupees on the strategy's price scale); NaN prices when flat.
#[derive(Clone, Copy, Default)]
pub struct PosIn {
    pub qty: f64,
    pub entry: f64,
    pub bars_in_trade: f64,
    pub unrealised_pct: f64,
    pub highest: f64,
    pub lowest: f64,
}

/// Fixed-capacity ring of the last `cap` values.
#[derive(Clone)]
struct Ring {
    buf: Vec<f64>,
    pos: usize,
    len: usize,
}

impl Ring {
    fn new(cap: usize) -> Self {
        Ring { buf: vec![0.0; cap.max(1)], pos: 0, len: 0 }
    }
    fn cap(&self) -> usize {
        self.buf.len()
    }
    fn full(&self) -> bool {
        self.len == self.buf.len()
    }
    /// push; returns the evicted value when full
    fn push(&mut self, x: f64) -> Option<f64> {
        let out = if self.full() { Some(self.buf[self.pos]) } else { None };
        self.buf[self.pos] = x;
        self.pos = (self.pos + 1) % self.buf.len();
        if self.len < self.buf.len() {
            self.len += 1;
        }
        out
    }
    /// k-th most recent value (0 = newest)
    fn ago(&self, k: usize) -> f64 {
        let c = self.buf.len();
        self.buf[(self.pos + c - 1 - k) % c]
    }
    fn iter(&self) -> impl Iterator<Item = f64> + '_ {
        (0..self.len).map(move |k| self.ago(k))
    }
}

/// Windowed sum / sum of squares with NaN count and periodic exact recomputation. The sums are kept on SHIFTED data
/// (x - k, k re-centred on the window mean at every recomputation), which removes the cancellation of
/// E[x^2] - E[x]^2 on price-level series (variance is tiny relative to the mean).
#[derive(Clone)]
struct Win {
    ring: Ring,
    k: f64,
    sum: f64,
    sq: f64,
    nans: usize,
    steps: u32,
}

impl Win {
    fn new(n: usize) -> Self {
        Win { ring: Ring::new(n), k: f64::NAN, sum: 0.0, sq: 0.0, nans: 0, steps: 0 }
    }
    fn push(&mut self, x: f64) {
        if self.k.is_nan() && !x.is_nan() {
            self.k = x;
        }
        if let Some(old) = self.ring.push(x) {
            if old.is_nan() {
                self.nans -= 1;
            } else {
                let y = old - self.k;
                self.sum -= y;
                self.sq -= y * y;
            }
        }
        if x.is_nan() {
            self.nans += 1;
        } else {
            let y = x - self.k;
            self.sum += y;
            self.sq += y * y;
        }
        self.steps += 1;
        if self.steps >= RESUM && self.ring.full() && self.nans == 0 {
            self.steps = 0;
            let n = self.ring.cap() as f64;
            self.k = self.ring.iter().sum::<f64>() / n;
            let k = self.k;
            self.sum = self.ring.iter().map(|v| v - k).sum();
            self.sq = self.ring.iter().map(|v| (v - k) * (v - k)).sum();
        }
    }
    fn ready(&self) -> bool {
        self.ring.full() && self.nans == 0
    }
    fn total(&self) -> f64 {
        self.sum + self.k * self.ring.cap() as f64
    }
    fn mean(&self) -> f64 {
        self.k + self.sum / self.ring.cap() as f64
    }
    /// population variance (TA-Lib STDDEV / BBANDS)
    fn var(&self) -> f64 {
        let n = self.ring.cap() as f64;
        let m = self.sum / n;
        (self.sq / n - m * m).max(0.0)
    }
}

/// Rolling max or min over the last n values (monotonic deque), positional, NaN-aware.
#[derive(Clone)]
struct Extreme {
    n: usize,
    i: usize,
    dq: VecDeque<(usize, f64)>,
    nan_until: usize,
    max: bool,
}

impl Extreme {
    fn new(n: usize, max: bool) -> Self {
        Extreme { n, i: 0, dq: VecDeque::new(), nan_until: 0, max }
    }
    fn push(&mut self, x: f64) -> f64 {
        let i = self.i;
        self.i += 1;
        if x.is_nan() {
            self.nan_until = i + self.n;
        } else {
            while let Some(&(_, v)) = self.dq.back() {
                if (self.max && v <= x) || (!self.max && v >= x) {
                    self.dq.pop_back();
                } else {
                    break;
                }
            }
            self.dq.push_back((i, x));
        }
        while let Some(&(j, _)) = self.dq.front() {
            if j + self.n <= i {
                self.dq.pop_front();
            } else {
                break;
            }
        }
        if self.i < self.n || i < self.nan_until { NAN } else { self.dq.front().map(|e| e.1).unwrap_or(NAN) }
    }
}

/// TA-Lib EMA: seeded with the SMA of the first n valid inputs.
#[derive(Clone)]
struct Ema {
    n: usize,
    k: f64,
    cnt: usize,
    sum: f64,
    v: f64,
}

impl Ema {
    fn new(n: usize) -> Self {
        Ema { n, k: 2.0 / (n as f64 + 1.0), cnt: 0, sum: 0.0, v: NAN }
    }
    fn step(&mut self, x: f64) -> f64 {
        if x.is_nan() {
            return NAN;
        }
        if self.cnt < self.n {
            self.cnt += 1;
            self.sum += x;
            if self.cnt == self.n {
                self.v = self.sum / self.n as f64;
                return self.v;
            }
            return NAN;
        }
        self.v += self.k * (x - self.v);
        self.v
    }
}

/// SMA with TA-Lib leading-NaN skip.
#[derive(Clone)]
struct Sma {
    w: Win,
}

impl Sma {
    fn new(n: usize) -> Self {
        Sma { w: Win::new(n) }
    }
    fn step(&mut self, x: f64) -> f64 {
        if x.is_nan() {
            return NAN;
        }
        self.w.push(x);
        if self.w.ring.full() { self.w.mean() } else { NAN }
    }
}

/// WMA (weights 1..n, newest heaviest), leading-NaN skip.
#[derive(Clone)]
struct Wma {
    ring: Ring,
}

impl Wma {
    fn new(n: usize) -> Self {
        Wma { ring: Ring::new(n) }
    }
    fn step(&mut self, x: f64) -> f64 {
        if x.is_nan() {
            return NAN;
        }
        self.ring.push(x);
        if !self.ring.full() {
            return NAN;
        }
        let n = self.ring.cap();
        let mut num = 0.0;
        for k in 0..n {
            num += (n - k) as f64 * self.ring.ago(k);
        }
        num / (n * (n + 1) / 2) as f64
    }
}

/// Wilder-smoothed RSI (TA-Lib: seed = mean of the first n changes).
#[derive(Clone)]
struct Rsi {
    n: usize,
    prev: f64,
    k: usize,
    up: f64,
    dn: f64,
}

impl Rsi {
    fn step(&mut self, x: f64) -> f64 {
        if x.is_nan() {
            return NAN;
        }
        if self.prev.is_nan() {
            self.prev = x;
            return NAN;
        }
        let d = x - self.prev;
        self.prev = x;
        self.k += 1;
        let (u, v) = (d.max(0.0), (-d).max(0.0));
        let n = self.n as f64;
        if self.k < self.n {
            self.up += u;
            self.dn += v;
            return NAN;
        }
        if self.k == self.n {
            self.up = (self.up + u) / n;
            self.dn = (self.dn + v) / n;
        } else {
            self.up = (self.up * (n - 1.0) + u) / n;
            self.dn = (self.dn * (n - 1.0) + v) / n;
        }
        let s = self.up + self.dn;
        if s == 0.0 { 0.0 } else { 100.0 * self.up / s }
    }
}

/// Wilder ATR (TA-Lib: first value = mean of TR[1..n] at index n).
#[derive(Clone)]
struct Atr {
    n: usize,
    pc: f64,
    k: usize,
    sum: f64,
    v: f64,
}

impl Atr {
    fn new(n: usize) -> Self {
        Atr { n, pc: NAN, k: 0, sum: 0.0, v: NAN }
    }
    fn step(&mut self, h: f64, l: f64, c: f64) -> f64 {
        if self.pc.is_nan() {
            self.pc = c;
            return NAN;
        }
        let tr = (h - l).max((h - self.pc).abs()).max((l - self.pc).abs());
        self.pc = c;
        self.k += 1;
        let n = self.n as f64;
        if self.k < self.n {
            self.sum += tr;
            return NAN;
        }
        if self.k == self.n {
            self.v = (self.sum + tr) / n;
            return self.v;
        }
        self.v = (self.v * (n - 1.0) + tr) / n;
        self.v
    }
}

#[inline]
fn ta_zero(x: f64) -> bool {
    -0.000_000_01 < x && x < 0.000_000_01
}

/// TA-Lib ADX / PLUS_DI / MINUS_DI.
#[derive(Clone)]
struct Adx {
    n: usize,
    k: usize,
    ph: f64,
    pl: f64,
    pc: f64,
    pdm: f64,
    mdm: f64,
    tr: f64,
    sum_dx: f64,
    adx: f64,
}

impl Adx {
    fn new(n: usize) -> Self {
        Adx { n, k: 0, ph: NAN, pl: NAN, pc: NAN, pdm: 0.0, mdm: 0.0, tr: 0.0, sum_dx: 0.0, adx: NAN }
    }
    /// -> (adx, plus_di, minus_di)
    fn step(&mut self, h: f64, l: f64, c: f64) -> [f64; 3] {
        if self.ph.is_nan() {
            (self.ph, self.pl, self.pc) = (h, l, c);
            return [NAN; 3];
        }
        self.k += 1;
        let n = self.n as f64;
        let dp = h - self.ph;
        let dm = self.pl - l;
        let tr = (h - l).max((h - self.pc).abs()).max((l - self.pc).abs());
        (self.ph, self.pl, self.pc) = (h, l, c);
        if self.k < self.n {
            if dm > 0.0 && dp < dm {
                self.mdm += dm;
            } else if dp > 0.0 && dp > dm {
                self.pdm += dp;
            }
            self.tr += tr;
            return [NAN; 3];
        }
        self.mdm -= self.mdm / n;
        self.pdm -= self.pdm / n;
        if dm > 0.0 && dp < dm {
            self.mdm += dm;
        } else if dp > 0.0 && dp > dm {
            self.pdm += dp;
        }
        self.tr = self.tr - self.tr / n + tr;
        let (mut pdi, mut mdi, mut dx) = (0.0, 0.0, NAN);
        if !ta_zero(self.tr) {
            mdi = 100.0 * (self.mdm / self.tr);
            pdi = 100.0 * (self.pdm / self.tr);
            let s = mdi + pdi;
            if !ta_zero(s) {
                dx = 100.0 * ((mdi - pdi).abs() / s);
            }
        }
        let j = self.k - self.n; // 0 at the first smoothed bar (index n)
        if j < self.n {
            if !dx.is_nan() {
                self.sum_dx += dx;
            }
            if j + 1 == self.n {
                self.adx = self.sum_dx / n;
                return [self.adx, pdi, mdi];
            }
            return [NAN, pdi, mdi];
        }
        if !dx.is_nan() {
            self.adx = (self.adx * (n - 1.0) + dx) / n;
        }
        [self.adx, pdi, mdi]
    }
}

#[derive(Clone)]
enum K {
    Const(f64),
    Col(Column),
    Pure(Op),
    Shift(Ring),
    Diff(Ring, bool),
    Roll(Win, Op),
    RollExt(Extreme),
    Quantile(Ring, f64, usize),
    Expanding(f64, bool),
    Cumsum(f64),
    BarsSince(f64),
    Rising(Ring, bool),
    Cross(f64, f64, bool),
    Sma(Sma),
    Ema(Ema),
    Wma(Wma),
    Dema(Ema, Ema),
    Tema(Ema, Ema, Ema),
    Hma(Wma, Wma, Wma),
    Rsi(Rsi),
    RocMom(Ring, bool),
    Stddev(Win),
    Zscore(Win),
    Linreg(Ring),
    Bbands(Win, f64),
    Macd { fast: Ema, slow: Ema, sig: Ema, skip: usize, seen: usize },
    Atr(Atr, bool),
    Cci(Ring),
    Willr(Extreme, Extreme),
    Mfi { n: usize, prev_tp: f64, flows: Ring, seen: usize },
    Stoch { hh: Extreme, ll: Extreme, k: Sma, d: Sma, lookback: usize, i: usize },
    Adx(Adx),
    Keltner(Ema, Atr, f64),
    Donchian(Extreme, Extreme),
    Obv(f64, f64),
    Vwap(f64, f64),
    Session(Op, SessionState),
    Position(Op),
}

#[derive(Clone, Default)]
pub struct SessionState {
    bar: f64,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    prior: [f64; 4],
    or_hi: f64,
    or_lo: f64,
    started: bool,
}

/// A streaming kernel (opaque state machine of one IR node).
#[derive(Clone)]
pub struct Kernel(K);

impl Kernel {
    pub fn new(n: &NodeSpec) -> Kernel {
        Kernel(K::new(n))
    }
    /// One bar. `a` = the node's series inputs; `ip` = integer params (opening range minutes); outputs to `out`.
    #[inline]
    pub fn step(&mut self, a: &[f64], ip: &[i64], bar: &BarIn, pos: &PosIn, out: &mut [f64; 3]) {
        self.0.step(a, ip, bar, pos, out)
    }
}

impl K {
    fn new(n: &NodeSpec) -> K {
        let p = |i: usize| n.ip.get(i).copied().unwrap_or(1).max(1) as usize;
        let f = |i: usize| n.fp.get(i).copied().unwrap_or(0.0);
        match n.op {
            Op::Const => K::Const(f(0)),
            Op::Col(c) => K::Col(c),
            Op::Shift => K::Shift(Ring::new(p(0) + 1)),
            Op::Diff => K::Diff(Ring::new(p(0) + 1), false),
            Op::PctChange => K::Diff(Ring::new(p(0) + 1), true),
            Op::RollingMean | Op::RollingSum | Op::RollingStd => K::Roll(Win::new(p(0)), n.op),
            Op::RollingMin => K::RollExt(Extreme::new(p(0), false)),
            Op::RollingMax => K::RollExt(Extreme::new(p(0), true)),
            Op::RollingQuantile => K::Quantile(Ring::new(p(0)), f(0), 0),
            Op::ExpandingMax => K::Expanding(NAN, true),
            Op::ExpandingMin => K::Expanding(NAN, false),
            Op::Cumsum => K::Cumsum(0.0),
            Op::BarsSince => K::BarsSince(NAN),
            Op::Rising => K::Rising(Ring::new(p(0) + 1), true),
            Op::Falling => K::Rising(Ring::new(p(0) + 1), false),
            Op::CrossAbove => K::Cross(NAN, NAN, true),
            Op::CrossBelow => K::Cross(NAN, NAN, false),
            Op::Sma => K::Sma(Sma::new(p(0))),
            Op::Ema => K::Ema(Ema::new(p(0))),
            Op::Wma => K::Wma(Wma::new(p(0))),
            Op::Dema => K::Dema(Ema::new(p(0)), Ema::new(p(0))),
            Op::Tema => K::Tema(Ema::new(p(0)), Ema::new(p(0)), Ema::new(p(0))),
            Op::Hma => {
                let half = (p(0) / 2).max(1);
                let root = ((p(0) as f64).sqrt().floor() as usize).max(1);
                K::Hma(Wma::new(half), Wma::new(p(0)), Wma::new(root))
            }
            Op::Rsi => K::Rsi(Rsi { n: p(0), prev: NAN, k: 0, up: 0.0, dn: 0.0 }),
            Op::Roc => K::RocMom(Ring::new(p(0) + 1), true),
            Op::Mom => K::RocMom(Ring::new(p(0) + 1), false),
            Op::Stddev => K::Stddev(Win::new(p(0))),
            Op::Zscore => K::Zscore(Win::new(p(0))),
            Op::LinregSlope => K::Linreg(Ring::new(p(0))),
            Op::Bbands => K::Bbands(Win::new(p(0)), f(0)),
            Op::Macd => {
                let (mut fast, mut slow) = (p(0), p(1));
                if slow < fast {
                    std::mem::swap(&mut fast, &mut slow);
                }
                K::Macd { fast: Ema::new(fast), slow: Ema::new(slow), sig: Ema::new(p(2)), skip: slow - fast, seen: 0 }
            }
            Op::Atr => K::Atr(Atr::new(p(0)), false),
            Op::Natr => K::Atr(Atr::new(p(0)), true),
            Op::Cci => K::Cci(Ring::new(p(0))),
            Op::Willr => K::Willr(Extreme::new(p(0), true), Extreme::new(p(0), false)),
            Op::Mfi => K::Mfi { n: p(0), prev_tp: NAN, flows: Ring::new(p(0)), seen: 0 },
            Op::Stoch => K::Stoch {
                hh: Extreme::new(p(0), true),
                ll: Extreme::new(p(0), false),
                k: Sma::new(p(1)),
                d: Sma::new(p(2)),
                lookback: p(0) - 1 + p(1) - 1 + p(2) - 1,
                i: 0,
            },
            Op::Adx => K::Adx(Adx::new(p(0))),
            Op::Keltner => K::Keltner(Ema::new(p(0)), Atr::new(p(0)), f(0)),
            Op::Donchian => K::Donchian(Extreme::new(p(0), true), Extreme::new(p(0), false)),
            Op::Obv => K::Obv(NAN, NAN),
            Op::Vwap => K::Vwap(0.0, 0.0),
            Op::PositionQty
            | Op::EntryPrice
            | Op::BarsInTrade
            | Op::UnrealisedPct
            | Op::HighestSinceEntry
            | Op::LowestSinceEntry => K::Position(n.op),
            op if hbt_ir::ops::def_of(op).map(|d| d.class == hbt_ir::ops::Class::Session).unwrap_or(false) => {
                K::Session(op, SessionState { prior: [NAN; 4], ..Default::default() })
            }
            op => K::Pure(op),
        }
    }

    #[inline]
    fn step(&mut self, a: &[f64], ip: &[i64], bar: &BarIn, pos: &PosIn, out: &mut [f64; 3]) {
        let x = a.first().copied().unwrap_or(NAN);
        out[0] = match self {
            K::Const(v) => *v,
            K::Col(c) => match c {
                Column::Open => bar.o,
                Column::High => bar.h,
                Column::Low => bar.l,
                Column::Close => bar.c,
                Column::Volume => bar.v,
                Column::Typical => (bar.h + bar.l + bar.c) / 3.0,
                Column::Hl2 => (bar.h + bar.l) / 2.0,
                Column::Extra(i) => bar.extras.get(*i as usize).copied().unwrap_or(NAN),
            },
            K::Pure(op) => pure(*op, a),
            K::Shift(r) => {
                r.push(x);
                if r.full() { r.ago(r.cap() - 1) } else { NAN }
            }
            K::Diff(r, pct) => {
                r.push(x);
                if !r.full() {
                    NAN
                } else {
                    let old = r.ago(r.cap() - 1);
                    if *pct { x / old - 1.0 } else { x - old }
                }
            }
            K::Roll(w, op) => {
                w.push(x);
                if !w.ready() {
                    NAN
                } else {
                    match op {
                        Op::RollingMean => w.mean(),
                        Op::RollingSum => w.total(),
                        _ => w.var().sqrt(),
                    }
                }
            }
            K::RollExt(e) => e.push(x),
            K::Quantile(r, q, nans) => {
                if let Some(old) = r.push(x)
                    && old.is_nan()
                {
                    *nans -= 1;
                }
                if x.is_nan() {
                    *nans += 1;
                }
                if !r.full() || *nans > 0 {
                    NAN
                } else {
                    let mut v: Vec<f64> = r.iter().collect();
                    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
                    let k = ((*q * v.len() as f64).ceil() as usize).clamp(1, v.len()) - 1;
                    v[k]
                }
            }
            K::Expanding(m, max) => {
                if !x.is_nan() && (m.is_nan() || (*max && x > *m) || (!*max && x < *m)) {
                    *m = x;
                }
                *m
            }
            K::Cumsum(s) => {
                if x.is_nan() {
                    NAN
                } else {
                    *s += x;
                    *s
                }
            }
            K::BarsSince(c) => {
                if truthy(x) {
                    *c = 0.0;
                } else if !c.is_nan() {
                    *c += 1.0;
                }
                *c
            }
            K::Rising(r, up) => {
                r.push(x);
                if !r.full() {
                    0.0
                } else {
                    let old = r.ago(r.cap() - 1);
                    b(if *up { x > old } else { x < old })
                }
            }
            K::Cross(pa, pb, above) => {
                let y = a.get(1).copied().unwrap_or(NAN);
                let r = if *above { x > y && *pa <= *pb } else { x < y && *pa >= *pb };
                let ok = !(x.is_nan() || y.is_nan() || pa.is_nan() || pb.is_nan());
                (*pa, *pb) = (x, y);
                b(ok && r)
            }
            K::Sma(s) => s.step(x),
            K::Ema(e) => e.step(x),
            K::Wma(w) => w.step(x),
            K::Dema(e1, e2) => {
                let v1 = e1.step(x);
                let v2 = e2.step(v1);
                if v2.is_nan() { NAN } else { 2.0 * v1 - v2 }
            }
            K::Tema(e1, e2, e3) => {
                let v1 = e1.step(x);
                let v2 = e2.step(v1);
                let v3 = e3.step(v2);
                if v3.is_nan() { NAN } else { 3.0 * v1 - 3.0 * v2 + v3 }
            }
            K::Hma(half, full, root) => {
                let h = half.step(x);
                let f = full.step(x);
                if h.is_nan() || f.is_nan() { root.step(NAN) } else { root.step(2.0 * h - f) }
            }
            K::Rsi(r) => r.step(x),
            K::RocMom(r, roc) => {
                if x.is_nan() {
                    NAN
                } else {
                    r.push(x);
                    if !r.full() {
                        NAN
                    } else {
                        let old = r.ago(r.cap() - 1);
                        if *roc { if old == 0.0 { 0.0 } else { (x / old - 1.0) * 100.0 } } else { x - old }
                    }
                }
            }
            K::Stddev(w) => {
                if x.is_nan() {
                    NAN
                } else {
                    w.push(x);
                    if w.ready() { w.var().sqrt() } else { NAN }
                }
            }
            K::Zscore(w) => {
                if x.is_nan() {
                    NAN
                } else {
                    w.push(x);
                    let sd = w.var().sqrt();
                    if !w.ready() || sd == 0.0 { NAN } else { (x - w.mean()) / sd }
                }
            }
            K::Linreg(r) => {
                if x.is_nan() {
                    NAN
                } else {
                    r.push(x);
                    if !r.full() {
                        NAN
                    } else {
                        // TA-Lib LINEARREG_SLOPE (x counts back from today)
                        let n = r.cap() as f64;
                        let sum_x = n * (n - 1.0) * 0.5;
                        let sum_x_sq = n * (n - 1.0) * (2.0 * n - 1.0) / 6.0;
                        let div = sum_x * sum_x - n * sum_x_sq;
                        let (mut sxy, mut sy) = (0.0, 0.0);
                        for k in 0..r.cap() {
                            let y = r.ago(k);
                            sxy += k as f64 * y;
                            sy += y;
                        }
                        (n * sxy - sum_x * sy) / div
                    }
                }
            }
            K::Bbands(w, k) => {
                if x.is_nan() {
                    out[1] = NAN;
                    out[2] = NAN;
                    NAN
                } else {
                    w.push(x);
                    if w.ready() {
                        let (m, sd) = (w.mean(), w.var().sqrt());
                        out[1] = m;
                        out[2] = m - *k * sd;
                        m + *k * sd
                    } else {
                        out[1] = NAN;
                        out[2] = NAN;
                        NAN
                    }
                }
            }
            K::Macd { fast, slow, sig, skip, seen } => {
                out[1] = NAN;
                out[2] = NAN;
                if x.is_nan() {
                    NAN
                } else {
                    // TA-Lib seeds the fast EMA late so that both EMAs start at index slow - 1
                    let i = *seen;
                    *seen += 1;
                    let f = if i >= *skip { fast.step(x) } else { NAN };
                    let s = slow.step(x);
                    if f.is_nan() || s.is_nan() {
                        NAN
                    } else {
                        let m = f - s;
                        let g = sig.step(m);
                        if g.is_nan() {
                            NAN
                        } else {
                            out[1] = g;
                            out[2] = m - g;
                            m
                        }
                    }
                }
            }
            K::Atr(a, natr) => {
                let v = a.step(bar.h, bar.l, bar.c);
                if *natr {
                    if v.is_nan() {
                        NAN
                    } else if bar.c == 0.0 {
                        0.0
                    } else {
                        v / bar.c * 100.0
                    }
                } else {
                    v
                }
            }
            K::Cci(r) => {
                let tp = (bar.h + bar.l + bar.c) / 3.0;
                r.push(tp);
                if !r.full() {
                    NAN
                } else {
                    let n = r.cap() as f64;
                    let avg = r.iter().sum::<f64>() / n;
                    let md = r.iter().map(|v| (v - avg).abs()).sum::<f64>() / n;
                    if md == 0.0 { 0.0 } else { (tp - avg) / (0.015 * md) }
                }
            }
            K::Willr(hh, ll) => {
                let (h, l) = (hh.push(bar.h), ll.push(bar.l));
                if h.is_nan() || l.is_nan() {
                    NAN
                } else if h - l == 0.0 {
                    0.0
                } else {
                    (h - bar.c) / (h - l) * -100.0
                }
            }
            K::Mfi { n, prev_tp, flows, seen } => {
                let tp = (bar.h + bar.l + bar.c) / 3.0;
                if prev_tp.is_nan() {
                    *prev_tp = tp;
                    NAN
                } else {
                    let mf = tp * bar.v;
                    let f = if tp > *prev_tp {
                        mf
                    } else if tp < *prev_tp {
                        -mf
                    } else {
                        0.0
                    };
                    *prev_tp = tp;
                    flows.push(f);
                    *seen += 1;
                    if *seen < *n {
                        NAN
                    } else {
                        let pos: f64 = flows.iter().filter(|v| *v > 0.0).sum();
                        let neg: f64 = -flows.iter().filter(|v| *v < 0.0).sum::<f64>();
                        if pos + neg < 1.0 { 0.0 } else { 100.0 * pos / (pos + neg) }
                    }
                }
            }
            K::Stoch { hh, ll, k, d, lookback, i } => {
                let (h, l) = (hh.push(bar.h), ll.push(bar.l));
                let fastk = if h.is_nan() || l.is_nan() {
                    NAN
                } else if h - l == 0.0 {
                    0.0
                } else {
                    (bar.c - l) / (h - l) * 100.0
                };
                let sk = k.step(fastk);
                let sd = d.step(sk);
                let ready = *i >= *lookback;
                *i += 1;
                if ready {
                    out[1] = sd;
                    sk
                } else {
                    out[1] = NAN;
                    NAN
                }
            }
            K::Adx(a) => {
                let r = a.step(bar.h, bar.l, bar.c);
                out[1] = r[1];
                out[2] = r[2];
                r[0]
            }
            K::Keltner(e, at, k) => {
                let m = e.step(bar.c);
                let v = at.step(bar.h, bar.l, bar.c);
                if m.is_nan() || v.is_nan() {
                    out[1] = NAN;
                    out[2] = NAN;
                    NAN
                } else {
                    out[1] = m;
                    out[2] = m - *k * v;
                    m + *k * v
                }
            }
            K::Donchian(hh, ll) => {
                out[1] = ll.push(bar.l);
                hh.push(bar.h)
            }
            K::Obv(prev, acc) => {
                if prev.is_nan() {
                    *acc = bar.v;
                } else if bar.c > *prev {
                    *acc += bar.v;
                } else if bar.c < *prev {
                    *acc -= bar.v;
                }
                *prev = bar.c;
                *acc
            }
            K::Vwap(pv, vol) => {
                if bar.new_session {
                    (*pv, *vol) = (0.0, 0.0);
                }
                let tp = (bar.h + bar.l + bar.c) / 3.0;
                *pv += tp * bar.v;
                *vol += bar.v;
                if *vol > 0.0 { *pv / *vol } else { tp }
            }
            K::Session(op, s) => session(*op, s, bar, ip),
            K::Position(op) => match op {
                Op::PositionQty => pos.qty,
                Op::EntryPrice => pos.entry,
                Op::BarsInTrade => pos.bars_in_trade,
                Op::UnrealisedPct => pos.unrealised_pct,
                Op::HighestSinceEntry => pos.highest,
                _ => pos.lowest,
            },
        };
    }
}

#[inline]
fn pure(op: Op, a: &[f64]) -> f64 {
    let x = a.first().copied().unwrap_or(NAN);
    let y = a.get(1).copied().unwrap_or(NAN);
    match op {
        Op::Add => x + y,
        Op::Sub => x - y,
        Op::Mul => x * y,
        Op::Div => x / y,
        Op::Min => {
            if x.is_nan() || y.is_nan() {
                NAN
            } else {
                x.min(y)
            }
        }
        Op::Max => {
            if x.is_nan() || y.is_nan() {
                NAN
            } else {
                x.max(y)
            }
        }
        Op::Pow => x.powf(y),
        Op::Neg => -x,
        Op::Abs => x.abs(),
        Op::Log => x.ln(),
        Op::Exp => x.exp(),
        Op::Sqrt => x.sqrt(),
        Op::Clip => {
            let z = a.get(2).copied().unwrap_or(NAN);
            if x.is_nan() { NAN } else { x.max(y).min(z) }
        }
        Op::Where => {
            if truthy(x) {
                y
            } else {
                a.get(2).copied().unwrap_or(NAN)
            }
        }
        Op::Gt => b(x > y),
        Op::Ge => b(x >= y),
        Op::Lt => b(x < y),
        Op::Le => b(x <= y),
        Op::Eq => b(x == y),
        Op::Ne => b(!x.is_nan() && !y.is_nan() && x != y),
        Op::Between => {
            let z = a.get(2).copied().unwrap_or(NAN);
            b(x >= y && x <= z)
        }
        Op::And => b(a.iter().all(|v| truthy(*v))),
        Op::Or => b(a.iter().any(|v| truthy(*v))),
        Op::Not => b(!truthy(x)),
        _ => NAN,
    }
}

fn session(op: Op, s: &mut SessionState, bar: &BarIn, ip: &[i64]) -> f64 {
    if bar.new_session || !s.started {
        if s.started {
            s.prior = [s.open, s.high, s.low, s.close];
        }
        s.started = true;
        s.bar = 0.0;
        s.open = bar.o;
        s.high = bar.h;
        s.low = bar.l;
        s.or_hi = f64::NEG_INFINITY;
        s.or_lo = f64::INFINITY;
    } else {
        s.bar += 1.0;
        s.high = s.high.max(bar.h);
        s.low = s.low.min(bar.l);
    }
    s.close = bar.c;
    let m = ip.first().copied().unwrap_or(0) as i32;
    if bar.minute < bar.sess_open + m {
        s.or_hi = s.or_hi.max(bar.h);
        s.or_lo = s.or_lo.min(bar.l);
    }
    let or_done = bar.minute + bar.bar_mins >= bar.sess_open + m && s.or_hi.is_finite();
    match op {
        Op::MinuteOfDay => bar.minute as f64,
        Op::BarInSession => s.bar,
        Op::IsFirstBar => b(s.bar == 0.0),
        Op::IsLastBar => b(bar.minute + bar.bar_mins >= bar.sess_close),
        Op::SessionOpenPrice => s.open,
        Op::SessionHigh => s.high,
        Op::SessionLow => s.low,
        Op::OpeningRangeHigh => {
            if or_done {
                s.or_hi
            } else {
                NAN
            }
        }
        Op::OpeningRangeLow => {
            if or_done {
                s.or_lo
            } else {
                NAN
            }
        }
        Op::PriorDayOpen => s.prior[0],
        Op::PriorDayHigh => s.prior[1],
        Op::PriorDayLow => s.prior[2],
        Op::PriorDayClose => s.prior[3],
        Op::DayOfWeek => ((bar.day + 3).rem_euclid(7)) as f64,
        Op::Vwap => NAN,
        _ => NAN,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use proptest::prelude::*;

    proptest! {
        /// the shifted running sums stay within 1e-9 (relative to the data scale) of an exact two-pass computation
        /// over long price-like series, NaNs included
        #[test]
        fn windowed_moments_match_two_pass(n in 2usize..60, len in 100usize..3000, seed in 0u64..1000,
                                          level in 1.0f64..100_000.0) {
            let mut w = Win::new(n);
            let mut x = level;
            let mut s = seed;
            let mut hist: Vec<f64> = vec![];
            for i in 0..len {
                s = s.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
                x *= 1.0 + (((s >> 33) as f64 / (1u64 << 31) as f64) - 0.5) * 0.01;
                let v = if i % 997 == 13 { f64::NAN } else { x };
                w.push(v);
                hist.push(v);
                if w.ready() {
                    let win = &hist[hist.len() - n..];
                    let m = win.iter().sum::<f64>() / n as f64;
                    let var = win.iter().map(|v| (v - m) * (v - m)).sum::<f64>() / n as f64;
                    prop_assert!((w.mean() - m).abs() <= 1e-9 * level.max(1.0));
                    prop_assert!((w.var() - var).abs() <= 1e-9 * level * level);
                }
            }
        }

        /// rolling extremes agree with a brute-force window scan
        #[test]
        fn rolling_extremes_match_a_scan(n in 1usize..40, xs in proptest::collection::vec(-1e6f64..1e6, 1..400)) {
            let (mut hi, mut lo) = (Extreme::new(n, true), Extreme::new(n, false));
            for i in 0..xs.len() {
                let (h, l) = (hi.push(xs[i]), lo.push(xs[i]));
                if i + 1 >= n {
                    let w = &xs[i + 1 - n..=i];
                    prop_assert_eq!(h, w.iter().cloned().fold(f64::MIN, f64::max));
                    prop_assert_eq!(l, w.iter().cloned().fold(f64::MAX, f64::min));
                } else {
                    prop_assert!(h.is_nan() && l.is_nan());
                }
            }
        }
    }
}
