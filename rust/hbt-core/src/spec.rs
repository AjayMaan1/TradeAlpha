//! ExecSpec v1 (design Appendix B): every semantic choice of a run, explicit and versioned. The serde defaults are
//! the conservative fill semantics of decision D-09; session times (14:55 cutoff, 15:10 square-off) and the intraday
//! participation cap depend on exchange and bar size, so the Python builder (`lab/hbt/spec.py`) always sends them
//! explicitly and hashes the resolved canonical JSON into the run manifest.

use serde::Deserialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FillTiming {
    NextOpen,
    Close,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IntrabarPolicy {
    /// adverse leg first (stop before target) inside an OHLC bar
    Pessimistic,
    /// close >= open: O -> L -> H -> C, else O -> H -> L -> C
    OhlcDirection,
    /// replay the stored 1m bars of a coarser bar; pessimistic inside a single ambiguous 1m bar
    #[serde(rename = "magnifier_1m")]
    Magnifier1m,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SameBarPriority {
    /// open events (square-off, signal exits, entries) before intrabar protective orders - like a real session
    Chronological,
    /// protective stops before open-executed signal exits (vectorbt-compatible, for comparisons only)
    StopFirst,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SlippageModel {
    None,
    Ticks,
    Pct,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Remainder {
    /// the unfilled part of a capped fill is carried to the next segment (entries and exits)
    Carry,
    /// the unfilled part is cancelled (entries and exits: a position may stay partly open)
    Cancel,
    /// entries: cancelled (a position is never built beyond the bar's participation share); exits: carried until
    /// flat - the flagfix2 P3 simulator's rule, ADR-001 (unified defaults)
    CancelEntries,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum Product {
    Mis,
    Cnc,
    Nrml,
}

fn d_latency() -> i64 {
    0
}
fn d_true() -> bool {
    true
}
fn d_one() -> f64 {
    1.0
}

#[derive(Debug, Clone, Deserialize)]
pub struct ExecSpec {
    #[serde(default = "d_version")]
    pub exec_spec_version: u32,
    #[serde(default = "d_fill")]
    pub fill_timing: FillTiming,
    /// seconds between the signal bar's close and the order reaching the market (paper-measured, D-23)
    #[serde(default = "d_latency")]
    pub decision_latency_s: i64,
    #[serde(default = "d_policy")]
    pub intrabar_policy: IntrabarPolicy,
    #[serde(default = "d_true")]
    pub bracket_live_on_entry_bar: bool,
    #[serde(default = "d_priority")]
    pub same_bar_priority: SameBarPriority,
    #[serde(default = "d_slip")]
    pub slippage_model: SlippageModel,
    #[serde(default)]
    pub slippage_value: f64,
    /// flagfix2 slippage refinements (ADR-001; all off by default): at least this many ticks of slippage ...
    #[serde(default)]
    pub slippage_min_ticks: f64,
    /// ... x `slippage_edge_widen` for fills in the first / last `slippage_edge_minutes` of the session (daily bars:
    /// fills at the open) ...
    #[serde(default)]
    pub slippage_edge_minutes: i32,
    #[serde(default = "d_one")]
    pub slippage_edge_widen: f64,
    /// ... and the slipped price never leaves the executing bar's range (low .. high, tick-rounded inwards)
    #[serde(default)]
    pub slippage_clip: bool,
    /// the session (minutes after midnight IST) the edge rule refers to (NSE cash 09:15 - 15:30)
    #[serde(default = "d_sopen")]
    pub session_open_min: i32,
    #[serde(default = "d_sclose")]
    pub session_close_min: i32,
    /// max share of a (1m or coarse) bar's volume one fill may take; None = unlimited
    #[serde(default)]
    pub participation_cap: Option<f64>,
    #[serde(default = "d_rem")]
    pub participation_remainder: Remainder,
    #[serde(default)]
    pub zero_volume_fills: bool,
    /// minutes after midnight IST: no new entry decision on bars CLOSING after this time (None = no limit)
    #[serde(default)]
    pub no_new_entries_after: Option<i32>,
    /// minutes after midnight IST: MIS positions are closed at the first bar opening at/after this time
    #[serde(default)]
    pub squareoff_at: Option<i32>,
    #[serde(default = "d_product")]
    pub product: Product,
    #[serde(default)]
    pub allow_short: Option<bool>,
    #[serde(default = "d_one")]
    pub leverage: f64,
    /// static price band of the instrument (e.g. 0.05): a bar locked at the band blocks fills on that side
    #[serde(default)]
    pub price_band_pct: Option<f64>,
    /// cash dividends on the ex-date: credited to longs / debited to shorts (raw execution, design §9.3)
    #[serde(default = "d_div")]
    pub dividends: Dividends,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Dividends {
    Credit,
    Ignore,
}

fn d_sopen() -> i32 {
    555
}
fn d_sclose() -> i32 {
    930
}
fn d_div() -> Dividends {
    Dividends::Credit
}

fn d_version() -> u32 {
    1
}
fn d_fill() -> FillTiming {
    FillTiming::NextOpen
}
fn d_policy() -> IntrabarPolicy {
    IntrabarPolicy::Pessimistic
}
fn d_priority() -> SameBarPriority {
    SameBarPriority::Chronological
}
fn d_slip() -> SlippageModel {
    SlippageModel::None
}
fn d_rem() -> Remainder {
    Remainder::Carry
}
fn d_product() -> Product {
    Product::Mis
}

impl ExecSpec {
    pub fn shorts_allowed(&self) -> bool {
        self.allow_short.unwrap_or(self.product != Product::Cnc) && self.product != Product::Cnc
    }
    /// audit 2 A2-M4: every field is range-checked (a typo must never become a silent nonsense run)
    pub fn validate(&self) -> Result<(), String> {
        if self.exec_spec_version != 1 {
            return Err(format!("unsupported exec_spec_version {}", self.exec_spec_version));
        }
        if self.decision_latency_s < 0 || self.decision_latency_s > 3600 {
            return Err("decision_latency_s must be within 0..3600".into());
        }
        if let Some(c) = self.participation_cap
            && !(c > 0.0 && c <= 1.0)
        {
            return Err("participation_cap must be in (0, 1]".into());
        }
        if !(1.0..=20.0).contains(&self.leverage) {
            return Err("leverage must be within 1..20".into());
        }
        match self.slippage_model {
            SlippageModel::None => {}
            SlippageModel::Ticks if !(0.0..=100.0).contains(&self.slippage_value) => {
                return Err("slippage_value (ticks) must be within 0..100".into());
            }
            SlippageModel::Pct if !(0.0..=0.05).contains(&self.slippage_value) => {
                return Err("slippage_value (pct) must be within 0..0.05 (5 %)".into());
            }
            _ => {}
        }
        if !(0.0..=100.0).contains(&self.slippage_min_ticks) || !(1.0..=10.0).contains(&self.slippage_edge_widen) {
            return Err("slippage_min_ticks must be within 0..100 and slippage_edge_widen within 1..10".into());
        }
        if !(0..=180).contains(&self.slippage_edge_minutes)
            || !(0..1440).contains(&self.session_open_min)
            || !(self.session_open_min..1440).contains(&self.session_close_min)
        {
            return Err("slippage_edge_minutes must be within 0..180 and the session open < close".into());
        }
        for (name, v) in [("no_new_entries_after", self.no_new_entries_after), ("squareoff_at", self.squareoff_at)] {
            if let Some(m) = v
                && !(0..1440).contains(&m)
            {
                return Err(format!("{name} must be a time of day (minutes 0..1439), got {m}"));
            }
        }
        if let Some(b) = self.price_band_pct
            && !(b > 0.0 && b <= 0.5)
        {
            return Err("price_band_pct must be in (0, 0.5]".into());
        }
        if self.product == Product::Cnc && self.allow_short == Some(true) {
            return Err("CNC (delivery) cannot sell short".into());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EntryType {
    Market,
    Limit,
    Stop,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SizingMode {
    /// all available cash (x leverage), whole lots, leaving room for the charges
    AllIn,
    FixedQty,
    /// rupees of notional
    FixedValue,
    /// fraction of current equity
    PctEquity,
    /// fraction of equity at risk between the entry and the stop (needs a stop)
    RiskPct,
}

fn d_entry() -> EntryType {
    EntryType::Market
}
fn d_sizing() -> SizingMode {
    SizingMode::AllIn
}

/// What the strategy's signals turn into (the Strategy IR of phase 1.3 compiles to this).
#[derive(Debug, Clone, Deserialize)]
pub struct OrderPlan {
    #[serde(default = "d_entry")]
    pub entry_type: EntryType,
    /// limit: buy below / sell above the signal close by this fraction; stop: buy above / sell below
    #[serde(default)]
    pub entry_offset_pct: f64,
    /// resting limit / stop entries are cancelled after this many bars (0 = end of session)
    #[serde(default)]
    pub entry_expiry_bars: u32,
    #[serde(default)]
    pub stop_pct: Option<f64>,
    #[serde(default)]
    pub target_pct: Option<f64>,
    #[serde(default)]
    pub trail_pct: Option<f64>,
    #[serde(default)]
    pub time_stop_bars: Option<u32>,
    #[serde(default = "d_sizing")]
    pub sizing: SizingMode,
    #[serde(default)]
    pub sizing_value: f64,
}

impl OrderPlan {
    pub fn validate(&self) -> Result<(), String> {
        for (name, v) in [("stop_pct", self.stop_pct), ("target_pct", self.target_pct), ("trail_pct", self.trail_pct)] {
            if let Some(x) = v
                && !(x > 0.0 && x < 1.0)
            {
                return Err(format!("{name} must be in (0, 1), got {x}"));
            }
        }
        if !(0.0..0.5).contains(&self.entry_offset_pct) {
            return Err(format!("entry_offset_pct must be in [0, 0.5), got {}", self.entry_offset_pct));
        }
        if let Some(0) = self.time_stop_bars {
            return Err("time_stop_bars must be >= 1".into());
        }
        let v = self.sizing_value;
        let ok = match self.sizing {
            SizingMode::AllIn => true,
            SizingMode::FixedQty => v >= 1.0 && v.fract() == 0.0,
            SizingMode::FixedValue => v > 0.0,
            SizingMode::PctEquity => v > 0.0 && v <= 20.0,
            SizingMode::RiskPct => v > 0.0 && v < 1.0,
        };
        if !ok {
            return Err(format!("sizing_value {v} is out of range for {:?}", self.sizing));
        }
        if self.sizing == SizingMode::RiskPct && self.stop_pct.is_none() {
            return Err("risk_pct sizing needs stop_pct".into());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct Instrument {
    #[serde(default = "d_lot")]
    pub lot: i64,
    /// tick size in price units (NSE equities: 5 = Rs 0.05)
    #[serde(default = "d_lot")]
    pub tick: i64,
    /// price units per rupee (100 = paise)
    #[serde(default = "d_scale")]
    pub price_scale: i64,
}

fn d_lot() -> i64 {
    1
}
fn d_scale() -> i64 {
    100
}
