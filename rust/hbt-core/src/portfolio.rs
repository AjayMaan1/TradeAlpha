//! Portfolio mode (unification U2 follow-up): several instruments on ONE account.
//!
//! Every instrument (a "leg") runs the normal single-instrument engine with its own cash LEDGER that starts at
//! zero; the legs are stepped together on the union of their bar timestamps. Before a leg processes a bar it is
//! told the rest of the account (`Stepper::set_account`):
//!
//!   cash    = starting cash + the other legs' ledgers          -> what a new long can spend
//!   equity  = cash + the other legs' positions at their last close   -> sizing base (all_in, pct_equity, risk_pct)
//!   gross   = |the other legs' positions| at their last close  -> shorts count against the leverage limit
//!   open    = positions open in the other legs                 -> `max_positions`
//!
//! So legs compete for one pool of cash, sizing is relative to the whole portfolio, and a leg cannot open a new
//! position while `max_positions` are open elsewhere (reject `max_positions`). Legs with a bar at the same
//! timestamp are processed in input order (the universe order), which decides who gets the cash first - the
//! same deterministic rule for every run. Portfolio equity is recorded after every timestamp of the union.
//! With one leg, the run is identical to `Engine::run_with` (tested).

use crate::Config;
use crate::decision::DecisionSource;
use crate::engine::{Bars, CaEvent, Engine, InstrumentChange, Outcome, Stepper};
use crate::money::Paise;

/// One instrument of a portfolio run.
pub struct Leg<'b> {
    pub bars: &'b Bars<'b>,
    pub m1: Option<&'b Bars<'b>>,
    pub events: &'b [CaEvent],
    pub changes: &'b [InstrumentChange],
}

#[derive(Debug, Default)]
pub struct PortfolioOutcome {
    /// per leg: fills, trades, intents, rejects; `equity` / `cash` of a leg are its ledger (P&L contribution)
    pub legs: Vec<Outcome>,
    /// union timeline and the portfolio equity at each timestamp (paise)
    pub ts: Vec<i64>,
    pub equity: Vec<Paise>,
    pub cash: Paise,
    /// the most positions open at once
    pub peak_open: u32,
}

