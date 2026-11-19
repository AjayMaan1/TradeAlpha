//! Compiler (design §7): resolve -> types -> causality -> bind params -> fold -> CSE -> lookback -> partition -> plan.
//!
//! `compile()` checks a document once (with the parameter defaults). `build_plan()` binds one or many parameter
//! variants into ONE node arena: structurally identical nodes are shared (hash-consing), so a sweep computes every
//! market-only node once (design §7.6). Nodes that depend on the position are private to their variant.

use std::collections::{BTreeMap, HashMap};

use serde_json::Value;

use crate::ast::{
    Document, Expr, ExprKind, IntentAst, IntentKind, LevelAst, MAX_LOOKBACK, OrderType, ParamType, SizeMode, parse,
    parse_value,
};
use crate::canon::{canonical, sha256_hex};
use crate::error::{IrError, IrResult};
use crate::ops::{self, A, Class, Column, Op, Ret, base_bars_per, frame_code, frame_rank, lookup, own_lookback};

pub const MAX_VARIANTS: usize = 100_000;

/// A checked document (types, references, causality verified with the parameter defaults).
#[derive(Debug, Clone)]
pub struct Program {
    pub doc: Document,
    pub canonical: String,
    pub ir_sha256: String,
}

pub fn compile(text: &str) -> IrResult<Program> {
    finish(parse(text)?)
}

pub fn compile_value(v: Value) -> IrResult<Program> {
    finish(parse_value(v)?)
}

fn finish(doc: Document) -> IrResult<Program> {
    let canonical = canonical(&doc.raw);
    let ir_sha256 = sha256_hex(canonical.as_bytes());
    let prog = Program { doc, canonical, ir_sha256 };
    // full static check: lower every signal and rule with the defaults on the declared (or finest) interval
    let base = prog.doc.interval.clone().unwrap_or_else(|| "1m".into());
    let plan = build_plan(&prog, &[BTreeMap::new()], &base, &[])?;
    if plan.variants.is_empty() {
        let why = plan.rejected.first().map(|r| r.1.clone()).unwrap_or_default();
        return Err(IrError::new("E-PARAM-RANGE", "/constraints", format!("the parameter defaults violate: {why}")));
    }
    Ok(prog)
}

/// Per-variant override of an OrderPlan field.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Ov<T> {
    Default,
    Off,
    Set(T),
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Lvl {
    Pct(f64),
    /// slot of a price expression (signal scale)
    Price(usize),
}

