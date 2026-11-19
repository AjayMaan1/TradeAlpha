//! Strategy IR evaluation (design §8): market nodes stepped bar by bar (higher timeframes through `available_at`),
//! position nodes inside the engine's decision phase, rules -> intents.
//!
//! * `MarketEval::advance(t)` is THE evaluation step: it first processes every higher-timeframe bar whose
//!   `available_at` <= the decision time of base bar t (coarser frames first on ties), then the base-frame nodes.
//!   Streaming (paper) calls it as bars arrive; batch calls it for t = 0..n. Same code, same values.
//! * `IrSource` is the `DecisionSource` of a compiled variant: market slots come live from a `MarketEval`, or from a
//!   matrix of SHARED nodes precomputed once for all variants of a sweep; nodes private to one variant (e.g. its
//!   cross of two shared EMAs) are stepped inside that variant's own loop (design §7.6).

use hbt_ir::ast::{OrderType, SizeMode};
use hbt_ir::compile::{Lvl, Ov, Plan, VariantPlan};
use hbt_ir::ops::FRAMES;

use crate::decision::{BarCtx, DecisionSource, Intent, Level, PosState, Sizing};
use crate::kernels::{BarIn, Kernel, PosIn};

/// OHLCV + availability of one frame, signal scale (f64 rupees).
#[derive(Clone, Copy)]
pub struct Frame<'a> {
    pub ts: &'a [i64],
    pub open: &'a [f64],
    pub high: &'a [f64],
    pub low: &'a [f64],
    pub close: &'a [f64],
    pub volume: &'a [f64],
    /// epoch seconds at which each bar is final in Historify (design §6.5)
    pub avail: &'a [i64],
}

impl Frame<'_> {
    pub fn len(&self) -> usize {
        self.ts.len()
    }
    pub fn is_empty(&self) -> bool {
        self.ts.is_empty()
    }
    fn check(&self, what: &str) -> Result<(), String> {
        let n = self.ts.len();
        for (k, l) in [
            ("open", self.open.len()),
            ("high", self.high.len()),
            ("low", self.low.len()),
            ("close", self.close.len()),
            ("volume", self.volume.len()),
            ("available_at", self.avail.len()),
        ] {
            if l != n {
                return Err(format!("{what}.{k} has {l} rows, ts has {n}"));
            }
        }
        for i in 1..n {
            if self.ts[i] <= self.ts[i - 1] {
                return Err(format!("{what}: ts not strictly ascending at row {i}"));
            }
            if self.avail[i] < self.avail[i - 1] {
                return Err(format!("{what}: available_at decreases at row {i}"));
            }
        }
        Ok(())
    }
}

/// Session open / close minutes per trading day (from the exchange calendar; special and partial sessions).
#[derive(Clone, Default)]
pub struct Sessions {
    pub day: Vec<i64>,
    pub open: Vec<i32>,
    pub close: Vec<i32>,
}

impl Sessions {
    pub fn get(&self, day: i64) -> (i32, i32) {
        match self.day.binary_search(&day) {
            Ok(i) => (self.open[i], self.close[i]),
            Err(_) => (555, 930), // NSE 09:15-15:30 when the table lacks the day
        }
    }
}

/// Everything the evaluator reads.
pub struct SigData<'a> {
    pub base: Frame<'a>,
    /// IST day number and open minute of each base bar
    pub day: &'a [i64],
    pub minute: &'a [i32],
    pub bar_secs: i64,
    /// higher frames by rank (hbt_ir::ops::FRAMES index)
    pub frames: Vec<(u8, Frame<'a>)>,
    /// extra columns ($name), in Plan::extras order
    pub extras: Vec<&'a [f64]>,
    /// signal_price = raw_price x factor (1.0 in raw mode)
    pub factor: &'a [f64],
    pub sessions: Sessions,
    pub price_scale: i64,
    /// first bar that may trade (earlier bars are warm-up data)
    pub trade_start: usize,
}

