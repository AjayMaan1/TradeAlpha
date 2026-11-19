#![cfg_attr(test, allow(clippy::inconsistent_digit_grouping))] // test money literals read as rupees_paise
//! hbt-core - Historify Lab execution core.
//!
//! * `money`    fixed-point prices (price units), paise, rates (e8), half-up rounding, tick rounding
//! * `costs`    statutory schedule by trade date + broker tariff + DP charge, per-charge paise rounding, GST
//! * `spec`     ExecSpec v1 (+ errata F1-F12), OrderPlan, Instrument (JSON)
//! * `decision` DecisionSource trait, intents, the boolean-array source
//! * `engine`   bar-by-bar execution (P0-P4), 1m segments, corporate-action position events
//! * `kernels`  streaming kernels of the Strategy IR operators (phase 1.3)
//! * `eval`     IR evaluation: market nodes (+ higher frames via available_at), position nodes, rules -> intents
//! * `metrics`  calendar-correct statistics from the equity curve and trades
//! * `portfolio` several instruments on one account (shared cash, portfolio sizing, max positions)

pub mod costs;
pub mod decision;
pub mod engine;
pub mod eval;
pub mod kernels;
pub mod metrics;
pub mod money;
pub mod portfolio;
pub mod spec;

pub use hbt_ir as ir;

/// Parsed and validated run configuration.
pub struct Config {
    pub spec: spec::ExecSpec,
    pub plan: spec::OrderPlan,
    pub inst: spec::Instrument,
    pub costs: costs::CostModel,
}

impl Config {
    pub fn from_json(spec_json: &str, plan_json: &str, inst_json: &str, costs_json: &str) -> Result<Config, String> {
        let spec: spec::ExecSpec = serde_json::from_str(spec_json).map_err(|e| format!("exec spec: {e}"))?;
        spec.validate()?;
        let plan: spec::OrderPlan = serde_json::from_str(plan_json).map_err(|e| format!("order plan: {e}"))?;
        plan.validate()?;
        let inst: spec::Instrument = serde_json::from_str(inst_json).map_err(|e| format!("instrument: {e}"))?;
        let mut costs: costs::CostModel = serde_json::from_str(costs_json).map_err(|e| format!("cost model: {e}"))?;
        costs.validate()?;
        if inst.lot < 1 || inst.tick < 1 || inst.price_scale < 1 {
            return Err("instrument lot, tick and price_scale must be >= 1".into());
        }
        Ok(Config { spec, plan, inst, costs })
    }

    pub fn run(
        &self,
        cash0: money::Paise,
        bars: &engine::Bars,
        m1: Option<&engine::Bars>,
        events: &[engine::CaEvent],
        src: &mut dyn decision::DecisionSource,
    ) -> Result<engine::Outcome, String> {
        self.run_full(cash0, bars, m1, events, &[], false, src)
    }

    /// `changes`: time-versioned lot / tick sizes; `metrics_only`: fused accumulators (sweeps).
    #[allow(clippy::too_many_arguments)]
    pub fn run_full(
        &self,
        cash0: money::Paise,
        bars: &engine::Bars,
        m1: Option<&engine::Bars>,
        events: &[engine::CaEvent],
        changes: &[engine::InstrumentChange],
        metrics_only: bool,
        src: &mut dyn decision::DecisionSource,
    ) -> Result<engine::Outcome, String> {
        let e = engine::Engine::new(&self.spec, &self.plan, &self.inst, &self.costs, cash0);
        let e = if metrics_only { e.metrics_only() } else { e };
        e.run_with(bars, m1, events, changes, src)
    }
}

/// One backtest with boolean signal arrays (the Phase 1.2 API; used by the bindings and the Rust tests).
#[allow(clippy::too_many_arguments)]
pub fn run_json(
    spec_json: &str,
    plan_json: &str,
    inst_json: &str,
    costs_json: &str,
    cash0: money::Paise,
    bars: &engine::Bars,
    m1: Option<&engine::Bars>,
    signals: [&[u8]; 4],
) -> Result<engine::Outcome, String> {
    let cfg = Config::from_json(spec_json, plan_json, inst_json, costs_json)?;
    let mut src = decision::ArraySignals::new(signals[0], signals[1], signals[2], signals[3], &cfg.plan);
    src.check(bars.len())?;
    cfg.run(cash0, bars, m1, &[], &mut src)
}