#[derive(Debug, Clone, PartialEq)]
pub struct IntentSpec {
    pub kind: IntentKind,
    pub order: OrderType,
    pub price: Option<usize>,
    /// stop-limit: slot of the limit leg (None = marketable: limit at the trigger)
    pub limit: Option<usize>,
    pub offset_pct: Option<f64>,
    pub size: Option<(SizeMode, f64)>,
    pub stop: Ov<Lvl>,
    pub target: Ov<Lvl>,
    pub trail_pct: Ov<f64>,
    pub time_stop_bars: Ov<u32>,
    pub expiry_bars: Option<u32>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RuleSpec {
    pub id: String,
    pub when: usize,
    pub intent: IntentSpec,
}

#[derive(Debug, Clone, PartialEq)]
pub struct NodeSpec {
    pub op: Op,
    pub inputs: Vec<usize>,
    pub ip: Vec<i64>,
    pub fp: Vec<f64>,
    /// frame rank (ops::FRAMES); constants carry u8::MAX (frame-agnostic)
    pub frame: u8,
    pub position: bool,
    pub variant: Option<u32>,
    pub slot: usize,
    pub nout: usize,
    pub is_bool: bool,
    /// cumulative lookback of this node in bars of the RUN interval (conservative)
    pub lookback: usize,
    pub label: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct VariantPlan {
    /// index of the variant in the request
    pub index: usize,
    pub params: BTreeMap<String, f64>,
    pub rules: Vec<RuleSpec>,
    /// warm-up in run-interval bars: the longest lookback among the nodes the rules read
    pub lookback: usize,
    /// position-dependent nodes of this variant (indices into Plan::nodes, topological order)
    pub position_nodes: Vec<usize>,
    /// market slots this variant reads (rules, price expressions, inputs of its position nodes)
    pub market_reads: Vec<usize>,
    /// slot of every named signal (constants materialised)
    pub signals: Vec<(String, usize)>,
}

#[derive(Debug, Clone)]
pub struct Plan {
    pub base: u8,
    pub nodes: Vec<NodeSpec>,
    pub n_slots: usize,
    /// node index owning each slot
    pub slot_node: Vec<usize>,
    pub variants: Vec<VariantPlan>,
    /// (request index, reason) of variants removed by the constraints
    pub rejected: Vec<(usize, String)>,
    /// names of the extra dataset columns the program reads ($name), by Column::Extra index
    pub extras: Vec<String>,
    /// frames other than the run interval that the program reads
    pub frames: Vec<u8>,
    pub plan_sha256: String,
}

impl Plan {
    /// market nodes in topological order
    pub fn market_nodes(&self) -> impl Iterator<Item = (usize, &NodeSpec)> {
        self.nodes.iter().enumerate().filter(|(_, n)| !n.position)
    }
}

#[derive(Clone, Copy, Debug)]
enum Val {
    Num(f64),
    Bool(bool),
    Node { slot: usize, is_bool: bool, frame: u8, position: bool },
}

#[derive(PartialEq, Eq, Hash)]
struct Key {
    op: Op,
    inputs: Vec<usize>,
    ip: Vec<i64>,
    fp: Vec<u64>,
    frame: u8,
    variant: Option<u32>,
}

struct Builder<'a> {
    doc: &'a Document,
    base: u8,
    bars_per_session: usize,
    nodes: Vec<NodeSpec>,
    index: HashMap<Key, usize>,
    slot_node: Vec<usize>,
    extras: Vec<String>,
    extra_allowed: &'a [String],
}

struct Ctx<'a> {
    params: &'a BTreeMap<String, f64>,
    variant: u32,
    memo: HashMap<String, Val>,
    visiting: Vec<String>,
}

fn e(code: &'static str, ptr: &str, msg: impl Into<String>) -> IrError {
    IrError::new(code, ptr, msg)
}

fn col_of(name: &str) -> Option<Column> {
    Some(match name {
        "open" => Column::Open,
        "high" => Column::High,
        "low" => Column::Low,
        "close" => Column::Close,
        "volume" => Column::Volume,
        "typical" => Column::Typical,
        "hl2" => Column::Hl2,
        _ => return None,
    })
}

fn fmt_num(x: f64) -> String {
    crate::canon::number(x)
}

fn infix(op: Op) -> Option<&'static str> {
    Some(match op {
        Op::Add => "+",
        Op::Sub => "-",
        Op::Mul => "*",
        Op::Div => "/",
        Op::Gt => ">",
        Op::Ge => ">=",
        Op::Lt => "<",
        Op::Le => "<=",
        Op::Eq => "==",
        Op::Ne => "!=",
        Op::And => "and",
        Op::Or => "or",
        _ => return None,
    })
}

fn fold(op: Op, a: &[f64]) -> f64 {
    let b = |x: bool| if x { 1.0 } else { 0.0 };
    match op {
        Op::Add => a[0] + a[1],
        Op::Sub => a[0] - a[1],
        Op::Mul => a[0] * a[1],
        Op::Div => a[0] / a[1],
        Op::Min => a[0].min(a[1]),
        Op::Max => a[0].max(a[1]),
        Op::Pow => a[0].powf(a[1]),
        Op::Neg => -a[0],
        Op::Abs => a[0].abs(),
        Op::Log => a[0].ln(),
        Op::Exp => a[0].exp(),
        Op::Sqrt => a[0].sqrt(),
        Op::Clip => a[0].max(a[1]).min(a[2]),
        Op::Where => {
            if a[0] != 0.0 {
                a[1]
            } else {
                a[2]
            }
        }
        Op::Gt => b(a[0] > a[1]),
        Op::Ge => b(a[0] >= a[1]),
        Op::Lt => b(a[0] < a[1]),
        Op::Le => b(a[0] <= a[1]),
        Op::Eq => b(a[0] == a[1]),
        Op::Ne => b(!a[0].is_nan() && !a[1].is_nan() && a[0] != a[1]),
        Op::Between => b(a[0] >= a[1] && a[0] <= a[2]),
        Op::And => b(a.iter().all(|x| *x != 0.0)),
        Op::Or => b(a.iter().any(|x| *x != 0.0)),
        Op::Not => b(a[0] == 0.0),
        _ => f64::NAN,
    }
}

