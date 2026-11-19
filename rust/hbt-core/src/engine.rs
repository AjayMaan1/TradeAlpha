//! Bar-by-bar execution engine (design §10). One instrument, netting, long and short, fixed-point money.
//!
//! Every coarse bar is processed as a sequence of SEGMENTS: its stored 1m bars when they are supplied and needed
//! (1m magnifier, decision latency), otherwise the bar itself. Inside each segment the phases run in this order:
//!
//!   P0 session boundary   (first segment of a new trading day) corporate-action events of the ex-date applied to the
//!                         position and orders; resting DAY entries of an earlier session cancelled; an MIS position
//!                         still open (no square-off bar in the data) is closed at this open ("forced")
//!   P1 segment open       MIS square-off -> pending exits -> pending market entries (bracket attached at the fill)
//!                         -> resting entries become live -> gap checks (protective orders, resting entries)
//!   P2 intrabar           resting entries trigger; protective stop / target by the intrabar policy
//!                         (bracket live on the entry bar by default)
//!   E  decide             the DecisionSource sees the position after P2 and returns intents
//!   P3 coarse bar close   trailing stop ratchets on the close; time stop; intents -> orders that reach the market at
//!                         close + decision latency (fill_timing next_open) or fill at the close (fill_timing close)
//!   P4 mark-to-market     equity = cash + position x close
//!
//! Fill prices are rounded to the tick in the adverse direction; charges are computed per fill per charge in paise.

use crate::costs::{Charges, CostModel, Side};
use crate::decision::{BarCtx, DecisionSource, Intent, IntentKind, Level, OrderType, Ov, PosState, Sizing};
use crate::money::{Paise, notional, round_to_tick};
use crate::spec::{
    Dividends, ExecSpec, FillTiming, Instrument, IntrabarPolicy, OrderPlan, Product, Remainder, SameBarPriority,
    SlippageModel,
};

/// Bars of one instrument. Prices in price units (see `Instrument.price_scale`).
pub struct Bars<'a> {
    pub ts: &'a [i64],
    pub open: &'a [i64],
    pub high: &'a [i64],
    pub low: &'a [i64],
    pub close: &'a [i64],
    pub volume: &'a [i64],
    /// IST trading day number (days since 1970-01-01) of each bar
    pub day: &'a [i64],
    /// IST minute of day of each bar's OPEN
    pub minute: &'a [i32],
    pub bar_secs: i64,
}

/// 2000-01-01 and 2100-01-01 (epoch seconds): anything outside is a unit mistake (ms / ns / days), audit 2 A2-M3
pub const TS_MIN: i64 = 946_684_800;
pub const TS_MAX: i64 = 4_102_444_800;

impl Bars<'_> {
    pub fn len(&self) -> usize {
        self.ts.len()
    }
    pub fn is_empty(&self) -> bool {
        self.ts.is_empty()
    }
    pub fn check(&self) -> Result<(), String> {
        let n = self.ts.len();
        for (name, l) in [
            ("open", self.open.len()),
            ("high", self.high.len()),
            ("low", self.low.len()),
            ("close", self.close.len()),
            ("volume", self.volume.len()),
            ("day", self.day.len()),
            ("minute", self.minute.len()),
        ] {
            if l != n {
                return Err(format!("bars.{name} has {l} rows, ts has {n}"));
            }
        }
        if self.bar_secs < 60 {
            return Err(format!("bar_secs must be >= 60, got {}", self.bar_secs));
        }
        for i in 0..n {
            if !(TS_MIN..TS_MAX).contains(&self.ts[i]) {
                return Err(format!(
                    "bar {i}: ts {} is not epoch SECONDS in 2000..2100 (milliseconds or nanoseconds?)",
                    self.ts[i]
                ));
            }
            if i > 0 && self.ts[i] <= self.ts[i - 1] {
                return Err(format!("bars are not strictly ascending at row {i}"));
            }
            if self.low[i] <= 0 {
                return Err(format!("bar {i}: prices must be > 0"));
            }
            if self.low[i] > self.open[i].min(self.close[i]) || self.high[i] < self.open[i].max(self.close[i]) {
                return Err(format!("bar {i} violates low <= open/close <= high"));
            }
            if self.volume[i] < 0 {
                return Err(format!("bar {i}: volume must be >= 0"));
            }
        }
        Ok(())
    }
}

/// A lot / tick size change in force from trading day `day` (time-versioned instrument attributes, T1.4.4).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InstrumentChange {
    pub day: i64,
    pub lot: i64,
    /// price units
    pub tick: i64,
}

/// A corporate action of the ex-date `day` applied to an open position (raw execution, design §9.3).
/// Share events multiply the quantity by qty_num / qty_den (bonus 1:1 -> 2/1, split 10 -> 2 -> 5/1, consolidation
/// 1 -> 10 -> 1/10); price levels move by the inverse. `cash_paise` is a cash dividend per share.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CaEvent {
    pub day: i64,
    pub qty_num: i64,
    pub qty_den: i64,
    pub cash_paise: i64,
}

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Reason {
    Entry = 0,
    Signal = 1,
    Stop = 2,
    Target = 3,
    Trail = 4,
    TimeStop = 5,
    SquareOff = 6,
    Forced = 7,
}

pub const REJECT_NAMES: [&str; 10] = [
    "insufficient_funds",
    "no_short",
    "band_locked",
    "zero_volume",
    "entry_cutoff",
    "expired",
    "partial_cancelled",
    "remainder_cancelled",
    "stale_remainder",
    "max_positions",
];
const R_FUNDS: usize = 0;
const R_NO_SHORT: usize = 1;
const R_BAND: usize = 2;
const R_ZERO_VOL: usize = 3;
const R_CUTOFF: usize = 4;
const R_EXPIRED: usize = 5;
const R_PARTIAL: usize = 6;
const R_REMAINDER: usize = 7;
const R_STALE: usize = 8;
/// portfolio mode: a new position would exceed the portfolio's max open positions
const R_MAXPOS: usize = 9;

#[derive(Debug, Clone, Copy)]
pub struct Fill {
    pub bar: u32,
    pub ts: i64,
    pub buy: bool,
    pub qty: i64,
    pub price: i64,
    pub value: Paise,
    pub charges: Charges,
    pub reason: Reason,
}

#[derive(Debug, Clone, Copy)]
pub struct Trade {
    pub dir: i8,
    /// quantity at the close of the trade (after any corporate-action change)
    pub qty: i64,
    pub entry_bar: u32,
    pub exit_bar: u32,
    pub entry_ts: i64,
    pub exit_ts: i64,
    pub entry_value: Paise,
    pub exit_value: Paise,
    pub fees: Paise,
    /// dividends and cash in lieu of fractional entitlements received (paid, for shorts) during the trade
    pub income: Paise,
    pub pnl: Paise,
    pub exit_reason: Reason,
}

/// One decision the engine received (for the decisions hash and the look-ahead harness).
/// ``limit``: the stop-limit's limit leg (NaN otherwise) - exported, NOT part of the decisions hash (stable).
#[derive(Debug, Clone, Copy)]
pub struct IntentRec {
    pub bar: u32,
    pub kind: u8,
    pub order: u8,
    pub price: f64,
    pub limit: f64,
}

