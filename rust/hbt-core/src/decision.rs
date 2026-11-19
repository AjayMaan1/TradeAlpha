//! Where decisions come from (design §5). The engine asks a `DecisionSource` once per bar, after intrabar execution
//! (P2) and before the close phase (P3); the source answers with intents. Sources: `ArraySignals` (the 1.2 API: four
//! boolean arrays + OrderPlan defaults), `eval::IrSource` (compiled Strategy IR), the Python tier (hbt-py).

pub use hbt_ir::Ov;
pub use hbt_ir::ast::{IntentKind, OrderType};

use crate::spec::{EntryType, OrderPlan, SizingMode};

/// A price level for a bracket leg. `Price` is in RAW price units (the source converts from the signal scale).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Level {
    Pct(f64),
    Price(f64),
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Sizing {
    AllIn,
    PctEquity(f64),
    FixedQty(i64),
    FixedValue(f64),
    RiskPct(f64),
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Intent {
    pub kind: IntentKind,
    pub order: OrderType,
    /// limit / stop price in raw price units (None: offset_pct from the close, or the OrderPlan default)
    pub price: Option<f64>,
    /// stop-limit: the limit leg in raw price units (None = limit at the trigger)
    pub limit: Option<f64>,
    pub offset_pct: Option<f64>,
    pub size: Option<Sizing>,
    pub stop: Ov<Level>,
    pub target: Ov<Level>,
    pub trail_pct: Ov<f64>,
    pub time_stop_bars: Ov<u32>,
    pub expiry_bars: Option<u32>,
}

impl Intent {
    pub fn exit(kind: IntentKind) -> Self {
        Intent {
            kind,
            order: OrderType::Market,
            price: None,
            limit: None,
            offset_pct: None,
            size: None,
            stop: Ov::Default,
            target: Ov::Default,
            trail_pct: Ov::Default,
            time_stop_bars: Ov::Default,
            expiry_bars: None,
        }
    }
    /// an entry that takes everything from the OrderPlan
    pub fn entry(kind: IntentKind) -> Self {
        Intent { order: OrderType::Market, ..Intent::exit(kind) }
    }
}

/// What the engine tells the source about the bar it just finished.
#[derive(Debug, Clone, Copy)]
pub struct BarCtx {
    pub t: usize,
    pub n: usize,
    pub day: i64,
}

/// Position after intrabar execution of bar t (raw price units).
#[derive(Debug, Clone, Copy, Default)]
pub struct PosState {
    pub qty: i64,
    /// average entry price (raw units); 0 when flat
    pub avg_entry: f64,
    pub bars_in_trade: u32,
    pub highest: i64,
    pub lowest: i64,
    pub close: i64,
}

pub trait DecisionSource {
    fn decide(&mut self, ctx: &BarCtx, pos: &PosState, out: &mut Vec<Intent>) -> Result<(), String>;
    fn on_fill(&mut self, _bar: usize, _buy: bool, _qty: i64, _price: i64) {}
    /// counters reported in the run result (name, value)
    fn counters(&self) -> Vec<(&'static str, u64)> {
        vec![]
    }
}

/// The Phase 1.2 API: boolean arrays + the OrderPlan. Long wins over short on the same bar (reported as a conflict).
pub struct ArraySignals<'a> {
    pub long_entry: &'a [u8],
    pub long_exit: &'a [u8],
    pub short_entry: &'a [u8],
    pub short_exit: &'a [u8],
    pub plan: &'a OrderPlan,
    conflicts: u64,
}

impl<'a> ArraySignals<'a> {
    pub fn new(le: &'a [u8], lx: &'a [u8], se: &'a [u8], sx: &'a [u8], plan: &'a OrderPlan) -> Self {
        ArraySignals { long_entry: le, long_exit: lx, short_entry: se, short_exit: sx, plan, conflicts: 0 }
    }
    pub fn check(&self, n: usize) -> Result<(), String> {
        for (name, l) in [
            ("long_entry", self.long_entry.len()),
            ("long_exit", self.long_exit.len()),
            ("short_entry", self.short_entry.len()),
            ("short_exit", self.short_exit.len()),
        ] {
            if l != n {
                return Err(format!("signals.{name} has {l} rows, bars have {n}"));
            }
        }
        Ok(())
    }
}

pub fn plan_entry(plan: &OrderPlan, kind: IntentKind) -> Intent {
    Intent {
        order: match plan.entry_type {
            EntryType::Market => OrderType::Market,
            EntryType::Limit => OrderType::Limit,
            EntryType::Stop => OrderType::Stop,
        },
        offset_pct: (plan.entry_type != EntryType::Market).then_some(plan.entry_offset_pct),
        ..Intent::entry(kind)
    }
}

impl DecisionSource for ArraySignals<'_> {
    fn decide(&mut self, ctx: &BarCtx, pos: &PosState, out: &mut Vec<Intent>) -> Result<(), String> {
        let t = ctx.t;
        if pos.qty > 0 && self.long_exit[t] != 0 {
            out.push(Intent::exit(IntentKind::ExitLong));
        }
        if pos.qty < 0 && self.short_exit[t] != 0 {
            out.push(Intent::exit(IntentKind::ExitShort));
        }
        let (l, s) = (self.long_entry[t] != 0, self.short_entry[t] != 0);
        if l && s {
            self.conflicts += 1;
        }
        if l {
            out.push(plan_entry(self.plan, IntentKind::EnterLong));
        } else if s {
            out.push(plan_entry(self.plan, IntentKind::EnterShort));
        }
        Ok(())
    }
    fn counters(&self) -> Vec<(&'static str, u64)> {
        vec![("entry_conflicts", self.conflicts)]
    }
}

impl From<(SizingMode, f64)> for Sizing {
    fn from((m, v): (SizingMode, f64)) -> Self {
        match m {
            SizingMode::AllIn => Sizing::AllIn,
            SizingMode::FixedQty => Sizing::FixedQty(v as i64),
            SizingMode::FixedValue => Sizing::FixedValue(v),
            SizingMode::PctEquity => Sizing::PctEquity(v),
            SizingMode::RiskPct => Sizing::RiskPct(v),
        }
    }
}