impl SigData<'_> {
    pub fn check(&self, plan: &Plan) -> Result<(), String> {
        let n = self.base.len();
        self.base.check("bars")?;
        if self.day.len() != n || self.minute.len() != n || self.factor.len() != n {
            return Err("day / minute / factor must have one value per bar".into());
        }
        if self.extras.len() != plan.extras.len() || self.extras.iter().any(|x| x.len() != n) {
            return Err(format!("the program reads extra columns {:?}; give one value per bar for each", plan.extras));
        }
        for r in &plan.frames {
            let f = self.frames.iter().find(|(k, _)| k == r).ok_or_else(|| {
                format!("the program reads the {} frame; the dataset lacks it", FRAMES[*r as usize].0)
            })?;
            f.1.check(FRAMES[*r as usize].0)?;
        }
        Ok(())
    }
}

/// Base-frame bar context with the session looked up once per day.
struct BaseCursor {
    prev_day: i64,
    so: i32,
    sc: i32,
    extras: Vec<f64>,
}

impl BaseCursor {
    fn new(n_extras: usize) -> Self {
        BaseCursor { prev_day: i64::MIN, so: 555, sc: 930, extras: vec![0.0; n_extras] }
    }
    fn bar<'b>(&'b mut self, d: &SigData, t: usize) -> BarIn<'b> {
        let day = d.day[t];
        let new_session = day != self.prev_day;
        if new_session {
            (self.so, self.sc) = d.sessions.get(day);
            self.prev_day = day;
        }
        for (k, x) in d.extras.iter().enumerate() {
            self.extras[k] = x[t];
        }
        BarIn {
            o: d.base.open[t],
            h: d.base.high[t],
            l: d.base.low[t],
            c: d.base.close[t],
            v: d.base.volume[t],
            new_session,
            minute: d.minute[t],
            bar_mins: (d.bar_secs / 60) as i32,
            sess_open: self.so,
            sess_close: self.sc,
            day,
            extras: &self.extras,
        }
    }
}

/// A node ready to step: kernel + input slots (flat) + output slot range.
struct XNode {
    kernel: Kernel,
    in0: u32,
    nin: u16,
    nout: u16,
    slot: u32,
    node: u32,
}

/// A list of nodes to step in topological order, with their inputs in one flat array.
struct Program {
    nodes: Vec<XNode>,
    inputs: Vec<u32>,
}

impl Program {
    fn new(plan: &Plan, ids: impl Iterator<Item = usize>) -> Self {
        let mut nodes = vec![];
        let mut inputs = vec![];
        for i in ids {
            let n = &plan.nodes[i];
            nodes.push(XNode {
                kernel: Kernel::new(n),
                in0: inputs.len() as u32,
                nin: n.inputs.len() as u16,
                nout: n.nout as u16,
                slot: n.slot as u32,
                node: i as u32,
            });
            inputs.extend(n.inputs.iter().map(|s| *s as u32));
        }
        Program { nodes, inputs }
    }
    #[inline]
    fn step(&mut self, plan: &Plan, slots: &mut [f64], bar: &BarIn, pos: &PosIn) {
        let mut out = [f64::NAN; 3];
        let mut buf = [f64::NAN; 8];
        let mut big: Vec<f64> = vec![];
        for x in self.nodes.iter_mut() {
            let ins = &self.inputs[x.in0 as usize..x.in0 as usize + x.nin as usize];
            let args: &[f64] = if ins.len() <= 8 {
                for (k, s) in ins.iter().enumerate() {
                    buf[k] = slots[*s as usize];
                }
                &buf[..ins.len()]
            } else {
                big.clear();
                big.extend(ins.iter().map(|s| slots[*s as usize]));
                &big
            };
            x.kernel.step(args, &plan.nodes[x.node as usize].ip, bar, pos, &mut out);
            let s = x.slot as usize;
            slots[s..s + x.nout as usize].copy_from_slice(&out[..x.nout as usize]);
        }
    }
}

