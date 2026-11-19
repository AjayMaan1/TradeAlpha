//! hbt-ir - Historify Lab Strategy IR v1 (design: PHASE_1_3_STRATEGY_IR_DESIGN.md).
//!
//! * `ast`      document model + parser (schema check, limits)
//! * `ops`      operator catalogue (arguments, outputs, lookback, causality class)
//! * `compile`  types, causality, params, folding, CSE, lookback, partition -> `Plan`
//! * `canon`    canonical JSON + sha256 (identical rules in Python: `hbt.ir.canonical`)
//! * `explain`  pseudo-code rendering
//! * `error`    structured errors (code + JSON Pointer + hint)

pub mod ast;
pub mod canon;
pub mod compile;
pub mod error;
pub mod explain;
pub mod ops;

pub use compile::{Lvl, Ov, Plan, Program, build_plan, compile, compile_value};
pub use error::{IrError, IrResult};
