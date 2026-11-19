//! Operator catalogue v1 (design §10 + Appendix C). One entry per IR function name: argument kinds, outputs, result
//! type, class and lookback. The evaluator (hbt-core::kernels) implements one streaming kernel per `Op`.
//!
//! Every operator here is CAUSAL: its value at bar t depends on inputs at bars <= t only. The look-ahead fuzz harness
//! checks this claim for every operator (tests/lab/test_lookahead_fuzz.py).

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Op {
    // leaves
    Const,
    Col(Column),
    // arithmetic / logic (stateless)
    Add,
    Sub,
    Mul,
    Div,
    Min,
    Max,
    Pow,
    Neg,
    Abs,
    Log,
    Exp,
    Sqrt,
    Clip,
    Where,
    Gt,
    Ge,
    Lt,
    Le,
    Eq,
    Ne,
    Between,
    And,
    Or,
    Not,
    // series (stateful)
    Shift,
    Diff,
    PctChange,
    RollingMean,
    RollingSum,
    RollingStd,
    RollingMin,
    RollingMax,
    RollingQuantile,
    ExpandingMax,
    ExpandingMin,
    Cumsum,
    BarsSince,
    Rising,
    Falling,
    CrossAbove,
    CrossBelow,
    // indicators on a series
    Sma,
    Ema,
    Wma,
    Dema,
    Tema,
    Hma,
    Rsi,
    Roc,
    Mom,
    Stddev,
    Zscore,
    LinregSlope,
    Bbands,
    Macd,
    // indicators on the frame's OHLCV
    Atr,
    Natr,
    Cci,
    Willr,
    Mfi,
    Stoch,
    Adx,
    Keltner,
    Donchian,
    Obv,
    Vwap,
    // session functions (run interval only)
    MinuteOfDay,
    BarInSession,
    IsFirstBar,
    IsLastBar,
    SessionOpenPrice,
    SessionHigh,
    SessionLow,
    OpeningRangeHigh,
    OpeningRangeLow,
    PriorDayOpen,
    PriorDayHigh,
    PriorDayLow,
    PriorDayClose,
    DayOfWeek,
    // read-only position state (run interval only, evaluated after intrabar execution)
    PositionQty,
    EntryPrice,
    BarsInTrade,
    UnrealisedPct,
    HighestSinceEntry,
    LowestSinceEntry,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub enum Column {
    Open,
    High,
    Low,
    Close,
    Volume,
    Typical,
    Hl2,
    /// a user-supplied column of the dataset (`{"col": "$name"}`, run interval only)
    Extra(u16),
}

/// Argument kinds.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum A {
    /// numeric series (a scalar broadcasts)
    S,
    /// boolean series (a scalar bool broadcasts)
    B,
    /// scalar number
    N,
    /// scalar integer >= 1 (window, shift, period)
    I,
    /// scalar in [0, 1]
    Q,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Ret {
    Num,
    Bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Class {
    /// stateless arithmetic / comparison / logic: folded when all inputs are constant
    Pure,
    /// stateful series operator or indicator
    Series,
    /// implicit OHLCV inputs of its frame (accepts "tf")
    Ohlc,
    /// session function (run interval only)
    Session,
    /// read-only position state (run interval only)
    Position,
}

pub struct OpDef {
    pub name: &'static str,
    pub op: Op,
    /// required arguments
    pub args: &'static [A],
    /// optional trailing arguments with defaults
    pub opt: &'static [(A, f64)],
    /// variadic: at least `args.len()` arguments of kind args[0]
    pub variadic: bool,
    /// output names (empty = one unnamed output)
    pub outs: &'static [&'static str],
    pub ret: Ret,
    pub class: Class,
}

const fn d(
    name: &'static str,
    op: Op,
    args: &'static [A],
    opt: &'static [(A, f64)],
    outs: &'static [&'static str],
    ret: Ret,
    class: Class,
) -> OpDef {
    OpDef { name, op, args, opt, variadic: false, outs, ret, class }
}

use A::*;
use Class::*;
use Ret::*;