#[derive(Debug, Default)]
pub struct Outcome {
    pub fills: Vec<Fill>,
    pub trades: Vec<Trade>,
    /// equity (paise) at each coarse bar close
    pub equity: Vec<Paise>,
    pub cash: Paise,
    pub position: i64,
    /// bracket of the position still open at the end (price units): lets a live route rest the protective stop /
    /// target AT THE BROKER (unification U3 / D3 - a crash of the app must not leave the position unprotected)
    pub active_stop: Option<i64>,
    pub active_target: Option<i64>,
    pub charges: Charges,
    pub rejects: [u32; 10],
    pub bars_in_market: u32,
    pub warnings: Vec<String>,
    pub intents: Vec<IntentRec>,
    pub counters: Vec<(&'static str, u64)>,
    /// metrics-only runs (sweeps): accumulated in the bar loop, no equity / fill / trade vectors (T1.6.1)
    pub fused: Option<crate::metrics::Acc>,
}

#[derive(Clone, Copy)]
struct Seg {
    ts: i64,
    o: i64,
    h: i64,
    l: i64,
    c: i64,
    v: i64,
}

#[derive(Clone, Copy)]
struct PendingExit {
    activation: i64,
    reason: Reason,
}

/// Everything an entry order carries, resolved against the OrderPlan defaults when the decision is made.
#[derive(Clone, Copy)]
struct EntryOrder {
    kind: OrderType,
    price: i64,
    /// stop-limit: the limit leg (price units; the trigger is `price`)
    limit: i64,
    sizing: Sizing,
    stop: Option<Level>,
    target: Option<Level>,
    trail: Option<f64>,
    time_stop: Option<u32>,
    expiry_bars: u32,
}

#[derive(Clone, Copy)]
struct PendingEntry {
    dir: i8,
    order: EntryOrder,
    activation: i64,
    /// session in which the signal was taken
    signal_day: i64,
    /// session in which the order became live (audit 2 A2-H2: DAY validity counts from here)
    live_day: Option<i64>,
    expiry_bar: Option<usize>,
    /// carried remainder of a partially filled entry (participation cap)
    qty: Option<i64>,
    live: bool,
}

struct OpenTrade {
    dir: i8,
    qty: i64,
    entry_bar: u32,
    entry_ts: i64,
    entry_value: Paise,
    exit_value: Paise,
    fees: Paise,
    income: Paise,
}

/// Bracket of the open position (from the entry order, set at the first fill).
#[derive(Clone, Copy, Default)]
struct Active {
    stop: Option<i64>,
    target: Option<i64>,
    trail: Option<f64>,
    time_stop: Option<u32>,
}

pub struct Engine<'a> {
    spec: &'a ExecSpec,
    plan: &'a OrderPlan,
    inst: &'a Instrument,
    costs: &'a CostModel,
    cash: Paise,
    pos: i64,
    active: Active,
    trail_ref: i64,
    trail_moved: bool,
    bars_in_trade: u32,
    highest: i64,
    lowest: i64,
    entry_bar: Option<usize>,
    trade: Option<OpenTrade>,
    pexit: Option<PendingExit>,
    pentry: Option<PendingEntry>,
    blocked_day: i64,
    last_dp_day: i64,
    decisions: u64,
    decision_rejects: u64,
    notified: usize,
    ca_applied: u64,
    /// lot / tick in force (time-versioned, T1.4.4)
    lot: i64,
    tick: i64,
    /// fill bookkeeping independent of the (optional) fill vector
    n_fills: usize,
    last_fill_ts: i64,
    /// portfolio mode (`crate::portfolio`): the rest of the shared account as seen before this bar - cash and
    /// equity of the other legs (plus the starting cash), their gross exposure and open positions. All zero for a
    /// single-instrument run, whose own `cash` then starts at the full capital.
    ext_cash: Paise,
    ext_equity: Paise,
    ext_gross: Paise,
    ext_open: u32,
    max_positions: Option<u32>,
    /// bars shorter than a day (set by `start`)
    intraday: bool,
    out: Outcome,
}

impl Engine<'_> {
    /// Sweeps: keep only the metric accumulators (no per-fill / per-bar vectors).
    pub fn metrics_only(mut self) -> Self {
        self.out.fused = Some(crate::metrics::Acc::new(self.cash));
        self
    }
}

impl<'a> Engine<'a> {
    pub fn new(
        spec: &'a ExecSpec,
        plan: &'a OrderPlan,
        inst: &'a Instrument,
        costs: &'a CostModel,
        cash0: Paise,
    ) -> Self {
        Engine {
            spec,
            plan,
            inst,
            costs,
            cash: cash0,
            pos: 0,
            active: Active::default(),
            trail_ref: 0,
            trail_moved: false,
            bars_in_trade: 0,
            highest: 0,
            lowest: 0,
            entry_bar: None,
            trade: None,
            pexit: None,
            pentry: None,
            blocked_day: i64::MIN,
            last_dp_day: i64::MIN,
            decisions: 0,
            decision_rejects: 0,
            notified: 0,
            ca_applied: 0,
            lot: inst.lot.max(1),
            tick: inst.tick.max(1),
            n_fills: 0,
            last_fill_ts: i64::MIN,
            ext_cash: 0,
            ext_equity: 0,
            ext_gross: 0,
            ext_open: 0,
            max_positions: None,
            intraday: false,
            out: Outcome::default(),
        }
    }

    fn reject(&mut self, r: usize) {
        self.out.rejects[r] += 1;
    }

    fn equity_at(&self, price: i64) -> Paise {
        self.cash + notional(self.pos, price, self.inst.price_scale)
    }

    /// Cash of the whole account (portfolio mode: this leg's ledger + the rest of the shared account).
    fn acct_cash(&self) -> Paise {
        self.cash + self.ext_cash
    }

    #[inline]
    fn slip(&self, px: i64, buy: bool, seg: &Seg) -> i64 {
        let sp = self.spec;
        if sp.slippage_model == SlippageModel::None && !sp.slippage_clip {
            return round_to_tick(px, self.tick, buy); // the common research case: no slippage model
        }
        // ADR-001 (flagfix2 model): a session-edge fill slips more; daily bars: a fill AT the open is an edge fill
        let edge = sp.slippage_edge_minutes > 0 && {
            if self.intraday {
                let m = (((seg.ts + 19_800) % 86_400) / 60) as i32;
                m < sp.session_open_min + sp.slippage_edge_minutes
                    || m >= sp.session_close_min - sp.slippage_edge_minutes
            } else {
                px == seg.o
            }
        };
        let widen = if edge { sp.slippage_edge_widen } else { 1.0 };
        let adj = match sp.slippage_model {
            SlippageModel::None => 0,
            SlippageModel::Ticks => ((sp.slippage_value * widen).round() as i64) * self.tick,
            SlippageModel::Pct => {
                let frac = (sp.slippage_min_ticks * self.tick as f64 / (px as f64).max(1.0)).max(sp.slippage_value);
                ((px as f64) * frac * widen).round() as i64
            }
        };
        let p = if buy { px + adj } else { (px - adj).max(1) };
        let p = round_to_tick(p, self.tick, buy);
        if !sp.slippage_clip {
            return p;
        }
        // never outside the executing bar's range (tick-rounded inwards; a bar narrower than a tick: its raw range)
        let (mut lo, mut hi) = (round_to_tick(seg.l, self.tick, true), round_to_tick(seg.h, self.tick, false));
        if lo > hi {
            (lo, hi) = (seg.l, seg.h);
        }
        p.clamp(lo, hi)
    }

