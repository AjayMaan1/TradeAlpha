//! IR v1 document model and parser (design §6). The parser is the schema check: every structural problem is an
//! E-SCHEMA error with the JSON Pointer of the offending value; limits (§6.8) are E-LIMIT.

use std::collections::BTreeMap;

use serde_json::{Map, Value};

use crate::canon::MAX_ABS_NUMBER;
use crate::error::{IrError, IrResult};

pub const MAX_NODES: usize = 10_000;
pub const MAX_DEPTH: usize = 64;
pub const MAX_RULES: usize = 64;
pub const MAX_DOC_BYTES: usize = 1 << 20;
pub const MAX_NAME: usize = 64;
pub const MAX_DESCRIPTION: usize = 4096;
pub const MAX_LOOKBACK: usize = 100_000;

#[derive(Debug, Clone, PartialEq)]
pub enum ExprKind {
    Num(f64),
    Bool(bool),
    /// "HH:MM" -> minutes after midnight IST
    Time(i32),
    Col {
        col: String,
        tf: Option<String>,
    },
    Sig(String),
    Param(String),
    Call {
        name: String,
        args: Vec<Expr>,
        out: Option<String>,
        tf: Option<String>,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub struct Expr {
    pub kind: ExprKind,
    pub ptr: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ParamType {
    Int,
    Float,
    Bool,
    Choice,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ParamSpec {
    pub name: String,
    pub ty: ParamType,
    pub default: f64,
    pub min: Option<f64>,
    pub max: Option<f64>,
    pub step: Option<f64>,
    pub choices: Vec<f64>,
    pub ptr: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IntentKind {
    EnterLong,
    EnterShort,
    ExitLong,
    ExitShort,
    ExitAll,
}

impl IntentKind {
    pub fn is_entry(self) -> bool {
        matches!(self, IntentKind::EnterLong | IntentKind::EnterShort)
    }
    pub fn name(self) -> &'static str {
        match self {
            IntentKind::EnterLong => "enter_long",
            IntentKind::EnterShort => "enter_short",
            IntentKind::ExitLong => "exit_long",
            IntentKind::ExitShort => "exit_short",
            IntentKind::ExitAll => "exit_all",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OrderType {
    Market,
    Limit,
    Stop,
    /// triggers like a stop, then rests (or fills) as a limit at `limit` (default: the trigger price)
    StopLimit,
}

#[derive(Debug, Clone, PartialEq)]
pub enum LevelAst {
    Pct(Expr),
    Price(Expr),
    Off,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SizeMode {
    AllIn,
    PctEquity,
    FixedQty,
    FixedValue,
    RiskPct,
}

#[derive(Debug, Clone, PartialEq)]
pub struct IntentAst {
    pub kind: IntentKind,
    pub order: OrderType,
    pub price: Option<Expr>,
    /// stop-limit: the limit leg (the order's `price` is the trigger)
    pub limit: Option<Expr>,
    pub offset_pct: Option<Expr>,
    pub size: Option<(SizeMode, Option<Expr>)>,
    pub stop: Option<LevelAst>,
    pub target: Option<LevelAst>,
    /// None = OrderPlan default; Some(None) = explicitly none (JSON null); Some(Some(x)) = value
    pub trail_pct: Option<Option<Expr>>,
    pub time_stop_bars: Option<Option<Expr>>,
    pub expiry_bars: Option<Expr>,
    pub ptr: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Rule {
    pub id: String,
    pub when: Expr,
    pub intent: IntentAst,
    pub ptr: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Document {
    pub ir_version: u32,
    pub name: String,
    pub params: BTreeMap<String, ParamSpec>,
    pub constraints: Vec<Expr>,
    pub interval: Option<String>,
    pub adjustment: Option<String>,
    pub signals: Vec<(String, Expr)>,
    pub rules: Vec<Rule>,
    pub product: Option<String>,
    pub raw: Value,
}

fn err(code: &'static str, ptr: &str, msg: impl Into<String>) -> IrError {
    IrError::new(code, ptr, msg)
}

fn esc(key: &str) -> String {
    key.replace('~', "~0").replace('/', "~1")
}

fn obj<'a>(v: &'a Value, ptr: &str, what: &str) -> IrResult<&'a Map<String, Value>> {
    v.as_object().ok_or_else(|| err("E-SCHEMA", ptr, format!("{what} must be an object")))
}

fn allowed(m: &Map<String, Value>, ptr: &str, keys: &[&str]) -> IrResult<()> {
    for k in m.keys() {
        if !keys.contains(&k.as_str()) {
            return Err(err("E-SCHEMA", &format!("{ptr}/{}", esc(k)), format!("unknown field '{k}'"))
                .hint(format!("allowed: {}", keys.join(", "))));
        }
    }
    Ok(())
}

fn name_ok(s: &str, ptr: &str) -> IrResult<()> {
    if s.is_empty() || s.len() > MAX_NAME || !s.chars().all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-') {
        return Err(err("E-SCHEMA", ptr, format!("invalid name '{s}' (1-{MAX_NAME} chars: letters, digits, _ or -)")));
    }
    Ok(())
}

fn num(v: &Value, ptr: &str) -> IrResult<f64> {
    let x = v.as_f64().ok_or_else(|| err("E-SCHEMA", ptr, "expected a number"))?;
    if !x.is_finite() || x.abs() > MAX_ABS_NUMBER {
        return Err(err("E-LIMIT", ptr, format!("numbers must be finite and |x| <= {MAX_ABS_NUMBER:e}")));
    }
    Ok(x)
}

pub fn parse_time(s: &str) -> Option<i32> {
    let (h, m) = s.split_once(':')?;
    if h.len() != 2 || m.len() != 2 {
        return None;
    }
    let (h, m): (i32, i32) = (h.parse().ok()?, m.parse().ok()?);
    ((0..24).contains(&h) && (0..60).contains(&m)).then_some(h * 60 + m)
}

struct Parser {
    nodes: usize,
}

impl Parser {
    fn expr(&mut self, v: &Value, ptr: &str, depth: usize) -> IrResult<Expr> {
        if depth > MAX_DEPTH {
            return Err(err("E-LIMIT", ptr, format!("expression deeper than {MAX_DEPTH}")));
        }
        self.nodes += 1;
        if self.nodes > MAX_NODES {
            return Err(err("E-LIMIT", ptr, format!("more than {MAX_NODES} expression nodes")));
        }
        let kind = match v {
            Value::Number(_) => ExprKind::Num(num(v, ptr)?),
            Value::Bool(b) => ExprKind::Bool(*b),
            Value::String(s) => ExprKind::Time(parse_time(s).ok_or_else(|| {
                err("E-SCHEMA", ptr, format!("string '{s}' is not a time \"HH:MM\""))
                    .hint("strings are only allowed as times; reference signals with {\"sig\": name}")
            })?),
            Value::Object(m) => {
                if let Some(c) = m.get("col") {
                    allowed(m, ptr, &["col", "tf"])?;
                    let col =
                        c.as_str().ok_or_else(|| err("E-SCHEMA", &format!("{ptr}/col"), "col must be a string"))?;
                    ExprKind::Col { col: col.to_string(), tf: self.tf(m, ptr)? }
                } else if let Some(s) = m.get("sig") {
                    allowed(m, ptr, &["sig"])?;
                    ExprKind::Sig(
                        s.as_str()
                            .ok_or_else(|| err("E-SCHEMA", &format!("{ptr}/sig"), "sig must be a string"))?
                            .into(),
                    )
                } else if let Some(p) = m.get("param") {
                    allowed(m, ptr, &["param"])?;
                    ExprKind::Param(
                        p.as_str()
                            .ok_or_else(|| err("E-SCHEMA", &format!("{ptr}/param"), "param must be a string"))?
                            .into(),
                    )
                } else if let Some(f) = m.get("fn") {
                    allowed(m, ptr, &["fn", "args", "out", "tf"])?;
                    let name = f
                        .as_str()
                        .ok_or_else(|| err("E-SCHEMA", &format!("{ptr}/fn"), "fn must be a string"))?
                        .to_string();
                    let mut args = vec![];
                    if let Some(a) = m.get("args") {
                        let arr = a
                            .as_array()
                            .ok_or_else(|| err("E-SCHEMA", &format!("{ptr}/args"), "args must be a list"))?;
                        for (i, x) in arr.iter().enumerate() {
                            args.push(self.expr(x, &format!("{ptr}/args/{i}"), depth + 1)?);
                        }
                    }
                    let out = match m.get("out") {
                        None => None,
                        Some(o) => Some(
                            o.as_str()
                                .ok_or_else(|| err("E-SCHEMA", &format!("{ptr}/out"), "out must be a string"))?
                                .to_string(),
                        ),
                    };
                    ExprKind::Call { name, args, out, tf: self.tf(m, ptr)? }
                } else {
                    return Err(err("E-SCHEMA", ptr, "expression object needs one of: fn, col, sig, param"));
                }
            }
            Value::Null => return Err(err("E-SCHEMA", ptr, "null is not an expression")),
            Value::Array(_) => return Err(err("E-SCHEMA", ptr, "a list is not an expression")),
        };
        Ok(Expr { kind, ptr: ptr.to_string() })
    }

    fn tf(&self, m: &Map<String, Value>, ptr: &str) -> IrResult<Option<String>> {
        match m.get("tf") {
            None => Ok(None),
            Some(Value::String(s)) => {
                if crate::ops::frame_rank(s).is_none() {
                    return Err(err("E-TF-UNKNOWN", &format!("{ptr}/tf"), format!("unknown interval '{s}'"))
                        .hint("1m 3m 5m 10m 15m 30m 1h D W M"));
                }
                Ok(Some(s.clone()))
            }
            Some(_) => Err(err("E-SCHEMA", &format!("{ptr}/tf"), "tf must be an interval code")),
        }
    }
}

fn parse_param(name: &str, v: &Value, ptr: &str) -> IrResult<ParamSpec> {
    name_ok(name, ptr)?;
    let m = obj(v, ptr, "a parameter")?;
    allowed(m, ptr, &["type", "default", "min", "max", "step", "choices", "prior", "description"])?;
    let ty = match m.get("type").and_then(Value::as_str) {
        Some("int") => ParamType::Int,
        Some("float") => ParamType::Float,
        Some("bool") => ParamType::Bool,
        Some("choice") => ParamType::Choice,
        _ => return Err(err("E-SCHEMA", &format!("{ptr}/type"), "type must be int, float, bool or choice")),
    };
    let d = m.get("default").ok_or_else(|| err("E-SCHEMA", ptr, "a parameter needs a default"))?;
    let default = match (ty, d) {
        (ParamType::Bool, Value::Bool(b)) => *b as u8 as f64,
        (ParamType::Bool, _) => return Err(err("E-SCHEMA", &format!("{ptr}/default"), "bool default expected")),
        _ => num(d, &format!("{ptr}/default"))?,
    };
    let opt = |k: &str| -> IrResult<Option<f64>> { m.get(k).map(|x| num(x, &format!("{ptr}/{k}"))).transpose() };
    let choices = match m.get("choices") {
        None => vec![],
        Some(Value::Array(a)) => {
            a.iter().enumerate().map(|(i, x)| num(x, &format!("{ptr}/choices/{i}"))).collect::<IrResult<Vec<_>>>()?
        }
        Some(_) => return Err(err("E-SCHEMA", &format!("{ptr}/choices"), "choices must be a list of numbers")),
    };
    if ty == ParamType::Choice && choices.is_empty() {
        return Err(err("E-SCHEMA", ptr, "a choice parameter needs choices"));
    }
    let p = ParamSpec {
        name: name.into(),
        ty,
        default,
        min: opt("min")?,
        max: opt("max")?,
        step: opt("step")?,
        choices,
        ptr: ptr.into(),
    };
    if let (Some(lo), Some(hi)) = (p.min, p.max)
        && lo > hi
    {
        return Err(err("E-SCHEMA", ptr, "min > max"));
    }
    if let Some(s) = p.step
        && s <= 0.0
    {
        return Err(err("E-SCHEMA", &format!("{ptr}/step"), "step must be > 0"));
    }
    p.check(default, &format!("{ptr}/default"))?;
    Ok(p)
}

impl ParamSpec {
    /// Validate a value for this parameter (type, range, choices).
    pub fn check(&self, x: f64, ptr: &str) -> IrResult<()> {
        let bad = |m: String| Err(err("E-PARAM-RANGE", ptr, m));
        match self.ty {
            ParamType::Int if x.fract() != 0.0 => return bad(format!("{} must be an integer, got {x}", self.name)),
            ParamType::Bool if x != 0.0 && x != 1.0 => return bad(format!("{} must be a bool", self.name)),
            ParamType::Choice if !self.choices.contains(&x) => {
                return bad(format!("{} must be one of {:?}, got {x}", self.name, self.choices));
            }
            _ => {}
        }
        if let Some(lo) = self.min
            && x < lo
        {
            return bad(format!("{} = {x} is below min {lo}", self.name));
        }
        if let Some(hi) = self.max
            && x > hi
        {
            return bad(format!("{} = {x} is above max {hi}", self.name));
        }
        Ok(())
    }
}

fn level(p: &mut Parser, v: &Value, ptr: &str) -> IrResult<LevelAst> {
    if v.is_null() {
        return Ok(LevelAst::Off);
    }
    let m = obj(v, ptr, "a level")?;
    allowed(m, ptr, &["pct", "price"])?;
    match (m.get("pct"), m.get("price")) {
        (Some(x), None) => Ok(LevelAst::Pct(p.expr(x, &format!("{ptr}/pct"), 1)?)),
        (None, Some(x)) => Ok(LevelAst::Price(p.expr(x, &format!("{ptr}/price"), 1)?)),
        _ => Err(err("E-INTENT", ptr, "a level is {\"pct\": x} or {\"price\": expression} (or null for none)")),
    }
}

fn intent(p: &mut Parser, v: &Value, ptr: &str) -> IrResult<IntentAst> {
    let m = obj(v, ptr, "do")?;
    if m.len() != 1 {
        return Err(err("E-INTENT", ptr, "do holds exactly one intent")
            .hint("enter_long, enter_short, exit_long, exit_short or exit_all"));
    }
    let (k, body) = m.iter().next().unwrap();
    let kind = match k.as_str() {
        "enter_long" => IntentKind::EnterLong,
        "enter_short" => IntentKind::EnterShort,
        "exit_long" => IntentKind::ExitLong,
        "exit_short" => IntentKind::ExitShort,
        "exit_all" => IntentKind::ExitAll,
        other => {
            return Err(err("E-INTENT", &format!("{ptr}/{}", esc(other)), format!("unknown intent '{other}'"))
                .hint("enter_long, enter_short, exit_long, exit_short or exit_all"));
        }
    };
    let ip = format!("{ptr}/{k}");
    let b = obj(body, &ip, "an intent")?;
    let mut out = IntentAst {
        kind,
        order: OrderType::Market,
        price: None,
        limit: None,
        offset_pct: None,
        size: None,
        stop: None,
        target: None,
        trail_pct: None,
        time_stop_bars: None,
        expiry_bars: None,
        ptr: ip.clone(),
    };
    if !kind.is_entry() {
        if !b.is_empty() {
            return Err(err("E-INTENT", &ip, "exit intents take no options ({})"));
        }
        return Ok(out);
    }
    allowed(b, &ip, &["order", "size", "bracket", "expiry_bars"])?;
    if let Some(o) = b.get("order") {
        let op = format!("{ip}/order");
        let om = obj(o, &op, "order")?;
        allowed(om, &op, &["type", "price", "offset_pct", "limit"])?;
        out.order = match om.get("type").and_then(Value::as_str).unwrap_or("market") {
            "market" => OrderType::Market,
            "limit" => OrderType::Limit,
            "stop" => OrderType::Stop,
            "stop_limit" => OrderType::StopLimit,
            t => {
                return Err(err(
                    "E-INTENT",
                    &format!("{op}/type"),
                    format!("order type '{t}' (market, limit, stop, stop_limit)"),
                ));
            }
        };
        if let Some(x) = om.get("price") {
            out.price = Some(p.expr(x, &format!("{op}/price"), 1)?);
        }
        if let Some(x) = om.get("offset_pct") {
            out.offset_pct = Some(p.expr(x, &format!("{op}/offset_pct"), 1)?);
        }
        if let Some(x) = om.get("limit") {
            out.limit = Some(p.expr(x, &format!("{op}/limit"), 1)?);
        }
        if out.order == OrderType::Market && (out.price.is_some() || out.offset_pct.is_some()) {
            return Err(err("E-INTENT", &op, "a market order takes no price"));
        }
        if out.order != OrderType::Market && out.price.is_some() == out.offset_pct.is_some() {
            return Err(err("E-INTENT", &op, "a limit / stop order needs exactly one of price or offset_pct"));
        }
        if out.limit.is_some() && out.order != OrderType::StopLimit {
            return Err(err("E-INTENT", &op, "'limit' belongs to a stop_limit order (the trigger is 'price')"));
        }
    }
    if let Some(s) = b.get("size") {
        let sp = format!("{ip}/size");
        let sm = obj(s, &sp, "size")?;
        if sm.len() != 1 {
            return Err(err(
                "E-INTENT",
                &sp,
                "size holds exactly one of all_in, pct_equity, fixed_qty, fixed_value, risk_pct",
            ));
        }
        let (k, x) = sm.iter().next().unwrap();
        let mode = match k.as_str() {
            "all_in" => SizeMode::AllIn,
            "pct_equity" => SizeMode::PctEquity,
            "fixed_qty" => SizeMode::FixedQty,
            "fixed_value" => SizeMode::FixedValue,
            "risk_pct" => SizeMode::RiskPct,
            other => return Err(err("E-INTENT", &format!("{sp}/{}", esc(other)), format!("unknown sizing '{other}'"))),
        };
        let value = if mode == SizeMode::AllIn { None } else { Some(p.expr(x, &format!("{sp}/{k}"), 1)?) };
        out.size = Some((mode, value));
    }
    if let Some(br) = b.get("bracket") {
        let bp = format!("{ip}/bracket");
        let bm = obj(br, &bp, "bracket")?;
        allowed(bm, &bp, &["stop", "target", "trail_pct", "time_stop_bars"])?;
        if let Some(x) = bm.get("stop") {
            out.stop = Some(level(p, x, &format!("{bp}/stop"))?);
        }
        if let Some(x) = bm.get("target") {
            out.target = Some(level(p, x, &format!("{bp}/target"))?);
        }
        if let Some(x) = bm.get("trail_pct") {
            out.trail_pct = Some(if x.is_null() { None } else { Some(p.expr(x, &format!("{bp}/trail_pct"), 1)?) });
        }
        if let Some(x) = bm.get("time_stop_bars") {
            out.time_stop_bars =
                Some(if x.is_null() { None } else { Some(p.expr(x, &format!("{bp}/time_stop_bars"), 1)?) });
        }
    }
    if let Some(x) = b.get("expiry_bars") {
        out.expiry_bars = Some(p.expr(x, &format!("{ip}/expiry_bars"), 1)?);
    }
    Ok(out)
}

pub fn parse(text: &str) -> IrResult<Document> {
    if text.len() > MAX_DOC_BYTES {
        return Err(err("E-LIMIT", "", format!("document larger than {MAX_DOC_BYTES} bytes")));
    }
    let raw: Value = serde_json::from_str(text).map_err(|e| err("E-SCHEMA", "", format!("invalid JSON: {e}")))?;
    parse_value(raw)
}

pub fn parse_value(raw: Value) -> IrResult<Document> {
    let m = obj(&raw, "", "the document")?;
    allowed(
        m,
        "",
        &[
            "ir_version",
            "name",
            "description",
            "params",
            "constraints",
            "universe",
            "data",
            "signals",
            "rules",
            "product",
            "meta",
        ],
    )?;
    let ir_version = match m.get("ir_version").and_then(Value::as_u64) {
        Some(1) => 1,
        Some(v) => return Err(err("E-SCHEMA", "/ir_version", format!("unsupported ir_version {v} (this build: 1)"))),
        None => return Err(err("E-SCHEMA", "/ir_version", "ir_version (1) is required")),
    };
    let name = m.get("name").and_then(Value::as_str).ok_or_else(|| err("E-SCHEMA", "/name", "name is required"))?;
    if name.is_empty() || name.len() > 200 {
        return Err(err("E-SCHEMA", "/name", "name must be 1-200 characters"));
    }
    if let Some(d) = m.get("description")
        && d.as_str().map(|s| s.len() > MAX_DESCRIPTION).unwrap_or(true)
    {
        return Err(err("E-LIMIT", "/description", format!("description must be a string <= {MAX_DESCRIPTION} bytes")));
    }
    let mut p = Parser { nodes: 0 };
    let mut params = BTreeMap::new();
    if let Some(ps) = m.get("params") {
        for (k, v) in obj(ps, "/params", "params")? {
            params.insert(k.clone(), parse_param(k, v, &format!("/params/{}", esc(k)))?);
        }
    }
    let mut constraints = vec![];
    if let Some(c) = m.get("constraints") {
        let a = c.as_array().ok_or_else(|| err("E-SCHEMA", "/constraints", "constraints must be a list"))?;
        for (i, x) in a.iter().enumerate() {
            constraints.push(p.expr(x, &format!("/constraints/{i}"), 0)?);
        }
    }
    let mut interval = None;
    if let Some(u) = m.get("universe") {
        let um = obj(u, "/universe", "universe")?;
        allowed(um, "/universe", &["interval", "watchlist", "symbols", "exchange"])?;
        if let Some(iv) = um.get("interval") {
            let s = iv.as_str().ok_or_else(|| err("E-SCHEMA", "/universe/interval", "interval must be a string"))?;
            if crate::ops::frame_rank(s).is_none() {
                return Err(err("E-TF-UNKNOWN", "/universe/interval", format!("unknown interval '{s}'")));
            }
            interval = Some(s.to_string());
        }
    }
    let mut adjustment = None;
    if let Some(d) = m.get("data") {
        let dm = obj(d, "/data", "data")?;
        allowed(dm, "/data", &["adjustment"])?;
        if let Some(a) = dm.get("adjustment") {
            match a.as_str() {
                Some(s @ ("raw" | "adjusted" | "total_return")) => adjustment = Some(s.to_string()),
                _ => {
                    return Err(err(
                        "E-SCHEMA",
                        "/data/adjustment",
                        "adjustment must be raw, adjusted or total_return",
                    ));
                }
            }
        }
    }
    let mut signals = vec![];
    if let Some(s) = m.get("signals") {
        for (k, v) in obj(s, "/signals", "signals")? {
            name_ok(k, &format!("/signals/{}", esc(k)))?;
            signals.push((k.clone(), p.expr(v, &format!("/signals/{}", esc(k)), 0)?));
        }
    }
    let rules_v = m.get("rules").ok_or_else(|| err("E-SCHEMA", "/rules", "rules are required"))?;
    let ra = rules_v.as_array().ok_or_else(|| err("E-SCHEMA", "/rules", "rules must be a list"))?;
    if ra.is_empty() {
        return Err(err("E-SCHEMA", "/rules", "at least one rule is required"));
    }
    if ra.len() > MAX_RULES {
        return Err(err("E-LIMIT", "/rules", format!("more than {MAX_RULES} rules")));
    }
    let mut rules = vec![];
    for (i, r) in ra.iter().enumerate() {
        let rp = format!("/rules/{i}");
        let rm = obj(r, &rp, "a rule")?;
        allowed(rm, &rp, &["id", "when", "do"])?;
        let id = match rm.get("id") {
            None => format!("r{i}"),
            Some(Value::String(s)) => {
                name_ok(s, &format!("{rp}/id"))?;
                s.clone()
            }
            Some(_) => return Err(err("E-SCHEMA", &format!("{rp}/id"), "id must be a string")),
        };
        let when =
            p.expr(rm.get("when").ok_or_else(|| err("E-SCHEMA", &rp, "a rule needs when"))?, &format!("{rp}/when"), 0)?;
        let intent =
            intent(&mut p, rm.get("do").ok_or_else(|| err("E-SCHEMA", &rp, "a rule needs do"))?, &format!("{rp}/do"))?;
        rules.push(Rule { id, when, intent, ptr: rp });
    }
    let product = match m.get("product") {
        None => None,
        Some(Value::String(s)) if ["MIS", "CNC", "NRML"].contains(&s.as_str()) => Some(s.clone()),
        Some(_) => return Err(err("E-SCHEMA", "/product", "product must be MIS, CNC or NRML")),
    };
    Ok(Document {
        ir_version,
        name: name.to_string(),
        params,
        constraints,
        interval,
        adjustment,
        signals,
        rules,
        product,
        raw,
    })
}
