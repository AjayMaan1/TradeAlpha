//! Compiler tests at the Rust level (the full fixture-per-error-code suite runs through Python).
use std::collections::BTreeMap;

use hbt_ir::{build_plan, compile};

const DOC: &str = r#"{
  "ir_version": 1, "name": "t",
  "params": {"fast": {"type": "int", "default": 9, "min": 2, "max": 50}, "slow": {"type": "int", "default": 21, "min": 5, "max": 200}},
  "signals": {"f": {"fn": "ema", "args": [{"col": "close"}, {"param": "fast"}]},
              "s": {"fn": "ema", "args": [{"col": "close"}, {"param": "slow"}]}},
  "rules": [{"when": {"fn": "cross_below", "args": [{"sig": "f"}, {"sig": "s"}]}, "do": {"exit_long": {}}},
            {"when": {"fn": "cross_above", "args": [{"sig": "f"}, {"sig": "s"}]}, "do": {"enter_long": {}}}]
}"#;

fn v(f: f64, s: f64) -> BTreeMap<String, f64> {
    BTreeMap::from([("fast".to_string(), f), ("slow".to_string(), s)])
}

#[test]
fn sweep_variants_share_market_nodes() {
    let p = compile(DOC).unwrap();
    // 4 fast x 4 slow variants: 8 distinct EMAs (+ close), not 32
    let vs: Vec<_> = [5.0, 9.0, 13.0, 17.0].iter().flat_map(|f| [21.0, 34.0, 55.0, 89.0].map(|s| v(*f, s))).collect();
    let plan = build_plan(&p, &vs, "5m", &[]).unwrap();
    let emas = plan.nodes.iter().filter(|n| format!("{:?}", n.op) == "Ema").count();
    assert_eq!(emas, 8);
    assert_eq!(plan.variants.len(), 16);
    assert_eq!(plan.variants[0].lookback, 21); // ema(21) 20 + cross 1
}

#[test]
fn causality_and_tf_errors_carry_codes_and_pointers() {
    let shift = DOC.replace(
        r#"{"fn": "ema", "args": [{"col": "close"}, {"param": "fast"}]}"#,
        r#"{"fn": "shift", "args": [{"col": "close"}, -1]}"#,
    );
    let e = compile(&shift).unwrap_err();
    assert_eq!((e.code, e.pointer.as_str()), ("E-CAUSAL", "/signals/f/args/1"));
    let tf = DOC
        .replace(r#"{"col": "close"}, {"param": "fast"}"#, r#"{"col": "close", "tf": "1m"}, {"param": "fast"}"#)
        .replace(r#""name": "t","#, r#""name": "t", "universe": {"interval": "5m"},"#);
    assert_eq!(compile(&tf).unwrap_err().code, "E-TF-LOWER");
    let p = compile(DOC).unwrap();
    assert_eq!(build_plan(&p, &[v(1.0, 21.0)], "5m", &[]).unwrap_err().code, "E-PARAM-RANGE");
}

#[test]
fn position_nodes_are_private_to_their_variant() {
    let doc = DOC.replace(
        r#"{"fn": "cross_below", "args": [{"sig": "f"}, {"sig": "s"}]}"#,
        r#"{"fn": "gt", "args": [{"fn": "bars_in_trade"}, {"param": "fast"}]}"#,
    );
    let p = compile(&doc).unwrap();
    let plan = build_plan(&p, &[v(5.0, 21.0), v(9.0, 21.0)], "5m", &[]).unwrap();
    assert!(plan.variants.iter().all(|v| !v.position_nodes.is_empty()));
    assert_ne!(plan.variants[0].position_nodes, plan.variants[1].position_nodes);
}
