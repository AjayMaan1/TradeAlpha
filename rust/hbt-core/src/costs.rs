//! Indian transaction costs by trade date (design §12). Each charge is computed on the fill value and rounded to the
//! paisa separately (contract-note style); GST applies to brokerage + exchange transaction charge + SEBI fee.

use serde::Deserialize;

use crate::money::{Paise, apply_rate};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Buy,
    Sell,
}

/// Statutory / exchange rates in force from `effective_day` (days since 1970-01-01, IST trading date).
#[derive(Debug, Clone, Deserialize)]
pub struct RateVersion {
    pub effective_day: i64,
    #[serde(default)]
    pub stt_buy_e8: i64,
    #[serde(default)]
    pub stt_sell_e8: i64,
    #[serde(default)]
    pub stamp_buy_e8: i64,
    #[serde(default)]
    pub exch_e8: i64,
    #[serde(default)]
    pub sebi_e8: i64,
    #[serde(default = "default_gst")]
    pub gst_e8: i64,
}

fn default_gst() -> i64 {
    18_000_000
}

/// Broker tariff for one segment: flat per order, or pct of value with an optional cap; optional minimum.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Brokerage {
    #[serde(default)]
    pub pct_e8: i64,
    #[serde(default)]
    pub cap_paise: Option<Paise>,
    #[serde(default)]
    pub flat_paise: Option<Paise>,
    #[serde(default)]
    pub min_paise: Option<Paise>,
}