    /// Quantity for a new position at price `px` (whole lots), leaving room for the charges.
    fn size(&self, order: &EntryOrder, px: i64, equity: Paise, day: i64, dir: i8) -> i64 {
        let lot = self.lot;
        let unit = notional(1, px, self.inst.price_scale).max(1);
        let all_in = || -> Paise {
            let bp = ((equity as f64) * self.spec.leverage) as Paise;
            if dir > 0 {
                bp.min(self.acct_cash().max(0) + ((self.spec.leverage - 1.0) * equity as f64) as Paise)
            } else {
                bp
            }
        };
        let budget: Paise = match order.sizing {
            Sizing::AllIn => all_in(),
            Sizing::FixedValue(v) => (v * 100.0) as Paise,
            Sizing::PctEquity(p) => ((equity as f64) * p) as Paise,
            Sizing::FixedQty(q) => {
                let q = (q / lot) * lot;
                return self.affordable(q, px, equity, day, dir);
            }
            Sizing::RiskPct(r) => {
                // qty = equity x r / |entry - stop|, capped by the all-in budget below
                // the distance to the stop the bracket will actually carry (tick-rounded like the order)
                let tick = self.tick;
                let stop = match order.stop {
                    Some(Level::Pct(p)) => {
                        round_to_tick((px as f64 * (1.0 - dir as f64 * p)).round() as i64, tick, dir < 0)
                    }
                    Some(Level::Price(l)) => round_to_tick(l.round() as i64, tick, dir < 0),
                    None => px,
                };
                let dist = (px - stop).abs() as f64;
                if dist <= 0.0 {
                    return 0;
                }
                let risk_paise = equity as f64 * r;
                let dist_paise = dist * 100.0 / self.inst.price_scale as f64;
                let q = ((risk_paise / dist_paise).floor() as i64 / lot) * lot;
                let cap = {
                    let b = all_in();
                    let rate = self.costs.buy_rate_bound_e8(day) as i128;
                    let per_unit = unit as i128 * (100_000_000 + rate) / 100_000_000;
                    ((b.max(0) as i128 / per_unit.max(1)) as i64 / lot) * lot
                };
                return self.affordable(q.min(cap), px, equity, day, dir);
            }
        };
        if budget <= 0 {
            return 0;
        }
        let rate = self.costs.buy_rate_bound_e8(day) as i128;
        let per_unit = unit as i128 * (100_000_000 + rate) / 100_000_000;
        let q = ((budget as i128 / per_unit.max(1)) as i64 / lot) * lot;
        self.affordable(q, px, equity, day, dir)
    }

    fn affordable(&self, mut q: i64, px: i64, equity: Paise, day: i64, dir: i8) -> i64 {
        let lot = self.lot;
        let limit = ((equity as f64) * self.spec.leverage) as Paise;
        while q > 0 {
            let value = notional(q, px, self.inst.price_scale);
            let ch = self.costs.charges(day, if dir > 0 { Side::Buy } else { Side::Sell }, value).total();
            // portfolio mode: a short also counts the other legs' gross exposure against the leverage limit
            let ok = if dir > 0 {
                value + ch <= self.acct_cash().max(0) + (limit - equity).max(0)
            } else {
                value + self.ext_gross <= limit
            };
            if ok {
                break;
            }
            q -= lot;
        }
        q.max(0)
    }

    fn cap_qty(&self, want: i64, seg: &Seg) -> i64 {
        let lot = self.lot;
        match self.spec.participation_cap {
            None => want,
            Some(c) => {
                let cap = (((seg.v as f64) * c).floor() as i64 / lot) * lot;
                want.min(cap.max(0))
            }
        }
    }

    /// audit 2 A2-M1: a bar without volume never fills (unless zero_volume_fills), whatever the participation cap
    fn no_volume(&self, seg: &Seg) -> bool {
        seg.v == 0 && !self.spec.zero_volume_fills
    }

    #[allow(clippy::too_many_arguments)]
    fn record_fill(&mut self, bar: usize, ts: i64, day: i64, buy: bool, qty: i64, price: i64, reason: Reason) {
        let value = notional(qty, price, self.inst.price_scale);
        let mut charges = self.costs.charges(day, if buy { Side::Buy } else { Side::Sell }, value);
        // DP charge: once per scrip per day on delivery sells of a long holding (A2-M7)
        if !buy && self.pos > 0 && self.spec.product == Product::Cnc && self.last_dp_day != day {
            let (dp, gst) = self.costs.dp_charge(day);
            if dp > 0 {
                charges.dp = dp;
                charges.gst += gst;
                self.last_dp_day = day;
            }
        }
        let fee = charges.total();
        if buy {
            self.cash -= value + fee
        } else {
            self.cash += value - fee
        }
        self.out.charges.add(&charges);
        self.n_fills += 1;
        self.last_fill_ts = ts;
        if self.out.fused.is_none() {
            self.out.fills.push(Fill { bar: bar as u32, ts, buy, qty, price, value, charges, reason });
        }
        let signed = if buy { qty } else { -qty };
        let opening = self.pos == 0 || (self.pos > 0) == (signed > 0);
        self.pos += signed;
        if opening {
            let t = self.trade.get_or_insert(OpenTrade {
                dir: if buy { 1 } else { -1 },
                qty: 0,
                entry_bar: bar as u32,
                entry_ts: ts,
                entry_value: 0,
                exit_value: 0,
                fees: 0,
                income: 0,
            });
            t.qty += qty;
            t.entry_value += value;
            t.fees += fee;
        } else if let Some(t) = self.trade.as_mut() {
            t.exit_value += value;
            t.fees += fee;
            if self.pos == 0 {
                let gross = if t.dir > 0 { t.exit_value - t.entry_value } else { t.entry_value - t.exit_value };
                let pnl = gross - t.fees + t.income;
                let trade = Trade {
                    dir: t.dir,
                    qty: t.qty,
                    entry_bar: t.entry_bar,
                    exit_bar: bar as u32,
                    entry_ts: t.entry_ts,
                    exit_ts: ts,
                    entry_value: t.entry_value,
                    exit_value: t.exit_value,
                    fees: t.fees,
                    income: t.income,
                    pnl,
                    exit_reason: reason,
                };
                match self.out.fused.as_mut() {
                    Some(acc) => acc.trade(&trade),
                    None => self.out.trades.push(trade),
                }
                self.trade = None;
                self.active = Active::default();
                self.trail_moved = false;
                self.bars_in_trade = 0;
                self.entry_bar = None;
            }
        }
    }

