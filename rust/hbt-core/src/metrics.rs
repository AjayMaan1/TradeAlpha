//! Performance statistics from an equity curve (paise per bar close) and closed trades. Annualisation takes
//! `periods_per_year` from the caller (exchange calendar x bars per session - never a 365-day default).
//!
//! `Acc` is the single implementation: `compute()` feeds it the stored equity curve and trades; sweeps feed it bar by
//! bar inside the engine (fused accumulators, no equity / fill vectors: design §21, T1.6.1). Same arithmetic, same
//! order -> identical results.

use crate::engine::{Outcome, Trade};

#[derive(Debug, Default, Clone, Copy)]
pub struct Metrics {
    pub total_return: f64,
    pub cagr: f64,
    pub ann_vol: f64,
    pub sharpe: f64,
    pub sortino: f64,
    pub max_drawdown: f64,
    pub max_dd_bars: u32,
    pub calmar: f64,
    pub trades: u32,
    pub win_rate: f64,
    pub profit_factor: f64,
    pub expectancy_paise: f64,
    pub avg_bars_held: f64,
    pub exposure: f64,
    pub fees_paise: i64,
}

/// Streaming accumulator of the metrics.
#[derive(Debug, Clone, Copy)]
pub struct Acc {
    cash0: f64,
    n: f64,
    last: f64,
    prev: f64,
    mean: f64,
    m2: f64,
    down2: f64,
    peak: f64,
    dd: f64,
    dd_bars: u32,
    cur_dd_bars: u32,
    trades: u32,
    wins: u32,
    gross_win: i64,
    gross_loss: i64,
    pnl_sum: f64,
    held_sum: f64,
}

impl Acc {
    pub fn new(cash0: i64) -> Self {
        let c = cash0 as f64;
        Acc {
            cash0: c,
            n: 0.0,
            last: c,
            prev: c,
            mean: 0.0,
            m2: 0.0,
            down2: 0.0,
            peak: c,
            dd: 0.0,
            dd_bars: 0,
            cur_dd_bars: 0,
            trades: 0,
            wins: 0,
            gross_win: 0,
            gross_loss: 0,
            pnl_sum: 0.0,
            held_sum: 0.0,
        }
    }

    /// equity (paise) at a bar close
    #[inline]
    pub fn bar(&mut self, equity: i64) {
        let e = equity as f64;
        let r = if self.prev != 0.0 { e / self.prev - 1.0 } else { 0.0 };
        self.prev = e;
        self.last = e;
        self.n += 1.0;
        let d = r - self.mean;
        self.mean += d / self.n;
        self.m2 += d * (r - self.mean);
        if r < 0.0 {
            self.down2 += r * r;
        }
        if e >= self.peak {
            self.peak = e;
            self.cur_dd_bars = 0;
        } else {
            self.cur_dd_bars += 1;
            self.dd_bars = self.dd_bars.max(self.cur_dd_bars);
            self.dd = self.dd.max(1.0 - e / self.peak);
        }
    }

    #[inline]
    pub fn trade(&mut self, t: &Trade) {
        self.trades += 1;
        if t.pnl > 0 {
            self.wins += 1;
            self.gross_win += t.pnl;
        } else if t.pnl < 0 {
            self.gross_loss -= t.pnl;
        }
        self.pnl_sum += t.pnl as f64;
        self.held_sum += (t.exit_bar - t.entry_bar) as f64;
    }

    pub fn finish(&self, periods_per_year: f64, fees_paise: i64, bars_in_market: u32) -> Metrics {
        let mut m = Metrics { fees_paise, ..Default::default() };
        if self.n == 0.0 || self.cash0 <= 0.0 {
            return m;
        }
        m.total_return = self.last / self.cash0 - 1.0;
        let years = self.n / periods_per_year.max(1.0);
        m.cagr = if self.last > 0.0 && years > 0.0 { (self.last / self.cash0).powf(1.0 / years) - 1.0 } else { -1.0 };
        let sd = if self.n > 1.0 { (self.m2 / (self.n - 1.0)).sqrt() } else { 0.0 };
        let dsd = (self.down2 / self.n).sqrt();
        let ann = periods_per_year.sqrt();
        m.ann_vol = sd * ann;
        m.sharpe = if sd > 0.0 { self.mean / sd * ann } else { 0.0 };
        m.sortino = if dsd > 0.0 { self.mean / dsd * ann } else { 0.0 };
        m.max_drawdown = self.dd;
        m.max_dd_bars = self.dd_bars;
        m.calmar = if self.dd > 0.0 { m.cagr / self.dd } else { 0.0 };
        m.trades = self.trades;
        if self.trades > 0 {
            let k = self.trades as f64;
            m.win_rate = self.wins as f64 / k;
            m.profit_factor = if self.gross_loss > 0 {
                self.gross_win as f64 / self.gross_loss as f64
            } else if self.gross_win > 0 {
                f64::INFINITY
            } else {
                0.0
            };
            m.expectancy_paise = self.pnl_sum / k;
            m.avg_bars_held = self.held_sum / k;
        }
        m.exposure = bars_in_market as f64 / self.n;
        m
    }
}

pub fn compute(out: &Outcome, cash0: i64, periods_per_year: f64) -> Metrics {
    if let Some(m) = out.fused {
        return m.finish(periods_per_year, out.charges.total(), out.bars_in_market);
    }
    let mut a = Acc::new(cash0);
    for &e in &out.equity {
        a.bar(e);
    }
    for t in &out.trades {
        a.trade(t);
    }
    a.finish(periods_per_year, out.charges.total(), out.bars_in_market)
}
