//! Structured compile errors: a stable code, a message, the JSON Pointer of the offending node and an optional hint.

use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IrError {
    pub code: &'static str,
    pub message: String,
    pub pointer: String,
    pub hint: Option<String>,
}

impl IrError {
    pub fn new(code: &'static str, pointer: &str, message: impl Into<String>) -> Self {
        IrError { code, message: message.into(), pointer: pointer.to_string(), hint: None }
    }
    pub fn hint(mut self, h: impl Into<String>) -> Self {
        self.hint = Some(h.into());
        self
    }
}

impl fmt::Display for IrError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} at {}: {}", self.code, if self.pointer.is_empty() { "/" } else { &self.pointer }, self.message)?;
        if let Some(h) = &self.hint {
            write!(f, " (hint: {h})")?;
        }
        Ok(())
    }
}

impl std::error::Error for IrError {}

pub type IrResult<T> = Result<T, IrError>;

/// Every error code the compiler can produce (design §7.3). Tests keep one failing fixture per code.
pub const CODES: &[&str] = &[
    "E-SCHEMA",
    "E-CYCLE",
    "E-UNKNOWN-FN",
    "E-ARITY",
    "E-TYPE",
    "E-CAUSAL",
    "E-TF-LOWER",
    "E-TF-UNKNOWN",
    "E-LIMIT",
    "E-PARAM-RANGE",
    "E-GRID-SIZE",
    "E-RISK-NO-STOP",
    "E-INTENT",
    "E-PRODUCT",
    "E-LOOKBACK-DATA",
];
