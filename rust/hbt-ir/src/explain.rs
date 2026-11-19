//! Pseudo-code rendering of a document (what the UI shows next to the JSON, design §7.2).

use crate::ast::{Document, Expr, ExprKind, IntentAst, LevelAst, OrderType, SizeMode};
use crate::canon::number;

fn infix(name: &str) -> Option<&'static str> {
    Some(match name {
        "add" => "+",
        "sub" => "-",
        "mul" => "*",
        "div" => "/",
        "gt" => ">",
        "ge" => ">=",
        "lt" => "<",
        "le" => "<=",
        "eq" => "==",
        "ne" => "!=",
        "and" => "and",
        "or" => "or",
        _ => return None,
    })
}

pub fn expr(x: &Expr) -> String {
    match &x.kind {
        ExprKind::Num(v) => number(*v),
        ExprKind::Bool(b) => b.to_string(),
        ExprKind::Time(m) => format!("{:02}:{:02}", m / 60, m % 60),
        ExprKind::Param(p) => p.clone(),
        ExprKind::Sig(s) => s.clone(),
        ExprKind::Col { col, tf } => match tf {
            Some(t) => format!("{col}@{t}"),
            None => col.clone(),
        },
        ExprKind::Call { name, args, out, tf } => {
            let a: Vec<String> = args.iter().map(expr).collect();
            let mut s = match (infix(name), a.len()) {
                (Some(sym), n) if n >= 2 => format!("({})", a.join(&format!(" {sym} "))),
                _ if name == "not" && a.len() == 1 => format!("not {}", a[0]),
                _ => format!("{}({})", name.to_uppercase(), a.join(", ")),
            };
            if let Some(o) = out {
                s = format!("{s}.{o}");
            }
            if let Some(t) = tf {
                s = format!("{s}@{t}");
            }
            s
        }
    }
}

/// a fraction as a percentage ("0.02" -> "2 %"); parameters are shown as "k x 100 %"
fn pct(x: &Expr) -> String {
    match &x.kind {
        ExprKind::Num(v) => format!("{} %", number((v * 1e6).round() / 1e4)),
        _ => format!("{} x 100 %", expr(x)),
    }
}

fn level(l: &Option<LevelAst>) -> Option<String> {
    match l {
        None => None,
        Some(LevelAst::Off) => Some("none".into()),
        Some(LevelAst::Pct(x)) => Some(pct(x)),
        Some(LevelAst::Price(x)) => Some(expr(x)),
    }
}

fn intent(it: &IntentAst) -> String {
    let mut s = it.kind.name().replace('_', " ");
    if !it.kind.is_entry() {
        return s;
    }
    s.push_str(match it.order {
        OrderType::Market => " market",
        OrderType::Limit => " limit",
        OrderType::Stop => " stop",
        OrderType::StopLimit => " stop-limit",
    });
    if let Some(p) = &it.price {
        s.push_str(&format!(" @ {}", expr(p)));
    }
    if let Some(l) = &it.limit {
        s.push_str(&format!(", limit {}", expr(l)));
    }
    if let Some(p) = &it.offset_pct {
        s.push_str(&format!(" @ close {} {}", if it.order == OrderType::Limit { "-/+" } else { "+/-" }, pct(p)));
    }
    if let Some((m, v)) = &it.size {
        let v = v.as_ref().map(expr).unwrap_or_default();
        s.push_str(&match m {
            SizeMode::AllIn => ", size all-in".to_string(),
            SizeMode::PctEquity => format!(", size {v} x equity"),
            SizeMode::FixedQty => format!(", size {v} shares"),
            SizeMode::FixedValue => format!(", size Rs {v}"),
            SizeMode::RiskPct => format!(
                ", size risking {} of equity",
                it.size.as_ref().and_then(|s| s.1.as_ref()).map(pct).unwrap_or(v)
            ),
        });
    }
    if let Some(l) = level(&it.stop) {
        s.push_str(&format!(", stop {l}"));
    }
    if let Some(l) = level(&it.target) {
        s.push_str(&format!(", target {l}"));
    }
    match &it.trail_pct {
        Some(Some(x)) => s.push_str(&format!(", trail {}", pct(x))),
        Some(None) => s.push_str(", no trail"),
        None => {}
    }
    match &it.time_stop_bars {
        Some(Some(x)) => s.push_str(&format!(", exit after {} bars", expr(x))),
        Some(None) => s.push_str(", no time stop"),
        None => {}
    }
    if let Some(x) = &it.expiry_bars {
        s.push_str(&format!(", order valid {} bars", expr(x)));
    }
    s
}

pub fn explain(doc: &Document) -> String {
    let mut out = format!("strategy \"{}\"", doc.name);
    if let Some(i) = &doc.interval {
        out.push_str(&format!(" on {i}"));
    }
    if let Some(p) = &doc.product {
        out.push_str(&format!(", {p}"));
    }
    if let Some(a) = &doc.adjustment {
        out.push_str(&format!(", {a} prices"));
    }
    out.push('\n');
    if !doc.params.is_empty() {
        out.push_str("params\n");
        for (k, p) in &doc.params {
            let range = match (p.min, p.max) {
                (Some(a), Some(b)) => format!(" in [{}, {}]", number(a), number(b)),
                _ => String::new(),
            };
            out.push_str(&format!("  {k} = {}{range}\n", number(p.default)));
        }
    }
    for c in &doc.constraints {
        out.push_str(&format!("require {}\n", expr(c)));
    }
    if !doc.signals.is_empty() {
        out.push_str("signals\n");
        for (k, x) in &doc.signals {
            out.push_str(&format!("  {k} = {}\n", expr(x)));
        }
    }
    out.push_str("rules (exits first, then the first matching entry)\n");
    for r in &doc.rules {
        out.push_str(&format!("  [{}] when {} -> {}\n", r.id, expr(&r.when), intent(&r.intent)));
    }
    out
}