impl<'a> Builder<'a> {
    fn push(&mut self, mut n: NodeSpec, variant: Option<u32>) -> usize {
        let key = Key {
            op: n.op,
            inputs: n.inputs.clone(),
            ip: n.ip.clone(),
            fp: n.fp.iter().map(|x| x.to_bits()).collect(),
            frame: n.frame,
            variant: if n.position { variant } else { None },
        };
        if let Some(&i) = self.index.get(&key) {
            return self.nodes[i].slot;
        }
        n.slot = self.slot_node.len();
        n.variant = if n.position { variant } else { None };
        let idx = self.nodes.len();
        for _ in 0..n.nout {
            self.slot_node.push(idx);
        }
        let slot = n.slot;
        self.index.insert(key, idx);
        self.nodes.push(n);
        slot
    }

    fn node_of(&self, slot: usize) -> &NodeSpec {
        &self.nodes[self.slot_node[slot]]
    }

    /// Label of a slot for use INSIDE another label; long ones are referenced by slot ("#12") so labels stay short
    /// on deep expressions (compile time stays linear, T1.6.1).
    fn label_of(&self, slot: usize) -> String {
        let n = self.node_of(slot);
        let def = ops::def_of(n.op);
        let l = match def {
            Some(d) if !d.outs.is_empty() => format!("{}.{}", n.label, d.outs[slot - n.slot]),
            _ => n.label.clone(),
        };
        if l.len() > 48 { format!("#{slot}") } else { l }
    }

    fn constant(&mut self, x: f64, is_bool: bool) -> usize {
        self.push(
            NodeSpec {
                op: Op::Const,
                inputs: vec![],
                ip: vec![],
                fp: vec![x],
                frame: u8::MAX,
                position: false,
                variant: None,
                slot: 0,
                nout: 1,
                is_bool,
                lookback: 0,
                label: if is_bool { (x != 0.0).to_string() } else { fmt_num(x) },
            },
            None,
        )
    }

    fn materialize(&mut self, v: Val) -> (usize, u8, bool) {
        match v {
            Val::Num(x) => (self.constant(x, false), u8::MAX, false),
            Val::Bool(b) => (self.constant(if b { 1.0 } else { 0.0 }, true), u8::MAX, false),
            Val::Node { slot, frame, position, .. } => (slot, frame, position),
        }
    }

    fn tf_rank(&self, tf: &Option<String>, ptr: &str) -> IrResult<u8> {
        match tf {
            None => Ok(self.base),
            Some(code) => {
                let r = frame_rank(code).ok_or_else(|| e("E-TF-UNKNOWN", ptr, format!("unknown interval '{code}'")))?;
                if r < self.base {
                    return Err(e(
                        "E-TF-LOWER",
                        ptr,
                        format!("tf {code} is below the run interval {}", frame_code(self.base)),
                    )
                    .hint(
                        "a strategy can read its own interval or higher ones; use the 1m magnifier for finer fills",
                    ));
                }
                Ok(r)
            }
        }
    }