    /// Close (part of) the position at `px`; the participation cap may leave a remainder (carried as a market exit).
    #[allow(clippy::too_many_arguments)]
    fn exit_at(&mut self, bar: usize, seg: &Seg, day: i64, px_raw: i64, reason: Reason, lock: (bool, bool)) -> bool {
        if self.pos == 0 {
            return false;
        }
        let buy = self.pos < 0;
        if (buy && lock.0) || (!buy && lock.1) {
            self.reject(R_BAND);
            return false;
        }
        if self.no_volume(seg) {
            self.reject(R_ZERO_VOL);
            return false;
        }
        let want = self.pos.abs();
        let q = self.cap_qty(want, seg);
        if q <= 0 {
            return false;
        }
        let px = if matches!(reason, Reason::Target) { px_raw } else { self.slip(px_raw, buy, seg) };
        self.record_fill(bar, seg.ts, day, buy, q, px, reason);
        if self.pos != 0 {
            // remainder: carry as a market exit at the next segment, or cancel (position stays open)
            match self.spec.participation_remainder {
                Remainder::Carry | Remainder::CancelEntries => {
                    self.pexit = Some(PendingExit { activation: seg.ts + 1, reason })
                }
                Remainder::Cancel => self.reject(R_PARTIAL),
            }
        }
        true
    }

    #[allow(clippy::too_many_arguments)]
    fn enter_at(
        &mut self,
        bar: usize,
        seg: &Seg,
        day: i64,
        dir: i8,
        px_raw: i64,
        slip: bool,
        lock: (bool, bool),
        order: EntryOrder,
        fixed_qty: Option<i64>,
    ) -> bool {
        let buy = dir > 0;
        if (buy && lock.0) || (!buy && lock.1) {
            self.reject(R_BAND);
            return false;
        }
        if self.no_volume(seg) {
            self.reject(R_ZERO_VOL);
            return false;
        }
        if self.pos == 0
            && let Some(m) = self.max_positions
            && self.ext_open >= m
        {
            self.reject(R_MAXPOS);
            return false;
        }
        let px = if slip { self.slip(px_raw, buy, seg) } else { round_to_tick(px_raw, self.tick, buy) };
        let equity = self.equity_at(px) + self.ext_equity;
        let want = fixed_qty.unwrap_or_else(|| self.size(&order, px, equity, day, dir));
        if want <= 0 {
            self.reject(R_FUNDS);
            return false;
        }
        let q = self.cap_qty(want, seg);
        if q <= 0 {
            return false;
        }
        let first = self.pos == 0;
        self.record_fill(bar, seg.ts, day, buy, q, px, Reason::Entry);
        if first {
            // first fill of this position: attach the bracket of the order
            let f = px as f64;
            let tick = self.tick;
            let lvl = |l: Option<Level>, sign: f64, up: bool| -> Option<i64> {
                l.map(|l| match l {
                    Level::Pct(p) => round_to_tick((f * (1.0 + sign * p)).round() as i64, tick, up),
                    Level::Price(x) => round_to_tick(x.round() as i64, tick, up),
                })
            };
            self.active = Active {
                stop: lvl(order.stop, -(dir as f64), !buy),
                target: lvl(order.target, dir as f64, buy),
                trail: order.trail,
                time_stop: order.time_stop,
            };
            self.trail_ref = px;
            self.highest = px;
            self.lowest = px;
            self.entry_bar = Some(bar);
        }
        if q < want {
            let left = want - q;
            match self.spec.participation_remainder {
                Remainder::Carry => {
                    self.pentry = Some(PendingEntry {
                        dir,
                        order: EntryOrder { kind: OrderType::Market, ..order },
                        activation: seg.ts + 1,
                        signal_day: day,
                        live_day: Some(day),
                        expiry_bar: None,
                        qty: Some(left),
                        live: true,
                    });
                }
                Remainder::Cancel | Remainder::CancelEntries => self.reject(R_PARTIAL),
            }
        }
        true
    }

    /// Protective orders inside one segment. Returns true when the position was closed.
    fn protective(&mut self, bar: usize, seg: &Seg, day: i64, gap_ok: bool, lock: (bool, bool)) -> bool {
        if self.pos == 0 || (self.active.stop.is_none() && self.active.target.is_none()) {
            return false;
        }
        let long = self.pos > 0;
        let (s, t) = (self.active.stop, self.active.target);
        if gap_ok {
            if let Some(sp) = s
                && ((long && seg.o <= sp) || (!long && seg.o >= sp))
            {
                let reason = if self.trail_moved { Reason::Trail } else { Reason::Stop };
                return self.exit_at(bar, seg, day, seg.o, reason, lock);
            }
            if let Some(tp) = t
                && ((long && seg.o >= tp) || (!long && seg.o <= tp))
            {
                return self.exit_at(bar, seg, day, seg.o, Reason::Target, lock);
            }
        }
        let hit_s = s.map(|sp| if long { seg.l <= sp } else { seg.h >= sp }).unwrap_or(false);
        let hit_t = t.map(|tp| if long { seg.h >= tp } else { seg.l <= tp }).unwrap_or(false);
        let stop_first = match (hit_s, hit_t) {
            (true, true) => match self.spec.intrabar_policy {
                IntrabarPolicy::OhlcDirection => {
                    let low_first = seg.c >= seg.o; // O -> L -> H -> C
                    if long { low_first } else { !low_first }
                }
                _ => true, // pessimistic (also inside one 1m bar)
            },
            (true, false) => true,
            (false, true) => false,
            _ => return false,
        };
        // never a better price than the segment offered: a stop beyond the open fills at the open (gap), a target
        // the open already passed fills at the open
        if stop_first {
            let reason = if self.trail_moved { Reason::Trail } else { Reason::Stop };
            let sp = s.unwrap();
            let px = if long { sp.min(seg.o) } else { sp.max(seg.o) };
            self.exit_at(bar, seg, day, px, reason, lock)
        } else {
            let tp = t.unwrap();
            let px = if long { tp.max(seg.o) } else { tp.min(seg.o) };
            self.exit_at(bar, seg, day, px, Reason::Target, lock)
        }
    }

    /// Resting limit / stop entry inside a segment: gap at the open, then intrabar.
    fn resting_entry(&mut self, bar: usize, seg: &Seg, day: i64, at_open: bool, lock: (bool, bool)) -> bool {
        let Some(pe) = self.pentry else { return false };
        if !pe.live || self.pos != 0 || pe.order.kind == OrderType::Market {
            return false;
        }
        let long = pe.dir > 0;
        let p = pe.order.price;
        if pe.order.kind == OrderType::StopLimit {
            return self.resting_stop_limit(bar, seg, day, pe, at_open, lock);
        }
        let px = match (pe.order.kind, long, at_open) {
            (OrderType::Limit, true, true) if seg.o <= p => Some(seg.o),
            (OrderType::Limit, false, true) if seg.o >= p => Some(seg.o),
            (OrderType::Stop, true, true) if seg.o >= p => Some(seg.o),
            (OrderType::Stop, false, true) if seg.o <= p => Some(seg.o),
            (OrderType::Limit, true, false) if seg.l <= p => Some(p),
            (OrderType::Limit, false, false) if seg.h >= p => Some(p),
            (OrderType::Stop, true, false) if seg.h >= p => Some(p),
            (OrderType::Stop, false, false) if seg.l <= p => Some(p),
            _ => None,
        };
        let Some(px) = px else { return false };
        self.pentry = None;
        let slip = pe.order.kind == OrderType::Stop; // limit orders fill at their price
        self.enter_at(bar, seg, day, pe.dir, px, slip, lock, pe.order, pe.qty)
    }