pub static OPS: &[OpDef] = &[
    d("add", Op::Add, &[S, S], &[], &[], Num, Pure),
    d("sub", Op::Sub, &[S, S], &[], &[], Num, Pure),
    d("mul", Op::Mul, &[S, S], &[], &[], Num, Pure),
    d("div", Op::Div, &[S, S], &[], &[], Num, Pure),
    d("min", Op::Min, &[S, S], &[], &[], Num, Pure),
    d("max", Op::Max, &[S, S], &[], &[], Num, Pure),
    d("pow", Op::Pow, &[S, S], &[], &[], Num, Pure),
    d("neg", Op::Neg, &[S], &[], &[], Num, Pure),
    d("abs", Op::Abs, &[S], &[], &[], Num, Pure),
    d("log", Op::Log, &[S], &[], &[], Num, Pure),
    d("exp", Op::Exp, &[S], &[], &[], Num, Pure),
    d("sqrt", Op::Sqrt, &[S], &[], &[], Num, Pure),
    d("clip", Op::Clip, &[S, S, S], &[], &[], Num, Pure),
    d("where", Op::Where, &[B, S, S], &[], &[], Num, Pure),
    d("gt", Op::Gt, &[S, S], &[], &[], Bool, Pure),
    d("ge", Op::Ge, &[S, S], &[], &[], Bool, Pure),
    d("lt", Op::Lt, &[S, S], &[], &[], Bool, Pure),
    d("le", Op::Le, &[S, S], &[], &[], Bool, Pure),
    d("eq", Op::Eq, &[S, S], &[], &[], Bool, Pure),
    d("ne", Op::Ne, &[S, S], &[], &[], Bool, Pure),
    d("between", Op::Between, &[S, S, S], &[], &[], Bool, Pure),
    OpDef { name: "and", op: Op::And, args: &[B, B], opt: &[], variadic: true, outs: &[], ret: Bool, class: Pure },
    OpDef { name: "or", op: Op::Or, args: &[B, B], opt: &[], variadic: true, outs: &[], ret: Bool, class: Pure },
    d("not", Op::Not, &[B], &[], &[], Bool, Pure),
    d("shift", Op::Shift, &[S, I], &[], &[], Num, Series),
    d("diff", Op::Diff, &[S], &[(I, 1.0)], &[], Num, Series),
    d("pct_change", Op::PctChange, &[S], &[(I, 1.0)], &[], Num, Series),
    d("rolling_mean", Op::RollingMean, &[S, I], &[], &[], Num, Series),
    d("rolling_sum", Op::RollingSum, &[S, I], &[], &[], Num, Series),
    d("rolling_std", Op::RollingStd, &[S, I], &[], &[], Num, Series),
    d("rolling_min", Op::RollingMin, &[S, I], &[], &[], Num, Series),
    d("rolling_max", Op::RollingMax, &[S, I], &[], &[], Num, Series),
    d("rolling_quantile", Op::RollingQuantile, &[S, I, Q], &[], &[], Num, Series),
    d("expanding_max", Op::ExpandingMax, &[S], &[], &[], Num, Series),
    d("expanding_min", Op::ExpandingMin, &[S], &[], &[], Num, Series),
    d("cumsum", Op::Cumsum, &[S], &[], &[], Num, Series),
    d("bars_since", Op::BarsSince, &[B], &[], &[], Num, Series),
    d("rising", Op::Rising, &[S], &[(I, 1.0)], &[], Bool, Series),
    d("falling", Op::Falling, &[S], &[(I, 1.0)], &[], Bool, Series),
    d("cross_above", Op::CrossAbove, &[S, S], &[], &[], Bool, Series),
    d("cross_below", Op::CrossBelow, &[S, S], &[], &[], Bool, Series),
    d("sma", Op::Sma, &[S, I], &[], &[], Num, Series),
    d("ema", Op::Ema, &[S, I], &[], &[], Num, Series),
    d("wma", Op::Wma, &[S, I], &[], &[], Num, Series),
    d("dema", Op::Dema, &[S, I], &[], &[], Num, Series),
    d("tema", Op::Tema, &[S, I], &[], &[], Num, Series),
    d("hma", Op::Hma, &[S, I], &[], &[], Num, Series),
    d("rsi", Op::Rsi, &[S], &[(I, 14.0)], &[], Num, Series),
    d("roc", Op::Roc, &[S], &[(I, 10.0)], &[], Num, Series),
    d("mom", Op::Mom, &[S], &[(I, 10.0)], &[], Num, Series),
    d("stddev", Op::Stddev, &[S, I], &[], &[], Num, Series),
    d("zscore", Op::Zscore, &[S, I], &[], &[], Num, Series),
    d("linreg_slope", Op::LinregSlope, &[S, I], &[], &[], Num, Series),
    d("bbands", Op::Bbands, &[S], &[(I, 20.0), (N, 2.0)], &["upper", "mid", "lower"], Num, Series),
    d("macd", Op::Macd, &[S], &[(I, 12.0), (I, 26.0), (I, 9.0)], &["macd", "signal", "hist"], Num, Series),
    d("atr", Op::Atr, &[], &[(I, 14.0)], &[], Num, Ohlc),
    d("natr", Op::Natr, &[], &[(I, 14.0)], &[], Num, Ohlc),
    d("cci", Op::Cci, &[], &[(I, 14.0)], &[], Num, Ohlc),
    d("willr", Op::Willr, &[], &[(I, 14.0)], &[], Num, Ohlc),
    d("mfi", Op::Mfi, &[], &[(I, 14.0)], &[], Num, Ohlc),
    d("stoch", Op::Stoch, &[], &[(I, 5.0), (I, 3.0), (I, 3.0)], &["k", "d"], Num, Ohlc),
    d("adx", Op::Adx, &[], &[(I, 14.0)], &["adx", "plus_di", "minus_di"], Num, Ohlc),
    d("keltner", Op::Keltner, &[], &[(I, 20.0), (N, 2.0)], &["upper", "mid", "lower"], Num, Ohlc),
    d("donchian", Op::Donchian, &[], &[(I, 20.0)], &["upper", "lower"], Num, Ohlc),
    d("obv", Op::Obv, &[], &[], &[], Num, Ohlc),
    d("vwap", Op::Vwap, &[], &[], &[], Num, Session),
    d("minute_of_day", Op::MinuteOfDay, &[], &[], &[], Num, Session),
    d("bar_in_session", Op::BarInSession, &[], &[], &[], Num, Session),
    d("is_first_bar", Op::IsFirstBar, &[], &[], &[], Bool, Session),
    d("is_last_bar", Op::IsLastBar, &[], &[], &[], Bool, Session),
    d("session_open_price", Op::SessionOpenPrice, &[], &[], &[], Num, Session),
    d("session_high", Op::SessionHigh, &[], &[], &[], Num, Session),
    d("session_low", Op::SessionLow, &[], &[], &[], Num, Session),
    d("opening_range_high", Op::OpeningRangeHigh, &[I], &[], &[], Num, Session),
    d("opening_range_low", Op::OpeningRangeLow, &[I], &[], &[], Num, Session),
    d("prior_day_open", Op::PriorDayOpen, &[], &[], &[], Num, Session),
    d("prior_day_high", Op::PriorDayHigh, &[], &[], &[], Num, Session),
    d("prior_day_low", Op::PriorDayLow, &[], &[], &[], Num, Session),
    d("prior_day_close", Op::PriorDayClose, &[], &[], &[], Num, Session),
    d("day_of_week", Op::DayOfWeek, &[], &[], &[], Num, Session),
    d("position_qty", Op::PositionQty, &[], &[], &[], Num, Position),
    d("entry_price", Op::EntryPrice, &[], &[], &[], Num, Position),
    d("bars_in_trade", Op::BarsInTrade, &[], &[], &[], Num, Position),
    d("unrealised_pct", Op::UnrealisedPct, &[], &[], &[], Num, Position),
    d("highest_since_entry", Op::HighestSinceEntry, &[], &[], &[], Num, Position),
    d("lowest_since_entry", Op::LowestSinceEntry, &[], &[], &[], Num, Position),
];