    fn lower(&mut self, x: &Expr, cx: &mut Ctx) -> IrResult<Val> {
        match &x.kind {
            ExprKind::Num(v) => Ok(Val::Num(*v)),
            ExprKind::Bool(b) => Ok(Val::Bool(*b)),
            ExprKind::Time(m) => Ok(Val::Num(*m as f64)),
            ExprKind::Param(name) => {
                let spec = self.doc.params.get(name).ok_or_else(|| {
                    e("E-SCHEMA", &x.ptr, format!("unknown parameter '{name}'")).hint("declare it under \"params\"")
                })?;
                let v = cx.params.get(name).copied().unwrap_or(spec.default);
                Ok(if spec.ty == ParamType::Bool { Val::Bool(v != 0.0) } else { Val::Num(v) })
            }
            ExprKind::Sig(name) => {
                if let Some(v) = cx.memo.get(name) {
                    return Ok(*v);
                }
                if cx.visiting.contains(name) {
                    return Err(e(
                        "E-CYCLE",
                        &x.ptr,
                        format!("signal '{name}' depends on itself: {}", cx.visiting.join(" -> ")),
                    ));
                }
                let (_, body) = self
                    .doc
                    .signals
                    .iter()
                    .find(|(k, _)| k == name)
                    .ok_or_else(|| e("E-SCHEMA", &x.ptr, format!("unknown signal '{name}'")))?;
                cx.visiting.push(name.clone());
                let v = self.lower(body, cx)?;
                cx.visiting.pop();
                cx.memo.insert(name.clone(), v);
                Ok(v)
            }
            ExprKind::Col { col, tf } => {
                let (c, label) = if let Some(extra) = col.strip_prefix('$') {
                    if tf.is_some() {
                        return Err(e("E-SCHEMA", &x.ptr, "extra columns ($name) exist on the run interval only"));
                    }
                    if !self.extra_allowed.is_empty() && !self.extra_allowed.iter().any(|n| n == extra) {
                        return Err(e("E-SCHEMA", &x.ptr, format!("the dataset has no column '${extra}'")));
                    }
                    let i = match self.extras.iter().position(|n| n == extra) {
                        Some(i) => i,
                        None => {
                            self.extras.push(extra.to_string());
                            self.extras.len() - 1
                        }
                    };
                    (Column::Extra(i as u16), col.clone())
                } else {
                    let c = col_of(col).ok_or_else(|| {
                        e("E-SCHEMA", &x.ptr, format!("unknown column '{col}'"))
                            .hint("open high low close volume typical hl2, or $name for a dataset column")
                    })?;
                    (c, col.clone())
                };
                let frame = self.tf_rank(tf, &x.ptr)?;
                let label = if frame == self.base { label } else { format!("{label}@{}", frame_code(frame)) };
                let slot = self.push(
                    NodeSpec {
                        op: Op::Col(c),
                        inputs: vec![],
                        ip: vec![],
                        fp: vec![],
                        frame,
                        position: false,
                        variant: None,
                        slot: 0,
                        nout: 1,
                        is_bool: false,
                        lookback: 0,
                        label,
                    },
                    None,
                );
                Ok(Val::Node { slot, is_bool: false, frame, position: false })
            }
            ExprKind::Call { name, args, out, tf } => self.call(x, name, args, out, tf, cx),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn call(
        &mut self,
        x: &Expr,
        name: &str,
        args: &[Expr],
        out: &Option<String>,
        tf: &Option<String>,
        cx: &mut Ctx,
    ) -> IrResult<Val> {
        let ptr = &x.ptr;
        let def = lookup(name).ok_or_else(|| {
            let hint = if ["lead", "future", "shift_forward", "centered_mean"].contains(&name) {
                "operators that read later bars do not exist (causality)"
            } else {
                "see the operator catalogue (design §10 / Appendix C)"
            };
            e("E-UNKNOWN-FN", ptr, format!("unknown function '{name}'")).hint(hint)
        })?;
        let (lo, hi) = (def.args.len(), def.args.len() + def.opt.len());
        if (def.variadic && args.len() < lo) || (!def.variadic && (args.len() < lo || args.len() > hi)) {
            let want = if def.variadic {
                format!("at least {lo}")
            } else if lo == hi {
                format!("{lo}")
            } else {
                format!("{lo} to {hi}")
            };
            return Err(e("E-ARITY", ptr, format!("{name} takes {want} argument(s), got {}", args.len())));
        }
        if tf.is_some() && def.class != Class::Ohlc {
            return Err(e("E-SCHEMA", &format!("{ptr}/tf"), format!("{name} takes no tf"))
                .hint("put tf on the columns: {\"col\": \"close\", \"tf\": \"D\"}"));
        }
        let out_idx = match (def.outs.is_empty(), out) {
            (true, None) => 0,
            (true, Some(o)) => {
                return Err(e("E-SCHEMA", &format!("{ptr}/out"), format!("{name} has one output, not '{o}'")));
            }
            (false, None) => {
                return Err(
                    e("E-SCHEMA", ptr, format!("{name} has several outputs: add \"out\"")).hint(def.outs.join(", "))
                );
            }
            (false, Some(o)) => def.outs.iter().position(|k| k == o).ok_or_else(|| {
                e("E-SCHEMA", &format!("{ptr}/out"), format!("{name} has no output '{o}'")).hint(def.outs.join(", "))
            })?,
        };
        // lower and type-check the arguments
        let mut series: Vec<Val> = vec![];
        let mut ip: Vec<i64> = vec![];
        let mut fp: Vec<f64> = vec![];
        let mut labels: Vec<String> = vec![];
        let mut consts: Vec<f64> = vec![];
        let mut all_const = true;
        for i in 0..args.len().max(hi) {
            let kind = if i < def.args.len() {
                def.args[i]
            } else if def.variadic {
                def.args[0]
            } else if i < hi {
                def.opt[i - def.args.len()].0
            } else {
                break;
            };
            let v = if i < args.len() { self.lower(&args[i], cx)? } else { Val::Num(def.opt[i - def.args.len()].1) };
            let aptr = if i < args.len() { args[i].ptr.clone() } else { ptr.clone() };
            match kind {
                A::S | A::B => {
                    let want_bool = kind == A::B;
                    let is_bool = match v {
                        Val::Num(_) => false,
                        Val::Bool(_) => true,
                        Val::Node { is_bool, .. } => is_bool,
                    };
                    if want_bool != is_bool {
                        return Err(e(
                            "E-TYPE",
                            &aptr,
                            format!(
                                "{name} argument {} must be a {} series, got a {}",
                                i + 1,
                                if want_bool { "boolean" } else { "numeric" },
                                if is_bool { "boolean" } else { "number" }
                            ),
                        )
                        .hint(if want_bool {
                            "compare it: {\"fn\": \"gt\", ...}"
                        } else {
                            "use where(cond, a, b)"
                        }));
                    }
                    match v {
                        Val::Num(c) => consts.push(c),
                        Val::Bool(b) => consts.push(if b { 1.0 } else { 0.0 }),
                        Val::Node { .. } => all_const = false,
                    }
                    labels.push(match v {
                        Val::Num(c) => fmt_num(c),
                        Val::Bool(b) => b.to_string(),
                        Val::Node { slot, .. } => self.label_of(slot),
                    });
                    series.push(v);
                }
                A::N | A::I | A::Q => {
                    let c = match v {
                        Val::Num(c) => c,
                        _ => {
                            return Err(e(
                                "E-TYPE",
                                &aptr,
                                format!("{name} argument {} must be a constant (a literal or a param)", i + 1),
                            ));
                        }
                    };
                    match kind {
                        A::I => {
                            if c.fract() != 0.0 {
                                return Err(e(
                                    "E-TYPE",
                                    &aptr,
                                    format!("{name} argument {} must be an integer, got {c}", i + 1),
                                ));
                            }
                            if c < 1.0 {
                                let code = if def.op == Op::Shift { "E-CAUSAL" } else { "E-PARAM-RANGE" };
                                return Err(e(code, &aptr, format!("{name} argument {} must be >= 1, got {c}", i + 1))
                                    .hint(if def.op == Op::Shift {
                                        "a shift < 1 reads the current or a FUTURE bar"
                                    } else {
                                        ""
                                    }));
                            }
                            if c as usize > MAX_LOOKBACK {
                                return Err(e(
                                    "E-LIMIT",
                                    &aptr,
                                    format!("window {c} above the maximum lookback {MAX_LOOKBACK}"),
                                ));
                            }
                            ip.push(c as i64);
                        }
                        A::Q if !(0.0..=1.0).contains(&c) => {
                            return Err(e(
                                "E-PARAM-RANGE",
                                &aptr,
                                format!("{name} argument {} must be in [0, 1]", i + 1),
                            ));
                        }
                        _ => fp.push(c),
                    }
                    labels.push(fmt_num(c));
                }
            }
        }
        // constant folding of pure operators
        if def.class == Class::Pure && all_const {
            let r = fold(def.op, &consts);
            return Ok(if def.ret == Ret::Bool { Val::Bool(r != 0.0) } else { Val::Num(r) });
        }
        let mut inputs = vec![];
        let mut frame = u8::MAX;
        let mut position = def.class == Class::Position;
        let mut lb_in = 0usize;
        for v in &series {
            let (slot, f, pos) = self.materialize(*v);
            inputs.push(slot);
            frame = frame.min(f);
            position |= pos;
            lb_in = lb_in.max(self.node_of(slot).lookback);
        }
        frame = match def.class {
            Class::Ohlc => self.tf_rank(tf, ptr)?,
            Class::Session | Class::Position => self.base,
            _ if frame == u8::MAX => self.base,
            _ => frame,
        };
        let own = own_lookback(def.op, &ip, self.bars_per_session);
        let lookback = lb_in + own * base_bars_per(frame, self.base);
        if lookback > MAX_LOOKBACK {
            return Err(e("E-LIMIT", ptr, format!("lookback {lookback} bars above the maximum {MAX_LOOKBACK}")));
        }
        let label = match (infix(def.op), labels.len()) {
            (Some(sym), n) if n >= 2 => format!("({})", labels.join(&format!(" {sym} "))),
            _ => {
                let base = format!("{name}({})", labels.join(", "));
                match (def.class, tf) {
                    (Class::Ohlc, Some(t)) if frame != self.base => format!("{base}@{t}"),
                    _ => base,
                }
            }
        };
        let nout = def.outs.len().max(1);
        let slot = self.push(
            NodeSpec {
                op: def.op,
                inputs,
                ip,
                fp,
                frame,
                position,
                variant: None,
                slot: 0,
                nout,
                is_bool: def.ret == Ret::Bool,
                lookback,
                label,
            },
            Some(cx.variant),
        );
        Ok(Val::Node { slot: slot + out_idx, is_bool: def.ret == Ret::Bool, frame, position })
    }

    fn constant_num(&mut self, x: &Expr, cx: &mut Ctx, what: &str) -> IrResult<f64> {
        match self.lower(x, cx)? {
            Val::Num(v) => Ok(v),
            _ => Err(e("E-TYPE", &x.ptr, format!("{what} must be a constant number (a literal or a param)"))),
        }
    }

    fn series_num(&mut self, x: &Expr, cx: &mut Ctx, what: &str) -> IrResult<usize> {
        let v = self.lower(x, cx)?;
        if matches!(v, Val::Bool(_) | Val::Node { is_bool: true, .. }) {
            return Err(e("E-TYPE", &x.ptr, format!("{what} must be a numeric expression")));
        }
        Ok(self.materialize(v).0)
    }

    fn pct(&mut self, x: &Expr, cx: &mut Ctx, what: &str) -> IrResult<f64> {
        let v = self.constant_num(x, cx, what)?;
        if !(v > 0.0 && v < 1.0) {
            return Err(e("E-INTENT", &x.ptr, format!("{what} must be in (0, 1), got {v}")));
        }
        Ok(v)
    }

    fn count(&mut self, x: &Expr, cx: &mut Ctx, what: &str) -> IrResult<u32> {
        let v = self.constant_num(x, cx, what)?;
        if v.fract() != 0.0 || !(1.0..=100_000.0).contains(&v) {
            return Err(e("E-INTENT", &x.ptr, format!("{what} must be an integer >= 1, got {v}")));
        }
        Ok(v as u32)
    }

    fn level(&mut self, l: &Option<LevelAst>, cx: &mut Ctx, what: &str) -> IrResult<Ov<Lvl>> {
        Ok(match l {
            None => Ov::Default,
            Some(LevelAst::Off) => Ov::Off,
            Some(LevelAst::Pct(x)) => Ov::Set(Lvl::Pct(self.pct(x, cx, what)?)),
            Some(LevelAst::Price(x)) => Ov::Set(Lvl::Price(self.series_num(x, cx, what)?)),
        })
    }

    fn intent(&mut self, it: &IntentAst, cx: &mut Ctx) -> IrResult<IntentSpec> {
        let price = it.price.as_ref().map(|x| self.series_num(x, cx, "order price")).transpose()?;
        let limit = it.limit.as_ref().map(|x| self.series_num(x, cx, "order limit")).transpose()?;
        let offset_pct = match &it.offset_pct {
            Some(x) => {
                let v = self.constant_num(x, cx, "offset_pct")?;
                if !(0.0..0.5).contains(&v) {
                    return Err(e("E-INTENT", &x.ptr, format!("offset_pct must be in [0, 0.5), got {v}")));
                }
                Some(v)
            }
            None => None,
        };
        let size = match &it.size {
            None => None,
            Some((SizeMode::AllIn, _)) => Some((SizeMode::AllIn, 0.0)),
            Some((mode, Some(x))) => {
                let v = self.constant_num(x, cx, "size")?;
                let ok = match mode {
                    SizeMode::PctEquity => v > 0.0 && v <= 20.0,
                    SizeMode::RiskPct => v > 0.0 && v < 1.0,
                    SizeMode::FixedQty => v >= 1.0 && v.fract() == 0.0,
                    _ => v > 0.0,
                };
                if !ok {
                    return Err(e("E-INTENT", &x.ptr, format!("size value {v} is out of range")));
                }
                Some((*mode, v))
            }
            Some((_, None)) => return Err(e("E-INTENT", &it.ptr, "size needs a value")),
        };
        let stop = self.level(&it.stop, cx, "stop")?;
        let target = self.level(&it.target, cx, "target")?;
        if matches!(size, Some((SizeMode::RiskPct, _))) && !matches!(stop, Ov::Set(_)) {
            return Err(e("E-RISK-NO-STOP", &it.ptr, "risk_pct sizing needs a stop in the bracket")
                .hint("\"bracket\": {\"stop\": {\"pct\": 0.01}} or {\"price\": expression}"));
        }
        let trail_pct = match &it.trail_pct {
            None => Ov::Default,
            Some(None) => Ov::Off,
            Some(Some(x)) => Ov::Set(self.pct(x, cx, "trail_pct")?),
        };
        let time_stop_bars = match &it.time_stop_bars {
            None => Ov::Default,
            Some(None) => Ov::Off,
            Some(Some(x)) => Ov::Set(self.count(x, cx, "time_stop_bars")?),
        };
        let expiry_bars = it.expiry_bars.as_ref().map(|x| self.count(x, cx, "expiry_bars")).transpose()?;
        Ok(IntentSpec {
            kind: it.kind,
            order: it.order,
            price,
            limit,
            offset_pct,
            size,
            stop,
            target,
            trail_pct,
            time_stop_bars,
            expiry_bars,
        })
    }
}

/// Bind `variants` (parameter values; missing = defaults) on the run interval `base_tf` into one plan.
/// `extra_columns`: names of the dataset's extra columns (empty = do not check).
pub fn build_plan(
    prog: &Program,
    variants: &[BTreeMap<String, f64>],
    base_tf: &str,
    extra_columns: &[String],
) -> IrResult<Plan> {
    let doc = &prog.doc;
    let base = frame_rank(base_tf)
        .ok_or_else(|| e("E-TF-UNKNOWN", "/universe/interval", format!("unknown interval '{base_tf}'")))?;
    if variants.len() > MAX_VARIANTS {
        return Err(e("E-GRID-SIZE", "/params", format!("{} variants (maximum {MAX_VARIANTS})", variants.len())));
    }
    if let Some(p) = &doc.product {
        let daily = ops::FRAMES[base as usize].1 >= 86_400;
        if p == "MIS" && daily {
            return Err(e("E-PRODUCT", "/product", "MIS (intraday) needs an intraday run interval")
                .hint("use CNC (delivery) or NRML on daily bars"));
        }
        if p == "CNC" && doc.rules.iter().any(|r| r.intent.kind == IntentKind::EnterShort) {
            return Err(e("E-PRODUCT", "/product", "CNC (delivery) cannot sell short"));
        }
    }
    let secs = ops::FRAMES[base as usize].1;
    let mut b = Builder {
        doc,
        base,
        bars_per_session: if secs >= 86_400 { 1 } else { ((22_500 + secs - 1) / secs) as usize },
        nodes: vec![],
        index: HashMap::new(),
        slot_node: vec![],
        extras: vec![],
        extra_allowed: extra_columns,
    };
    let mut out_variants = vec![];
    let mut rejected = vec![];
    for (vi, vals) in variants.iter().enumerate() {
        for (k, v) in vals {
            let spec =
                doc.params.get(k).ok_or_else(|| e("E-PARAM-RANGE", "/params", format!("unknown parameter '{k}'")))?;
            spec.check(*v, &spec.ptr)?;
        }
        let mut params = BTreeMap::new();
        for (k, s) in &doc.params {
            params.insert(k.clone(), vals.get(k).copied().unwrap_or(s.default));
        }
        let mut cx = Ctx { params: &params, variant: vi as u32, memo: HashMap::new(), visiting: vec![] };
        let mut ok = true;
        for c in &doc.constraints {
            match b.lower(c, &mut cx)? {
                Val::Bool(true) => {}
                Val::Bool(false) => {
                    rejected.push((vi, format!("constraint at {} is false", c.ptr)));
                    ok = false;
                    break;
                }
                _ => return Err(e("E-TYPE", &c.ptr, "a constraint must be a boolean expression of params only")),
            }
        }
        if !ok {
            continue;
        }
        // every signal is checked even when no rule uses it
        let mut signals = vec![];
        for (name, _) in &doc.signals {
            let s = Expr { kind: ExprKind::Sig(name.clone()), ptr: format!("/signals/{name}") };
            let v = b.lower(&s, &mut cx)?;
            signals.push((name.clone(), b.materialize(v).0));
        }
        let mut rules = vec![];
        for r in &doc.rules {
            let when = match b.lower(&r.when, &mut cx)? {
                Val::Bool(v) => b.constant(if v { 1.0 } else { 0.0 }, true),
                Val::Node { slot, is_bool: true, .. } => slot,
                _ => return Err(e("E-TYPE", &r.when.ptr, "when must be a boolean expression")),
            };
            let intent = b.intent(&r.intent, &mut cx)?;
            rules.push(RuleSpec { id: r.id.clone(), when, intent });
        }
        out_variants.push(VariantPlan {
            index: vi,
            params,
            rules,
            lookback: 0,
            position_nodes: vec![],
            market_reads: vec![],
            signals,
        });
    }
    // per-variant reachability: lookback, position nodes, market reads
    for v in &mut out_variants {
        let mut roots: Vec<usize> = vec![];
        for r in &v.rules {
            roots.push(r.when);
            if let Some(p) = r.intent.price {
                roots.push(p);
            }
            for l in [r.intent.stop, r.intent.target] {
                if let Ov::Set(Lvl::Price(s)) = l {
                    roots.push(s);
                }
            }
        }
        let mut seen = vec![false; b.nodes.len()];
        let mut stack: Vec<usize> = roots.iter().map(|s| b.slot_node[*s]).collect();
        let mut lb = 0;
        let mut reads = std::collections::BTreeSet::new();
        for s in &roots {
            if !b.node_of(*s).position {
                reads.insert(*s);
            }
        }
        while let Some(ni) = stack.pop() {
            if seen[ni] {
                continue;
            }
            seen[ni] = true;
            let n = &b.nodes[ni];
            lb = lb.max(n.lookback);
            if n.position {
                for &i in &n.inputs {
                    if !b.node_of(i).position {
                        reads.insert(i);
                    }
                }
            }
            for &i in &n.inputs {
                stack.push(b.slot_node[i]);
            }
        }
        v.lookback = lb;
        v.position_nodes = (0..b.nodes.len()).filter(|&i| seen[i] && b.nodes[i].position).collect();
        v.market_reads = reads.into_iter().collect();
    }
    let mut frames: Vec<u8> = b.nodes.iter().map(|n| n.frame).filter(|f| *f != u8::MAX && *f != base).collect();
    frames.sort();
    frames.dedup();
    let digest_src = format!("{base}|{:?}|{:?}", b.nodes, out_variants.iter().map(|v| &v.rules).collect::<Vec<_>>());
    Ok(Plan {
        base,
        n_slots: b.slot_node.len(),
        slot_node: b.slot_node,
        nodes: b.nodes,
        variants: out_variants,
        rejected,
        extras: b.extras,
        frames,
        plan_sha256: sha256_hex(digest_src.as_bytes()),
    })
}