impl Brokerage {
    pub fn charge(&self, value: Paise) -> Paise {
        let mut b = match self.flat_paise {
            Some(f) if self.pct_e8 > 0 => f.min(apply_rate(value, self.pct_e8)),
            Some(f) => f,
            None => {
                let p = apply_rate(value, self.pct_e8);
                self.cap_paise.map_or(p, |c| p.min(c))
            }
        };
        if let Some(m) = self.min_paise
            && b > 0
        {
            b = b.max(m);
        }
        b
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct CostModel {
    pub versions: Vec<RateVersion>,
    #[serde(default)]
    pub brokerage: Brokerage,
    /// depository (DP) charge per scrip per day on delivery sells, paise before GST (audit 2 A2-M7)
    #[serde(default)]
    pub dp_paise: Paise,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Charges {
    pub brokerage: Paise,
    pub stt: Paise,
    pub exch: Paise,
    pub sebi: Paise,
    pub stamp: Paise,
    pub gst: Paise,
    pub dp: Paise,
}

impl Charges {
    pub fn total(&self) -> Paise {
        self.brokerage + self.stt + self.exch + self.sebi + self.stamp + self.gst + self.dp
    }
    pub fn add(&mut self, o: &Charges) {
        self.brokerage += o.brokerage;
        self.stt += o.stt;
        self.exch += o.exch;
        self.sebi += o.sebi;
        self.stamp += o.stamp;
        self.gst += o.gst;
        self.dp += o.dp;
    }
}

impl CostModel {
    pub fn zero() -> Self {
        CostModel {
            versions: vec![RateVersion {
                effective_day: i64::MIN,
                stt_buy_e8: 0,
                stt_sell_e8: 0,
                stamp_buy_e8: 0,
                exch_e8: 0,
                sebi_e8: 0,
                gst_e8: 0,
            }],
            brokerage: Brokerage::default(),
            dp_paise: 0,
        }
    }

    pub fn validate(&mut self) -> Result<(), String> {
        if self.versions.is_empty() {
            return Err("cost model needs at least one rate version".into());
        }
        self.versions.sort_by_key(|v| v.effective_day);
        Ok(())
    }

    fn version(&self, day: i64) -> &RateVersion {
        let i = self.versions.partition_point(|v| v.effective_day <= day);
        &self.versions[i.saturating_sub(1)]
    }

    pub fn charges(&self, day: i64, side: Side, value: Paise) -> Charges {
        let v = self.version(day);
        let brokerage = self.brokerage.charge(value);
        let stt = apply_rate(value, if side == Side::Buy { v.stt_buy_e8 } else { v.stt_sell_e8 });
        let stamp = if side == Side::Buy { apply_rate(value, v.stamp_buy_e8) } else { 0 };
        let exch = apply_rate(value, v.exch_e8);
        let sebi = apply_rate(value, v.sebi_e8);
        let gst = apply_rate(brokerage + exch + sebi, v.gst_e8);
        Charges { brokerage, stt, exch, sebi, stamp, gst, dp: 0 }
    }

    /// DP charge of one delivery sell day: (dp, GST on dp)
    pub fn dp_charge(&self, day: i64) -> (Paise, Paise) {
        if self.dp_paise <= 0 {
            return (0, 0);
        }
        (self.dp_paise, apply_rate(self.dp_paise, self.version(day).gst_e8))
    }

    /// Upper-bound cost rate (e8) of a buy at `value`, for sizing ("all-in" orders must leave room for charges).
    pub fn buy_rate_bound_e8(&self, day: i64) -> i64 {
        // flat brokerage is not a rate: Engine::affordable() trims the quantity until value + charges fit
        let v = self.version(day);
        v.stt_buy_e8
            + v.stamp_buy_e8
            + (v.exch_e8 + v.sebi_e8 + self.brokerage.pct_e8) * (100_000_000 + v.gst_e8) / 100_000_000
            + 2
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn nse_intraday() -> CostModel {
        let mut m = CostModel {
            versions: vec![RateVersion {
                effective_day: 20_000,
                stt_buy_e8: 0,
                stt_sell_e8: 25_000,
                stamp_buy_e8: 3_000,
                exch_e8: 2_970,
                sebi_e8: 100,
                gst_e8: 18_000_000,
            }],
            brokerage: Brokerage { pct_e8: 30_000, cap_paise: Some(2_000), flat_paise: None, min_paise: None },
            dp_paise: 0,
        };
        m.validate().unwrap();
        m
    }

    #[test]
    fn contract_note_for_rs_2_lakh_intraday_round_trip() {
        // Rs 2,00,000 each side: brokerage min(0.03 %, Rs 20) = Rs 20; STT 0.025 % on the sell = Rs 50;
        // stamp 0.003 % on the buy = Rs 6; exchange 0.00297 % = Rs 5.94; SEBI Rs 10/crore = Rs 0.20;
        // GST 18 % x (20 + 5.94 + 0.20) = Rs 4.7052 -> Rs 4.71
        let m = nse_intraday();
        let buy = m.charges(20_100, Side::Buy, 200_000_00);
        let sell = m.charges(20_100, Side::Sell, 200_000_00);
        assert_eq!(buy, Charges { brokerage: 2_000, stt: 0, exch: 594, sebi: 20, stamp: 600, gst: 471, dp: 0 });
        assert_eq!(sell, Charges { brokerage: 2_000, stt: 5_000, exch: 594, sebi: 20, stamp: 0, gst: 471, dp: 0 });
        assert_eq!(buy.total() + sell.total(), 3_685 + 8_085);
    }

    #[test]
    fn versions_are_chosen_by_trade_date_and_brokerage_rules() {
        let mut m = nse_intraday();
        m.versions.push(RateVersion {
            effective_day: 20_500,
            stt_buy_e8: 0,
            stt_sell_e8: 50_000,
            stamp_buy_e8: 0,
            exch_e8: 0,
            sebi_e8: 0,
            gst_e8: 0,
        });
        m.validate().unwrap();
        assert_eq!(m.charges(20_499, Side::Sell, 100_000_00).stt, 2_500);
        assert_eq!(m.charges(20_500, Side::Sell, 100_000_00).stt, 5_000);
        assert_eq!(m.charges(19_000, Side::Sell, 100_000_00).stt, 2_500); // before the first: earliest version
        let flat = Brokerage { pct_e8: 0, cap_paise: None, flat_paise: Some(2_000), min_paise: None };
        assert_eq!(flat.charge(1_00), 2_000);
        let flat_or_pct = Brokerage { pct_e8: 100_000, cap_paise: None, flat_paise: Some(2_000), min_paise: None };
        assert_eq!(flat_or_pct.charge(5_000_00), 500); // 0.1 % of Rs 5,000 = Rs 5 < Rs 20
        let with_min = Brokerage { pct_e8: 100_000, cap_paise: Some(2_000), flat_paise: None, min_paise: Some(500) };
        assert_eq!(with_min.charge(1_000_00), 500); // 0.1 % of Rs 1,000 = Rs 1 -> minimum Rs 5
    }

    #[test]
    fn dp_charge_per_sell_day_with_gst() {
        let mut m = nse_intraday();
        assert_eq!(m.dp_charge(20_100), (0, 0));
        m.dp_paise = 13_50; // Rs 13.50 + 18 % GST = Rs 2.43
        assert_eq!(m.dp_charge(20_100), (13_50, 2_43));
    }
}