fn frame_bar<'b>(f: &Frame, i: usize, extras: &'b [f64]) -> BarIn<'b> {
    BarIn {
        o: f.open[i],
        h: f.high[i],
        l: f.low[i],
        c: f.close[i],
        v: f.volume[i],
        new_session: false,
        minute: 0,
        bar_mins: 0,
        sess_open: 0,
        sess_close: 0,
        day: 0,
        extras,
    }
}

pub struct MarketEval<'a> {
    plan: &'a Plan,
    data: &'a SigData<'a>,
    pub slots: Vec<f64>,
    base: Program,
    /// per higher frame: (rank, nodes, frame index in data.frames, next bar)
    htf: Vec<(u8, Program, usize, usize)>,
    cursor: BaseCursor,
    next_t: usize,
}

impl<'a> MarketEval<'a> {
    /// All market nodes of the plan.
    pub fn new(plan: &'a Plan, data: &'a SigData<'a>) -> Result<Self, String> {
        Self::with_filter(plan, data, |_| true)
    }

    /// The market nodes selected by `keep` (node index) - a sweep's shared nodes.
    pub fn with_filter(plan: &'a Plan, data: &'a SigData<'a>, keep: impl Fn(usize) -> bool) -> Result<Self, String> {
        data.check(plan)?;
        let mut slots = vec![f64::NAN; plan.n_slots];
        let mut base_ids = vec![];
        let mut htf_ids: Vec<(u8, Vec<usize>)> = vec![];
        for (i, n) in plan.market_nodes() {
            if n.frame == u8::MAX {
                slots[n.slot] = n.fp[0]; // constant
                continue;
            }
            if !keep(i) {
                continue;
            }
            if n.frame == plan.base {
                base_ids.push(i);
            } else {
                match htf_ids.iter_mut().find(|h| h.0 == n.frame) {
                    Some(h) => h.1.push(i),
                    None => htf_ids.push((n.frame, vec![i])),
                }
            }
        }
        let mut htf: Vec<(u8, Program, usize, usize)> = htf_ids
            .into_iter()
            .map(|(r, ids)| {
                let fi = data.frames.iter().position(|(k, _)| *k == r).unwrap();
                (r, Program::new(plan, ids.into_iter()), fi, 0)
            })
            .collect();
        htf.sort_by_key(|a| std::cmp::Reverse(a.0)); // coarser first (ties in available_at)
        Ok(MarketEval {
            plan,
            data,
            slots,
            base: Program::new(plan, base_ids.into_iter()),
            htf,
            cursor: BaseCursor::new(data.extras.len()),
            next_t: 0,
        })
    }

    /// Evaluate market nodes for base bar `t` (must be called for t = 0, 1, 2, ... in order).
    pub fn advance(&mut self, t: usize) -> Result<(), String> {
        if t != self.next_t {
            return Err(format!("market evaluation out of order: bar {t}, expected {}", self.next_t));
        }
        self.next_t += 1;
        let d = self.data;
        let pos = PosIn::default();
        if !self.htf.is_empty() {
            // higher frames: every bar final by the decision time of t, in availability order (coarser first on ties)
            let decision = d.base.avail[t];
            loop {
                let mut pick: Option<(usize, i64)> = None;
                for (hi, h) in self.htf.iter().enumerate() {
                    let f = &d.frames[h.2].1;
                    if h.3 < f.len() && f.avail[h.3] <= decision && pick.map(|p| f.avail[h.3] < p.1).unwrap_or(true) {
                        pick = Some((hi, f.avail[h.3]));
                    }
                }
                let Some((hi, _)) = pick else { break };
                let h = &mut self.htf[hi];
                let bar = frame_bar(&d.frames[h.2].1, h.3, &[]);
                h.3 += 1;
                h.1.step(self.plan, &mut self.slots, &bar, &pos);
            }
        }
        let bar = self.cursor.bar(d, t);
        self.base.step(self.plan, &mut self.slots, &bar, &pos);
        Ok(())
    }
}