/// Version of the operator catalogue (additions bump it; it is recorded in the run manifest).
pub const OPS_VERSION: u32 = 1;

pub fn lookup(name: &str) -> Option<&'static OpDef> {
    OPS.iter().find(|o| o.name == name)
}

pub fn def_of(op: Op) -> Option<&'static OpDef> {
    OPS.iter().find(|o| o.op == op)
}

/// Lookback in bars of the node's own frame, from its integer parameters (design §7.5). `bars_per_session` is needed
/// by the prior-day functions.
pub fn own_lookback(op: Op, ip: &[i64], bars_per_session: usize) -> usize {
    let p = |i: usize| ip.get(i).copied().unwrap_or(1).max(1) as usize;
    match op {
        Op::Shift | Op::Diff | Op::PctChange | Op::Rising | Op::Falling | Op::Roc | Op::Mom => p(0),
        Op::RollingMean
        | Op::RollingSum
        | Op::RollingStd
        | Op::RollingMin
        | Op::RollingMax
        | Op::RollingQuantile
        | Op::Sma
        | Op::Ema
        | Op::Wma
        | Op::Stddev
        | Op::Zscore
        | Op::LinregSlope
        | Op::Bbands
        | Op::Cci
        | Op::Willr
        | Op::Donchian => p(0) - 1,
        Op::CrossAbove | Op::CrossBelow => 1,
        Op::Dema => 2 * (p(0) - 1),
        Op::Tema => 3 * (p(0) - 1),
        Op::Hma => p(0) - 1 + ((p(0) as f64).sqrt().floor() as usize).max(1) - 1,
        Op::Rsi | Op::Atr | Op::Natr | Op::Mfi | Op::Keltner => p(0),
        Op::Macd => p(1).max(p(0)) - 1 + p(2) - 1,
        Op::Stoch => p(0) - 1 + p(1) - 1 + p(2) - 1,
        Op::Adx => 2 * p(0) - 1,
        Op::PriorDayOpen | Op::PriorDayHigh | Op::PriorDayLow | Op::PriorDayClose => bars_per_session,
        _ => 0,
    }
}

