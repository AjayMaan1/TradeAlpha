//! Canonical JSON and content hashes (design §6.9). Python implements the same rules (`hbt.ir.canonical`); shared test
//! vectors keep the two identical.
//!
//! * objects: keys sorted by code point (UTF-8 byte order == code-point order, as Python sorts str)
//! * no insignificant whitespace
//! * numbers: integral values with |x| <= 1e15 are written as integers (`2.0` -> `2`, `-0.0` -> `0`); other numbers in
//!   the shortest round-trip positional form (`0.005`, `0.0000001`); no exponent
//! * the top-level `meta` and `description` are excluded (they do not change behaviour)

use serde_json::Value;
use sha2::{Digest, Sha256};

pub const MAX_ABS_NUMBER: f64 = 1e15;

pub fn number(x: f64) -> String {
    if x.fract() == 0.0 && x.abs() <= MAX_ABS_NUMBER { format!("{}", x as i64) } else { format!("{x}") }
}

fn write(v: &Value, out: &mut String, top: bool) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                out.push_str(&i.to_string())
            } else if let Some(u) = n.as_u64() {
                out.push_str(&u.to_string())
            } else {
                out.push_str(&number(n.as_f64().unwrap_or(0.0)))
            }
        }
        Value::String(s) => out.push_str(&serde_json::to_string(s).unwrap_or_default()),
        Value::Array(a) => {
            out.push('[');
            for (i, x) in a.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                write(x, out, false);
            }
            out.push(']');
        }
        Value::Object(m) => {
            let mut keys: Vec<&String> =
                m.keys().filter(|k| !(top && (k.as_str() == "meta" || k.as_str() == "description"))).collect();
            keys.sort();
            out.push('{');
            for (i, k) in keys.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                out.push_str(&serde_json::to_string(k).unwrap_or_default());
                out.push(':');
                write(&m[*k], out, false);
            }
            out.push('}');
        }
    }
}

pub fn canonical(v: &Value) -> String {
    let mut s = String::new();
    write(v, &mut s, true);
    s
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    let d = Sha256::digest(bytes);
    d.iter().map(|b| format!("{b:02x}")).collect()
}

pub fn ir_sha256(v: &Value) -> String {
    sha256_hex(canonical(v).as_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn canonical_form_is_order_and_format_independent() {
        let a: Value = serde_json::from_str(r#"{"b": 2.0, "a": [0.005, 1e-7, -0.0], "meta": {"x": 1}}"#).unwrap();
        let b: Value = serde_json::from_str(r#"{"a":[0.005,0.0000001,0],"b":2}"#).unwrap();
        assert_eq!(canonical(&a), r#"{"a":[0.005,0.0000001,0],"b":2}"#);
        assert_eq!(canonical(&a), canonical(&b));
        assert_eq!(ir_sha256(&a), ir_sha256(&b));
        let nested: Value = serde_json::from_str(r#"{"x": {"meta": 1}}"#).unwrap();
        assert_eq!(canonical(&nested), r#"{"x":{"meta":1}}"#); // only the TOP-level meta is excluded
    }
}