/// Sweep layout (design §7.6): nodes read by two or more variants (and every higher-frame node) are computed once
/// and stored; a base-frame market node read by ONE variant is private to it and stepped in its own loop.
pub struct SweepLayout {
    pub shared: Vec<bool>,
    pub private: Vec<Vec<usize>>,
    /// shared slots read by private nodes, position nodes or rules (the precomputed matrix columns)
    pub cols: Vec<usize>,
    /// per variant: the (matrix column, slot) pairs it actually reads
    pub reads: Vec<Vec<(usize, usize)>>,
}

impl SweepLayout {
    pub fn new(plan: &Plan) -> Self {
        let nn = plan.nodes.len();
        let mut reach: Vec<Vec<bool>> = vec![];
        let mut count = vec![0u32; nn];
        for v in &plan.variants {
            let mut seen = vec![false; nn];
            let mut stack: Vec<usize> = vec![];
            for r in &v.rules {
                stack.push(plan.slot_node[r.when]);
                if let Some(p) = r.intent.price {
                    stack.push(plan.slot_node[p]);
                }
                for l in [r.intent.stop, r.intent.target] {
                    if let Ov::Set(Lvl::Price(s)) = l {
                        stack.push(plan.slot_node[s]);
                    }
                }
            }
            while let Some(i) = stack.pop() {
                if seen[i] {
                    continue;
                }
                seen[i] = true;
                for s in &plan.nodes[i].inputs {
                    stack.push(plan.slot_node[*s]);
                }
            }
            for i in 0..nn {
                count[i] += seen[i] as u32;
            }
            reach.push(seen);
        }
        let single = plan.variants.len() <= 1;
        let shared: Vec<bool> = (0..nn)
            .map(|i| {
                let n = &plan.nodes[i];
                !n.position && n.frame != u8::MAX && (single || count[i] >= 2 || n.frame != plan.base)
            })
            .collect();
        let private: Vec<Vec<usize>> = reach
            .iter()
            .map(|seen| {
                (0..nn)
                    .filter(|&i| {
                        let n = &plan.nodes[i];
                        seen[i] && !n.position && n.frame != u8::MAX && !shared[i]
                    })
                    .collect()
            })
            .collect();
        // columns: shared slots read by anything private / positional / a rule
        let mut per_variant: Vec<std::collections::BTreeSet<usize>> = vec![];
        for (vi, v) in plan.variants.iter().enumerate() {
            let mut cols = std::collections::BTreeSet::new();
            let mut consumers: Vec<usize> = private[vi].clone();
            consumers.extend(v.position_nodes.iter().copied());
            for c in consumers {
                for s in &plan.nodes[c].inputs {
                    if shared[plan.slot_node[*s]] {
                        cols.insert(*s);
                    }
                }
            }
            for r in &v.rules {
                let mut roots = vec![r.when];
                roots.extend(r.intent.price);
                for l in [r.intent.stop, r.intent.target] {
                    if let Ov::Set(Lvl::Price(s)) = l {
                        roots.push(s);
                    }
                }
                for s in roots {
                    if shared[plan.slot_node[s]] {
                        cols.insert(s);
                    }
                }
            }
            per_variant.push(cols);
        }
        let cols: Vec<usize> =
            per_variant.iter().flatten().copied().collect::<std::collections::BTreeSet<_>>().into_iter().collect();
        let reads =
            per_variant.iter().map(|set| set.iter().map(|s| (cols.binary_search(s).unwrap(), *s)).collect()).collect();
        SweepLayout { shared, private, cols, reads }
    }
}

/// Precompute the market slots `cols` for every base bar; with a layout only its shared nodes are evaluated.
pub fn precompute(
    plan: &Plan,
    data: &SigData,
    cols: &[usize],
    layout: Option<&SweepLayout>,
) -> Result<Vec<f64>, String> {
    let mut m = match layout {
        Some(l) => MarketEval::with_filter(plan, data, |i| l.shared[i])?,
        None => MarketEval::new(plan, data)?,
    };
    let n = data.base.len();
    let mut out = Vec::with_capacity(n * cols.len());
    for t in 0..n {
        m.advance(t)?;
        out.extend(cols.iter().map(|&c| m.slots[c]));
    }
    Ok(out)
}