    /// A stop-limit entry: triggers like a stop, then fills (or rests) as a LIMIT at its limit leg - never worse.
    /// Pessimistic: a marketable trigger fills at the limit leg capped by the segment's range; a non-marketable one
    /// becomes a resting limit order (same expiry), filled by the normal limit path later.
    fn resting_stop_limit(
        &mut self,
        bar: usize,
        seg: &Seg,
        day: i64,
        pe: PendingEntry,
        at_open: bool,
        lock: (bool, bool),
    ) -> bool {
        let long = pe.dir > 0;
        let (p, l) = (pe.order.price, pe.order.limit);
        let triggered = match (long, at_open) {
            (true, true) => seg.o >= p,
            (false, true) => seg.o <= p,
            (true, false) => seg.h >= p,
            (false, false) => seg.l <= p,
        };
        if !triggered {
            return false;
        }
        let px = if at_open {
            if (long && seg.o <= l) || (!long && seg.o >= l) {
                Some(seg.o) // gap through the trigger, open inside the limit
            } else if (long && seg.l <= l) || (!long && seg.h >= l) {
                Some(l) // trades back to the limit inside the segment
            } else {
                None
            }
        } else if (long && l >= p) || (!long && l <= p) {
            Some(if long { l.min(seg.h) } else { l.max(seg.l) }) // marketable: pessimistic within the allowed range
        } else {
            None // triggered intrabar, not marketable: pessimistic - it rests as a limit from the NEXT segment on
        };
        match px {
            Some(px) => {
                self.pentry = None;
                self.enter_at(bar, seg, day, pe.dir, px, false, lock, pe.order, pe.qty)
            }
            None => {
                // triggered, not fillable: it is a plain resting limit from here on
                let mut rest = pe;
                rest.order.kind = OrderType::Limit;
                rest.order.price = l;
                self.pentry = Some(rest);
                false
            }
        }
    }

    /// Corporate actions of the ex-date (P0, before the open): quantity, levels, pending orders, cash.
    fn apply_ca(&mut self, e: &CaEvent, open_raw: i64) {
        let (num, den) = (e.qty_num.max(1), e.qty_den.max(1));
        if e.cash_paise > 0 && self.pos != 0 && self.spec.dividends == Dividends::Credit {
            let cash = self.pos * e.cash_paise; // shorts pay the dividend
            self.cash += cash;
            if let Some(t) = self.trade.as_mut() {
                t.income += cash;
            }
        }
        if num == den {
            return;
        }
        let tick = self.tick;
        let scale = |p: i64, up: bool| round_to_tick(((p as i128 * den as i128) / num as i128) as i64, tick, up);
        if self.pos != 0 {
            let lot = self.lot;
            let abs = self.pos.abs();
            let units = abs as i128 * num as i128; // in 1/den shares
            let new_abs = ((units / den as i128) as i64 / lot) * lot;
            let frac_units = units - new_abs as i128 * den as i128;
            let sign = self.pos.signum();
            if frac_units > 0 {
                // cash in lieu of the fractional entitlement, at the ex-date open
                let paise =
                    (frac_units * open_raw as i128 * 100 / (den as i128 * self.inst.price_scale as i128)) as i64;
                self.cash += sign * paise;
                if let Some(t) = self.trade.as_mut() {
                    t.income += sign * paise;
                }
            }
            self.pos = sign * new_abs;
            if let Some(t) = self.trade.as_mut() {
                t.qty = new_abs;
            }
            let long = sign > 0;
            self.active.stop = self.active.stop.map(|s| scale(s, !long));
            self.active.target = self.active.target.map(|s| scale(s, long));
            self.trail_ref = scale(self.trail_ref, !long);
            self.highest = scale(self.highest, true);
            self.lowest = scale(self.lowest, false);
        }
        if let Some(mut pe) = self.pentry {
            if pe.order.kind != OrderType::Market {
                pe.order.price = scale(
                    pe.order.price,
                    (pe.dir > 0) == matches!(pe.order.kind, OrderType::Stop | OrderType::StopLimit),
                );
                if pe.order.kind == OrderType::StopLimit {
                    pe.order.limit = scale(pe.order.limit, pe.dir < 0);
                }
            }
            if let Some(q) = pe.qty {
                pe.qty = Some(((q as i128 * num as i128) / den as i128) as i64);
            }
            self.pentry = Some(pe);
        }
        self.ca_applied += 1;
    }

    fn resolve_entry(&self, it: &Intent, close: i64, dir: i8) -> EntryOrder {
        let tick = self.tick;
        let price = match it.order {
            OrderType::Market => 0,
            OrderType::Limit => match it.price {
                Some(p) => round_to_tick(p.round() as i64, tick, dir < 0),
                None => round_to_tick(
                    (close as f64 * (1.0 - dir as f64 * it.offset_pct.unwrap_or(0.0))).round() as i64,
                    tick,
                    dir < 0,
                ),
            },
            OrderType::Stop | OrderType::StopLimit => match it.price {
                Some(p) => round_to_tick(p.round() as i64, tick, dir > 0),
                None => round_to_tick(
                    (close as f64 * (1.0 + dir as f64 * it.offset_pct.unwrap_or(0.0))).round() as i64,
                    tick,
                    dir > 0,
                ),
            },
        };
        // the limit leg of a stop-limit (conservative rounding: a buy limit never above the intention)
        let limit = match (it.order, it.limit) {
            (OrderType::StopLimit, Some(l)) => round_to_tick(l.round() as i64, tick, dir < 0),
            (OrderType::StopLimit, None) => price, // marketable: limit at the trigger
            _ => 0,
        };
        let lvl = |o: Ov<Level>, d: Option<f64>| match o {
            Ov::Default => d.map(Level::Pct),
            Ov::Off => None,
            Ov::Set(l) => Some(l),
        };
        EntryOrder {
            kind: it.order,
            price,
            limit,
            sizing: it.size.unwrap_or_else(|| (self.plan.sizing, self.plan.sizing_value).into()),
            stop: lvl(it.stop, self.plan.stop_pct),
            target: lvl(it.target, self.plan.target_pct),
            trail: match it.trail_pct {
                Ov::Default => self.plan.trail_pct,
                Ov::Off => None,
                Ov::Set(x) => Some(x),
            },
            time_stop: match it.time_stop_bars {
                Ov::Default => self.plan.time_stop_bars,
                Ov::Off => None,
                Ov::Set(x) => Some(x),
            },
            expiry_bars: it.expiry_bars.unwrap_or(self.plan.entry_expiry_bars),
        }
    }

    /// Run the whole series. The loop body is split by phase (audit 2 A2-L12): `session_boundary` (P0),
    /// `open_events` (P1), `intrabar` (P2), `close` (E + P3), `mark` (P4).
    pub fn run(
        self,
        bars: &Bars,
        m1: Option<&Bars>,
        events: &[CaEvent],
        src: &mut dyn DecisionSource,
    ) -> Result<Outcome, String> {
        self.run_with(bars, m1, events, &[], src)
    }