/// Run `legs` (one `Config` each: the instrument and the cost model may differ) on one account of `cash0` paise.
pub fn run_portfolio(
    cfgs: &[Config],
    legs: &[Leg],
    srcs: &mut [Box<dyn DecisionSource + '_>],
    cash0: Paise,
    max_positions: Option<u32>,
) -> Result<PortfolioOutcome, String> {
    if cfgs.len() != legs.len() || srcs.len() != legs.len() {
        return Err("portfolio: one config and one decision source per leg".into());
    }
    if legs.is_empty() {
        return Err("portfolio: no legs".into());
    }
    if max_positions == Some(0) {
        return Err("portfolio: max_positions must be >= 1".into());
    }
    let mut st: Vec<Stepper> = Vec::with_capacity(legs.len());
    for (c, l) in cfgs.iter().zip(legs) {
        let e = Engine::new(&c.spec, &c.plan, &c.inst, &c.costs, 0).with_max_positions(max_positions);
        st.push(e.start(l.bars, l.m1, l.events, l.changes)?);
    }
    let mut out = PortfolioOutcome::default();
    while let Some(now) = st.iter().filter_map(|s| s.next_ts()).min() {
        for i in 0..st.len() {
            if st[i].next_ts() != Some(now) {
                continue;
            }
            let (mut cash, mut marks, mut gross, mut open) = (cash0, 0 as Paise, 0 as Paise, 0u32);
            for (j, s) in st.iter().enumerate() {
                if j != i {
                    cash += s.cash();
                    let m = s.mark_value();
                    marks += m;
                    gross += m.abs();
                    open += (s.position() != 0) as u32;
                }
            }
            st[i].set_account(cash, cash + marks, gross, open);
            st[i].step(srcs[i].as_mut())?;
        }
        let cash = cash0 + st.iter().map(|s| s.cash()).sum::<Paise>();
        out.ts.push(now);
        out.equity.push(cash + st.iter().map(|s| s.mark_value()).sum::<Paise>());
        out.peak_open = out.peak_open.max(st.iter().filter(|s| s.position() != 0).count() as u32);
    }
    out.cash = cash0 + st.iter().map(|s| s.cash()).sum::<Paise>();
    for (s, src) in st.into_iter().zip(srcs.iter_mut()) {
        out.legs.push(s.finish(src.as_mut()));
    }
    Ok(out)
}

/// The portfolio as one `Outcome` for `metrics::compute`: portfolio equity, every leg's trades (by exit time) and
/// charges, bars in market = timestamps with any position open is not tracked per timestamp - the sum of the legs'.
pub fn combined(p: &PortfolioOutcome) -> Outcome {
    let mut o = Outcome { equity: p.equity.clone(), cash: p.cash, ..Default::default() };
    for l in &p.legs {
        o.trades.extend(l.trades.iter().copied());
        o.charges.add(&l.charges);
        o.bars_in_market += l.bars_in_market;
        for (k, v) in l.rejects.iter().enumerate() {
            o.rejects[k] += v;
        }
    }
    o.trades.sort_by_key(|t| (t.exit_ts, t.entry_ts));
    o
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::decision::ArraySignals;

    fn cfg(sizing: &str) -> Config {
        Config::from_json(
            r#"{"product": "CNC", "fill_timing": "next_open"}"#,
            &format!(r#"{{"sizing": "{sizing}", "sizing_value": 0.5}}"#),
            r#"{"tick": 5, "lot": 1, "price_scale": 100}"#,
            r#"{"versions":[{"effective_day":0,"gst_e8":0}]}"#,
        )
        .unwrap()
    }

    struct Series {
        ts: Vec<i64>,
        p: Vec<i64>,
        v: Vec<i64>,
        day: Vec<i64>,
        minute: Vec<i32>,
    }

    fn series(n: usize, px: i64) -> Series {
        let t0 = 1_704_067_200; // 2024-01-01
        Series {
            ts: (0..n as i64).map(|i| t0 + i * 86_400).collect(),
            p: vec![px; n],
            v: vec![1_000_000; n],
            day: (0..n as i64).map(|i| 19_723 + i).collect(),
            minute: vec![555; n],
        }
    }

    fn bars(s: &Series) -> Bars<'_> {
        Bars {
            ts: &s.ts,
            open: &s.p,
            high: &s.p,
            low: &s.p,
            close: &s.p,
            volume: &s.v,
            day: &s.day,
            minute: &s.minute,
            bar_secs: 86_400,
        }
    }

    #[test]
    fn one_leg_equals_the_single_engine_and_legs_share_the_cash() {
        let (a, b) = (series(6, 10_000), series(6, 20_000));
        let (ba, bb) = (bars(&a), bars(&b));
        let le = [1u8, 0, 0, 0, 0, 0];
        let lx = [0u8, 0, 0, 1, 0, 0];
        let z = [0u8; 6];
        let c = cfg("all_in");
        // one leg == the single-instrument engine
        let mut s1 = ArraySignals::new(&le, &lx, &z, &z, &c.plan);
        let single = c.run(1_000_000_00, &ba, None, &[], &mut s1).unwrap();
        let cfgs = vec![cfg("all_in")];
        let mut srcs: Vec<Box<dyn DecisionSource>> = vec![Box::new(ArraySignals::new(&le, &lx, &z, &z, &cfgs[0].plan))];
        let legs = [Leg { bars: &ba, m1: None, events: &[], changes: &[] }];
        let p = run_portfolio(&cfgs, &legs, &mut srcs, 1_000_000_00, None).unwrap();
        assert_eq!(p.equity, single.equity);
        assert_eq!(p.legs[0].fills.len(), single.fills.len());
        assert_eq!(p.legs[0].fills[0].qty, single.fills[0].qty);
        // two legs, all_in: the first leg takes the cash, the second cannot buy (one account)
        let cfgs = vec![cfg("all_in"), cfg("all_in")];
        let mut srcs: Vec<Box<dyn DecisionSource>> = vec![
            Box::new(ArraySignals::new(&le, &lx, &z, &z, &cfgs[0].plan)),
            Box::new(ArraySignals::new(&le, &lx, &z, &z, &cfgs[1].plan)),
        ];
        let legs = [
            Leg { bars: &ba, m1: None, events: &[], changes: &[] },
            Leg { bars: &bb, m1: None, events: &[], changes: &[] },
        ];
        let p = run_portfolio(&cfgs, &legs, &mut srcs, 1_000_000_00, None).unwrap();
        assert_eq!(p.legs[0].fills[0].qty, 10_000);
        assert!(p.legs[1].fills.is_empty() && p.legs[1].rejects[0] >= 1); // insufficient_funds
        assert_eq!(p.cash, 1_000_000_00); // flat price, zero costs
        // pct_equity 50 %: both legs get half of the PORTFOLIO equity
        let cfgs = vec![cfg("pct_equity"), cfg("pct_equity")];
        let mut srcs: Vec<Box<dyn DecisionSource>> = vec![
            Box::new(ArraySignals::new(&le, &lx, &z, &z, &cfgs[0].plan)),
            Box::new(ArraySignals::new(&le, &lx, &z, &z, &cfgs[1].plan)),
        ];
        let p = run_portfolio(&cfgs, &legs, &mut srcs, 1_000_000_00, None).unwrap();
        assert_eq!((p.legs[0].fills[0].qty, p.legs[1].fills[0].qty), (5_000, 2_500));
        assert_eq!(p.peak_open, 2);
        // max_positions 1: the second leg is refused
        let mut srcs: Vec<Box<dyn DecisionSource>> = vec![
            Box::new(ArraySignals::new(&le, &lx, &z, &z, &cfgs[0].plan)),
            Box::new(ArraySignals::new(&le, &lx, &z, &z, &cfgs[1].plan)),
        ];
        let p = run_portfolio(&cfgs, &legs, &mut srcs, 1_000_000_00, Some(1)).unwrap();
        assert!(p.legs[1].fills.is_empty() && p.legs[1].rejects[9] >= 1 && p.peak_open == 1);
        let comb = combined(&p);
        assert_eq!(comb.trades.len(), 1);
    }
}