/// Interval codes in increasing order (frame ranks).
pub const FRAMES: &[(&str, i64)] = &[
    ("1m", 60),
    ("3m", 180),
    ("5m", 300),
    ("10m", 600),
    ("15m", 900),
    ("30m", 1_800),
    ("1h", 3_600),
    ("D", 86_400),
    ("W", 604_800),
    ("M", 2_592_000),
];

pub fn frame_rank(code: &str) -> Option<u8> {
    let c = match code {
        "1d" | "d" | "day" => "D",
        "1w" | "w" | "week" => "W",
        "month" => "M",
        "60m" => "1h",
        other => other,
    };
    FRAMES.iter().position(|(k, _)| *k == c).map(|i| i as u8)
}

pub fn frame_code(rank: u8) -> &'static str {
    FRAMES[rank as usize].0
}

/// Bars of the `base` frame per bar of `frame` (conservative, for warm-up). NSE session = 375 minutes.
pub fn base_bars_per(frame: u8, base: u8) -> usize {
    if frame <= base {
        return 1;
    }
    let secs = |r: u8| FRAMES[r as usize].1;
    let per_session = |r: u8| if secs(r) >= 86_400 { 1 } else { (375 * 60 + secs(r) - 1) / secs(r) } as usize;
    let sessions_of = |r: u8| match frame_code(r) {
        "D" => 1usize,
        "W" => 5,
        "M" => 23,
        _ => 0,
    };
    if secs(frame) < 86_400 {
        ((secs(frame) + secs(base) - 1) / secs(base)) as usize
    } else {
        sessions_of(frame) * per_session(base) / if secs(base) >= 86_400 { sessions_of(base).max(1) } else { 1 }
    }
}