    /// `changes`: time-versioned lot / tick sizes (T1.4.4), applied from their trading day on.
    pub fn run_with(
        mut self,
        bars: &Bars,
        m1: Option<&Bars>,
        events: &[CaEvent],
        changes: &[InstrumentChange],
        src: &mut dyn DecisionSource,
    ) -> Result<Outcome, String> {
        // the single-instrument hot loop stays a plain loop (sweeps run it millions of times); portfolio mode uses the
        // same setup (`begin`) through the Stepper
        let mut ls = self.begin(bars, m1, events, changes)?;
        for t in 0..bars.len() {
            self.bar(t, &mut ls, bars, m1, events, changes, src)?;
        }
        self.finish(src);
        Ok(self.out)
    }

    /// Portfolio mode: the leg refuses a NEW position while `max` positions are open in the other legs.
    pub fn with_max_positions(mut self, max: Option<u32>) -> Self {
        self.max_positions = max;
        self
    }

    /// Validate the inputs and return a stepper positioned before the first bar (portfolio mode steps several
    /// engines bar by bar on one timeline; `run_with` steps one to the end).
    pub fn start<'b>(
        mut self,
        bars: &'b Bars<'b>,
        m1: Option<&'b Bars<'b>>,
        events: &'b [CaEvent],
        changes: &'b [InstrumentChange],
    ) -> Result<Stepper<'a, 'b>, String> {
        let ls = self.begin(bars, m1, events, changes)?;
        Ok(Stepper { e: self, ls, bars, m1, events, changes, t: 0 })
    }

    /// Input checks and the loop state before the first bar (shared by `run_with` and `start`).
    fn begin(
        &mut self,
        bars: &Bars,
        m1: Option<&Bars>,
        events: &[CaEvent],
        changes: &[InstrumentChange],
    ) -> Result<LoopState, String> {
        bars.check()?;
        let n = bars.len();
        if let Some(m) = m1 {
            m.check()?;
        }
        let intraday = bars.bar_secs < 86_400;
        self.intraday = intraday;
        if !intraday && self.spec.product == Product::Mis {
            return Err("product MIS (intraday) needs intraday bars; use CNC or NRML on daily bars".into());
        }
        if changes.iter().any(|c| c.lot < 1 || c.tick < 1) {
            return Err("instrument changes need lot >= 1 and tick >= 1".into());
        }
        let use_m1 = m1.is_some()
            && (self.spec.intrabar_policy == IntrabarPolicy::Magnifier1m || self.spec.decision_latency_s > 0);
        if self.spec.decision_latency_s > 0 && m1.is_none() && bars.bar_secs > 60 {
            self.out.warnings.push(
                "decision latency without 1m bars: orders reach the market at the first coarse bar opening after \
                 the latency"
                    .into(),
            );
        }
        // audit 2 A2-H1: session rules (entry cutoff, MIS square-off) exist for intraday bars only
        let (cutoff_at, squareoff_at) =
            if intraday { (self.spec.no_new_entries_after, self.spec.squareoff_at) } else { (None, None) };
        let ls = LoopState {
            intraday,
            use_m1,
            cutoff_at,
            squareoff_at,
            shorts: self.spec.shorts_allowed(),
            ev_ptr: events.partition_point(|e| n == 0 || e.day <= bars.day[0]),
            ch_ptr: 0,
            mptr: 0,
            prev_day: i64::MIN,
            prev_day_close: None,
            last_close: None,
            segs: Vec::with_capacity(64),
            intents: Vec::with_capacity(4),
        };
        Ok(ls)
    }

    #[allow(clippy::too_many_arguments)]
    fn bar(
        &mut self,
        t: usize,
        ls: &mut LoopState,
        bars: &Bars,
        m1: Option<&Bars>,
        events: &[CaEvent],
        changes: &[InstrumentChange],
        src: &mut dyn DecisionSource,
    ) -> Result<(), String> {
        let day = bars.day[t];
        let new_day = day != ls.prev_day;
        if new_day {
            ls.prev_day_close = ls.last_close;
            // time-versioned lot / tick size: the values in force on this trading day
            while ls.ch_ptr < changes.len() && changes[ls.ch_ptr].day <= day {
                self.lot = changes[ls.ch_ptr].lot;
                self.tick = changes[ls.ch_ptr].tick;
                ls.ch_ptr += 1;
            }
        }
        // price-band lock for this bar (static band vs the previous day's close)
        let lock = match (self.spec.price_band_pct, ls.prev_day_close) {
            (Some(b), Some(pc)) if bars.high[t] == bars.low[t] => {
                let up = (pc as f64 * (1.0 + b)) as i64 - self.tick;
                let dn = (pc as f64 * (1.0 - b)) as i64 + self.tick;
                (bars.close[t] >= up, bars.close[t] <= dn) // (buys blocked, sells blocked)
            }
            _ => (false, false),
        };
        self.segments(t, ls, bars, m1);
        let segs = std::mem::take(&mut ls.segs);
        for (k, seg) in segs.iter().enumerate() {
            let seg_min = bars.minute[t] + ((seg.ts - bars.ts[t]) / 60) as i32;
            if k == 0 && new_day {
                self.session_boundary(t, ls, seg, day, events, lock);
            }
            self.open_events(t, ls, seg, seg_min, day, lock);
            self.intrabar(t, seg, day, lock);
        }
        ls.segs = segs;
        self.close(t, ls, bars, day, lock, src)?;
        self.mark(t, ls, bars, day);
        Ok(())
    }

    /// The bar's segments: its stored 1m bars (magnifier / decision latency) or the bar itself.
    fn segments(&mut self, t: usize, ls: &mut LoopState, bars: &Bars, m1: Option<&Bars>) {
        ls.segs.clear();
        if ls.use_m1 {
            let m = m1.unwrap();
            let end = bars.ts[t] + bars.bar_secs;
            while ls.mptr < m.len() && m.ts[ls.mptr] < bars.ts[t] {
                ls.mptr += 1;
            }
            while ls.mptr < m.len() && m.ts[ls.mptr] < end {
                let i = ls.mptr;
                ls.segs.push(Seg {
                    ts: m.ts[i],
                    o: m.open[i],
                    h: m.high[i],
                    l: m.low[i],
                    c: m.close[i],
                    v: m.volume[i],
                });
                ls.mptr += 1;
            }
        }
        if ls.segs.is_empty() {
            ls.segs.push(Seg {
                ts: bars.ts[t],
                o: bars.open[t],
                h: bars.high[t],
                l: bars.low[t],
                c: bars.close[t],
                v: bars.volume[t],
            });
        }
    }

    /// P0: corporate actions of the ex-date, stale DAY orders, MIS positions left over from the previous session.
    fn session_boundary(
        &mut self,
        t: usize,
        ls: &mut LoopState,
        seg: &Seg,
        day: i64,
        events: &[CaEvent],
        lock: (bool, bool),
    ) {
        while ls.ev_ptr < events.len() && events[ls.ev_ptr].day <= day {
            let e = events[ls.ev_ptr];
            ls.ev_ptr += 1;
            self.apply_ca(&e, seg.o);
        }
        if let Some(pe) = self.pentry {
            let stale_day = pe.live_day.map(|d| d != day).unwrap_or(false);
            if pe.order.kind != OrderType::Market && pe.live && stale_day {
                self.pentry = None; // DAY order: cancelled at the end of the session it was live in
                self.reject(R_EXPIRED);
            } else if ls.intraday && self.spec.product == Product::Mis && pe.signal_day != day {
                self.pentry = None; // MIS: no intraday order survives the session
                self.reject(R_EXPIRED);
            }
        }
        if self.pos != 0 && self.spec.product == Product::Mis && ls.prev_day != i64::MIN {
            self.exit_at(t, seg, day, seg.o, Reason::Forced, lock);
            if self.pos == 0 {
                self.pexit = None; // audit 2 A2-M2: a partial forced close keeps its carried remainder
            }
        }
    }

    /// P1: MIS square-off, pending exits, pending market entries, resting entries going live, gap checks.
    fn open_events(&mut self, t: usize, ls: &LoopState, seg: &Seg, seg_min: i32, day: i64, lock: (bool, bool)) {
        if self.spec.product == Product::Mis
            && let Some(sq) = ls.squareoff_at
            && seg_min >= sq
        {
            self.blocked_day = day;
            self.pentry = None;
            if self.pos != 0 {
                self.exit_at(t, seg, day, seg.o, Reason::SquareOff, lock);
                if self.pos == 0 {
                    self.pexit = None;
                }
            }
        }
        if self.spec.same_bar_priority == SameBarPriority::StopFirst && self.protective(t, seg, day, true, lock) {
            self.pexit = None;
        }
        if let Some(px) = self.pexit
            && px.activation <= seg.ts
        {
            self.pexit = None;
            if self.pos != 0 {
                self.exit_at(t, seg, day, seg.o, px.reason, lock);
            }
        }
        if let Some(pe) = self.pentry
            && pe.activation <= seg.ts
        {
            if self.blocked_day == day {
                self.pentry = None;
                self.reject(R_CUTOFF);
            } else if pe.order.kind == OrderType::Market {
                self.pentry = None;
                match pe.qty {
                    // audit 2 A2-H3: a carried remainder only tops up the SAME open position; once the position
                    // is gone (exit, stop) or reversed it is stale and dropped
                    Some(q) if self.pos != 0 && (self.pos > 0) == (pe.dir > 0) => {
                        self.enter_at(t, seg, day, pe.dir, seg.o, true, lock, pe.order, Some(q));
                    }
                    Some(_) => self.reject(R_STALE),
                    None if self.pos == 0 => {
                        self.enter_at(t, seg, day, pe.dir, seg.o, true, lock, pe.order, None);
                    }
                    None => {}
                }
            } else if !pe.live {
                let expiry_bar = (pe.order.expiry_bars > 0).then(|| t + pe.order.expiry_bars as usize - 1);
                self.pentry = Some(PendingEntry { live: true, live_day: Some(day), expiry_bar, ..pe });
            }
        }
        // audit 2 A2-L7: the bracket is "live on the entry bar" per COARSE bar, not per 1m segment
        let entered_this_bar = self.entry_bar == Some(t);
        if self.spec.same_bar_priority == SameBarPriority::Chronological
            && !(entered_this_bar && !self.spec.bracket_live_on_entry_bar)
            && !(entered_this_bar && self.last_fill_ts == seg.ts)
            && self.protective(t, seg, day, true, lock)
        {
            self.pexit = None;
        }
        if self.pos == 0 && self.blocked_day != day {
            self.resting_entry(t, seg, day, true, lock);
        }
    }

    /// P2: resting entries trigger inside the segment; protective stop / target by the intrabar policy.
    fn intrabar(&mut self, t: usize, seg: &Seg, day: i64, lock: (bool, bool)) {
        if self.pos == 0 && self.blocked_day != day {
            self.resting_entry(t, seg, day, false, lock);
        }
        let entered_this_bar = self.entry_bar == Some(t);
        if self.pos != 0 && (!entered_this_bar || self.spec.bracket_live_on_entry_bar) {
            let gap_ok = false; // open already handled in P1
            if self.protective(t, seg, day, gap_ok, lock) {
                self.pexit = None;
            }
        }
    }

    /// E + P3: the decision source sees the position after P2; trailing / time stops; intents become orders.
    fn close(
        &mut self,
        t: usize,
        ls: &mut LoopState,
        bars: &Bars,
        day: i64,
        lock: (bool, bool),
        src: &mut dyn DecisionSource,
    ) -> Result<(), String> {
        let n = bars.len();
        let close = bars.close[t];
        if self.pos != 0 {
            self.highest = self.highest.max(bars.high[t]);
            self.lowest = self.lowest.min(bars.low[t]);
        }
        let avg_entry = match &self.trade {
            Some(tr) if tr.qty > 0 => tr.entry_value as f64 * self.inst.price_scale as f64 / 100.0 / tr.qty as f64,
            _ => 0.0,
        };
        let ps = PosState {
            qty: self.pos,
            avg_entry,
            bars_in_trade: if self.pos != 0 { self.bars_in_trade + 1 } else { 0 },
            highest: self.highest,
            lowest: self.lowest,
            close,
        };
        while self.notified < self.out.fills.len() {
            let f = self.out.fills[self.notified];
            src.on_fill(f.bar as usize, f.buy, f.qty, f.price);
            self.notified += 1;
        }
        let mut intents = std::mem::take(&mut ls.intents);
        intents.clear();
        src.decide(&BarCtx { t, n, day }, &ps, &mut intents)?;
        let bar_close_ts = bars.ts[t] + bars.bar_secs;
        let close_min = bars.minute[t] + (bars.bar_secs / 60) as i32;
        if self.pos != 0 {
            self.bars_in_trade += 1;
            self.out.bars_in_market += 1;
            self.trail_and_time_stop(close, bar_close_ts);
        }
        let has_next = t + 1 < n;
        let mut exit_sig = false;
        let mut entry: Option<(i8, Intent)> = None;
        let record = self.out.fused.is_none();
        for it in &intents {
            if record {
                self.out.intents.push(IntentRec {
                    bar: t as u32,
                    kind: it.kind as u8,
                    order: it.order as u8,
                    price: it.price.unwrap_or(f64::NAN),
                    limit: it.limit.unwrap_or(f64::NAN),
                });
            }
            match it.kind {
                IntentKind::ExitLong => exit_sig |= self.pos > 0,
                IntentKind::ExitShort => exit_sig |= self.pos < 0,
                IntentKind::ExitAll => exit_sig |= self.pos != 0,
                IntentKind::EnterLong if entry.is_none() => entry = Some((1, *it)),
                IntentKind::EnterShort if entry.is_none() => {
                    if ls.shorts {
                        entry = Some((-1, *it));
                    } else {
                        self.reject(R_NO_SHORT);
                        self.decision_rejects += 1;
                        self.decisions += 1;
                    }
                }
                _ => {}
            }
        }
        ls.intents = intents;
        let entry_dir = entry.map(|e| e.0).unwrap_or(0);
        let reversing = entry_dir != 0 && self.pos != 0 && (self.pos > 0) != (entry_dir > 0);
        let same_side = entry_dir != 0 && self.pos != 0 && !reversing;
        if exit_sig || reversing {
            // audit 2 A2-H3: the strategy is leaving: a carried entry remainder must not re-open the position
            if let Some(pe) = self.pentry
                && pe.qty.is_some()
            {
                self.pentry = None;
                self.reject(R_REMAINDER);
            }
        }
        let cutoff = ls.cutoff_at.map(|c| close_min > c).unwrap_or(false)
            || (self.spec.product == Product::Mis && ls.squareoff_at.map(|s| close_min >= s).unwrap_or(false))
            || self.blocked_day == day;
        if self.spec.fill_timing == FillTiming::Close {
            let seg = Seg { ts: bar_close_ts - 1, o: close, h: close, l: close, c: close, v: bars.volume[t] };
            if exit_sig || reversing {
                self.exit_at(t, &seg, day, close, Reason::Signal, lock);
            }
            if let Some((dir, it)) = entry
                && self.pos == 0
                && has_next
            {
                self.decisions += 1;
                if cutoff {
                    self.reject(R_CUTOFF);
                    self.decision_rejects += 1;
                } else {
                    let order = self.resolve_entry(&it, close, dir);
                    let fills = self.n_fills;
                    self.enter_at(t, &seg, day, dir, close, true, lock, order, None);
                    if self.n_fills == fills {
                        self.decision_rejects += 1;
                    }
                }
            }
        } else {
            let act = bar_close_ts + self.spec.decision_latency_s;
            if (exit_sig || reversing) && has_next && self.pexit.is_none() {
                self.pexit = Some(PendingExit { activation: act, reason: Reason::Signal });
            }
            if let Some((dir, it)) = entry
                && has_next
                && !same_side
                && self.pentry.is_none()
            {
                self.decisions += 1;
                if cutoff {
                    self.reject(R_CUTOFF);
                    self.decision_rejects += 1;
                } else {
                    let order = self.resolve_entry(&it, close, dir);
                    self.pentry = Some(PendingEntry {
                        dir,
                        order,
                        activation: act,
                        signal_day: day,
                        live_day: None,
                        expiry_bar: None,
                        qty: None,
                        live: false,
                    });
                }
            }
        }
        if let Some(pe) = self.pentry
            && pe.expiry_bar.map(|e| t >= e).unwrap_or(false)
        {
            self.pentry = None;
            self.reject(R_EXPIRED);
        }
        Ok(())
    }

    fn trail_and_time_stop(&mut self, close: i64, bar_close_ts: i64) {
        if let Some(tr) = self.active.trail {
            let long = self.pos > 0;
            if (long && close > self.trail_ref) || (!long && close < self.trail_ref) {
                self.trail_ref = close;
            }
            let lvl = round_to_tick(
                (self.trail_ref as f64 * (1.0 - if long { tr } else { -tr })).round() as i64,
                self.tick,
                !long,
            );
            let new = match self.active.stop {
                Some(s) if long => s.max(lvl),
                Some(s) => s.min(lvl),
                None => lvl,
            };
            if Some(new) != self.active.stop && self.active.stop.is_some() {
                self.trail_moved = true;
            }
            self.active.stop = Some(new);
        }
        if let Some(ts_bars) = self.active.time_stop
            && self.bars_in_trade >= ts_bars
            && self.pexit.is_none()
        {
            self.pexit =
                Some(PendingExit { activation: bar_close_ts + self.spec.decision_latency_s, reason: Reason::TimeStop });
        }
    }

    /// P4: mark to market at the close.
    fn mark(&mut self, t: usize, ls: &mut LoopState, bars: &Bars, day: i64) {
        let close = bars.close[t];
        let eq = self.equity_at(close);
        match self.out.fused.as_mut() {
            Some(acc) => acc.bar(eq),
            None => self.out.equity.push(eq),
        }
        ls.last_close = Some(close);
        ls.prev_day = day;
    }

    fn finish(&mut self, src: &mut dyn DecisionSource) {
        // audit 2 A2-H1: a run where most entry decisions were refused is flagged, never silent
        let refused = self.decision_rejects + self.out.rejects[R_FUNDS] as u64 + self.out.rejects[R_ZERO_VOL] as u64;
        if self.decisions >= 4 && refused * 2 >= self.decisions {
            let names: Vec<String> = REJECT_NAMES
                .iter()
                .zip(self.out.rejects.iter())
                .filter(|(_, v)| **v > 0)
                .map(|(k, v)| format!("{k} {v}"))
                .collect();
            self.out.warnings.push(format!(
                "{refused} of {} entry decisions were refused ({}) - check the ExecSpec session times, capital and product",
                self.decisions,
                names.join(", ")
            ));
        }
        self.out.counters.push(("ca_applied", self.ca_applied));
        self.out.counters.push(("entry_decisions", self.decisions));
        self.out.counters.extend(src.counters());
        self.out.cash = self.cash;
        self.out.position = self.pos;
        if self.pos != 0 {
            self.out.active_stop = self.active.stop;
            self.out.active_target = self.active.target;
        }
    }
}