enum Market<'a> {
    Live(Box<MarketEval<'a>>),
    Pre { width: usize, reads: &'a [(usize, usize)], data: &'a [f64], private: Program, cursor: BaseCursor },
}

#[derive(Default, Clone, Copy)]
pub struct IrCounters {
    pub warmup_suppressed: u64,
    pub entry_conflicts: u64,
    pub nan_levels: u64,
}

pub struct IrSource<'a> {
    plan: &'a Plan,
    v: &'a VariantPlan,
    data: &'a SigData<'a>,
    market: Market<'a>,
    pos: Program,
    own: Vec<f64>,
    warm: usize,
    pub counters: IrCounters,
    /// optional per-bar copy of every slot (look-ahead harness, traces)
    pub trace: Option<Vec<f64>>,
}

impl<'a> IrSource<'a> {
    fn build(plan: &'a Plan, variant: usize, data: &'a SigData<'a>, market: Market<'a>) -> Result<Self, String> {
        let v = plan.variants.get(variant).ok_or("no such variant")?;
        let pos = Program::new(plan, v.position_nodes.iter().copied());
        let mut own = vec![f64::NAN; plan.n_slots];
        for n in plan.nodes.iter() {
            if n.frame == u8::MAX {
                own[n.slot] = n.fp[0];
            }
        }
        let warm = v.lookback.max(data.trade_start);
        Ok(IrSource { plan, v, data, market, pos, own, warm, counters: IrCounters::default(), trace: None })
    }
    /// market nodes evaluated live inside the bar loop (single runs, paper / streaming)
    pub fn live(plan: &'a Plan, variant: usize, data: &'a SigData<'a>) -> Result<Self, String> {
        let m = MarketEval::new(plan, data)?;
        Self::build(plan, variant, data, Market::Live(Box::new(m)))
    }
    /// shared market nodes read from a matrix precomputed with `precompute(plan, data, &layout.cols, Some(&layout))`;
    /// the variant's private nodes are stepped here
    pub fn pre(
        plan: &'a Plan,
        variant: usize,
        data: &'a SigData<'a>,
        layout: &'a SweepLayout,
        matrix: &'a [f64],
    ) -> Result<Self, String> {
        data.check(plan)?;
        let private = Program::new(plan, layout.private[variant].iter().copied());
        let market = Market::Pre {
            width: layout.cols.len(),
            reads: &layout.reads[variant],
            data: matrix,
            private,
            cursor: BaseCursor::new(data.extras.len()),
        };
        Self::build(plan, variant, data, market)
    }
    pub fn with_trace(mut self) -> Self {
        self.trace = Some(Vec::with_capacity(self.data.base.len() * self.plan.n_slots));
        self
    }

    fn to_raw(&self, v: f64, t: usize) -> f64 {
        let f = self.data.factor[(t + 1).min(self.data.factor.len() - 1)];
        v / f * self.data.price_scale as f64
    }

    fn level(&self, l: Ov<Lvl>, slots: &[f64], t: usize) -> Result<Ov<Level>, ()> {
        Ok(match l {
            Ov::Default => Ov::Default,
            Ov::Off => Ov::Off,
            Ov::Set(Lvl::Pct(p)) => Ov::Set(Level::Pct(p)),
            Ov::Set(Lvl::Price(s)) => {
                let v = slots[s];
                if !v.is_finite() || v <= 0.0 {
                    return Err(());
                }
                Ov::Set(Level::Price(self.to_raw(v, t)))
            }
        })
    }
}

impl DecisionSource for IrSource<'_> {
    fn decide(&mut self, ctx: &BarCtx, pos: &PosState, out: &mut Vec<Intent>) -> Result<(), String> {
        let t = ctx.t;
        // market values of bar t (live, or shared matrix + private nodes), then position nodes
        let plan = self.plan;
        let slots: &mut Vec<f64> = match &mut self.market {
            Market::Live(m) => {
                m.advance(t)?;
                &mut m.slots
            }
            Market::Pre { width, reads, data, private, cursor } => {
                let row = &data[t * *width..(t + 1) * *width];
                for &(j, c) in reads.iter() {
                    self.own[c] = row[j];
                }
                if !private.nodes.is_empty() {
                    let bar = cursor.bar(self.data, t);
                    private.step(plan, &mut self.own, &bar, &PosIn::default());
                }
                &mut self.own
            }
        };
        if !self.pos.nodes.is_empty() {
            // position state in signal scale
            let f = self.data.factor[t];
            let px = |raw: f64| raw * f / self.data.price_scale as f64;
            let flat = pos.qty == 0;
            let p = PosIn {
                qty: pos.qty as f64,
                entry: if flat { f64::NAN } else { px(pos.avg_entry) },
                bars_in_trade: pos.bars_in_trade as f64,
                unrealised_pct: if flat || pos.avg_entry <= 0.0 {
                    0.0
                } else {
                    (pos.close as f64 / pos.avg_entry - 1.0) * pos.qty.signum() as f64
                },
                highest: if flat { f64::NAN } else { px(pos.highest as f64) },
                lowest: if flat { f64::NAN } else { px(pos.lowest as f64) },
            };
            let bar = frame_bar(&self.data.base, t, &[]);
            self.pos.step(plan, slots, &bar, &p);
        }
        if let Some(tr) = self.trace.as_mut() {
            tr.extend_from_slice(slots);
        }
        if t < self.warm {
            self.counters.warmup_suppressed += 1;
            return Ok(());
        }
        let slots: &[f64] = match &self.market {
            Market::Live(m) => &m.slots,
            Market::Pre { .. } => &self.own,
        };
        let truthy = |s: usize| slots[s] != 0.0 && !slots[s].is_nan();
        // exits first (all that fire), then the FIRST matching entry (design §6.6)
        for r in &self.v.rules {
            if !r.intent.kind.is_entry() && truthy(r.when) {
                out.push(Intent::exit(r.intent.kind));
            }
        }
        let mut entered = false;
        for r in &self.v.rules {
            if !r.intent.kind.is_entry() || !truthy(r.when) {
                continue;
            }
            if entered {
                self.counters.entry_conflicts += 1;
                continue;
            }
            let it = &r.intent;
            let price = match it.price {
                Some(s) => {
                    let v = slots[s];
                    if !v.is_finite() || v <= 0.0 {
                        self.counters.nan_levels += 1;
                        continue;
                    }
                    Some(self.to_raw(v, t))
                }
                None => None,
            };
            let limit = match it.limit {
                Some(s) => {
                    let v = slots[s];
                    if !v.is_finite() || v <= 0.0 {
                        self.counters.nan_levels += 1;
                        continue;
                    }
                    Some(self.to_raw(v, t))
                }
                None => None,
            };
            let (Ok(stop), Ok(target)) = (self.level(it.stop, slots, t), self.level(it.target, slots, t)) else {
                self.counters.nan_levels += 1;
                continue;
            };
            let size = it.size.map(|(m, v)| match m {
                SizeMode::AllIn => Sizing::AllIn,
                SizeMode::PctEquity => Sizing::PctEquity(v),
                SizeMode::FixedQty => Sizing::FixedQty(v as i64),
                SizeMode::FixedValue => Sizing::FixedValue(v),
                SizeMode::RiskPct => Sizing::RiskPct(v),
            });
            out.push(Intent {
                kind: it.kind,
                order: it.order,
                price,
                limit,
                offset_pct: match it.order {
                    OrderType::Market => None,
                    _ => it.offset_pct,
                },
                size,
                stop,
                target,
                trail_pct: it.trail_pct,
                time_stop_bars: it.time_stop_bars,
                expiry_bars: it.expiry_bars,
            });
            entered = true;
        }
        Ok(())
    }
    fn counters(&self) -> Vec<(&'static str, u64)> {
        vec![
            ("warmup_suppressed", self.counters.warmup_suppressed),
            ("entry_conflicts", self.counters.entry_conflicts),
            ("nan_levels", self.counters.nan_levels),
        ]
    }
}