/// Loop state carried from bar to bar (indices into the inputs, session bookkeeping, reused buffers).
struct LoopState {
    intraday: bool,
    use_m1: bool,
    cutoff_at: Option<i32>,
    squareoff_at: Option<i32>,
    shorts: bool,
    ev_ptr: usize,
    ch_ptr: usize,
    mptr: usize,
    prev_day: i64,
    prev_day_close: Option<i64>,
    last_close: Option<i64>,
    segs: Vec<Seg>,
    intents: Vec<Intent>,
}

/// One engine run, bar by bar (see `Engine::start`).
pub struct Stepper<'a, 'b> {
    e: Engine<'a>,
    ls: LoopState,
    bars: &'b Bars<'b>,
    m1: Option<&'b Bars<'b>>,
    events: &'b [CaEvent],
    changes: &'b [InstrumentChange],
    t: usize,
}

impl Stepper<'_, '_> {
    /// Timestamp of the next bar, None at the end.
    pub fn next_ts(&self) -> Option<i64> {
        (self.t < self.bars.len()).then(|| self.bars.ts[self.t])
    }

    /// Process the next bar. Returns false when there was none.
    #[inline]
    pub fn step(&mut self, src: &mut dyn DecisionSource) -> Result<bool, String> {
        if self.t >= self.bars.len() {
            return Ok(false);
        }
        let (bars, m1, events, changes) = (self.bars, self.m1, self.events, self.changes);
        self.e.bar(self.t, &mut self.ls, bars, m1, events, changes, src)?;
        self.t += 1;
        Ok(true)
    }

    /// This leg's cash ledger (paise).
    pub fn cash(&self) -> Paise {
        self.e.cash
    }

    pub fn position(&self) -> i64 {
        self.e.pos
    }

    /// The open position marked at the last processed close (paise, signed).
    pub fn mark_value(&self) -> Paise {
        match self.ls.last_close {
            Some(c) => notional(self.e.pos, c, self.e.inst.price_scale),
            None => 0,
        }
    }

    /// The rest of the shared account as this leg must see it before its next bar (portfolio mode).
    pub fn set_account(&mut self, cash: Paise, equity: Paise, gross: Paise, open: u32) {
        self.e.ext_cash = cash;
        self.e.ext_equity = equity;
        self.e.ext_gross = gross;
        self.e.ext_open = open;
    }

    pub fn finish(mut self, src: &mut dyn DecisionSource) -> Outcome {
        self.e.finish(src);
        self.e.out
    }
}
