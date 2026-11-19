//! Fixed-point money (ADR-3). Prices are integers in "price units" (1/price_scale rupee; 100 = paise for equities),
//! cash and charges are i64 paise. Rates are integers scaled by 1e8 ("e8": 0.1 % = 100_000). All roundings are
//! explicit and half-up, like broker contract notes.

pub type Paise = i64;
pub const E8: i128 = 100_000_000;

/// round(num / den) half-up for non-negative results, symmetric for negatives.
#[inline]
pub fn div_round_half_up(num: i128, den: i128) -> i128 {
    debug_assert!(den > 0);
    if num >= 0 { (num + den / 2) / den } else { -((-num + den / 2) / den) }
}

/// value (paise) x rate (e8) -> paise, rounded half-up.
#[inline]
pub fn apply_rate(value: Paise, rate_e8: i64) -> Paise {
    div_round_half_up(value as i128 * rate_e8 as i128, E8) as Paise
}

/// Notional in paise of `qty` at `price` (price units) for an instrument quoted at 1/price_scale rupee.
#[inline]
pub fn notional(qty: i64, price: i64, price_scale: i64) -> Paise {
    let raw = qty as i128 * price as i128 * 100;
    div_round_half_up(raw, price_scale as i128) as Paise
}

/// Round a price to the tick grid. `up` rounds towards +inf (buys: adverse), otherwise towards -inf (sells).
#[inline]
pub fn round_to_tick(price: i64, tick: i64, up: bool) -> i64 {
    if tick <= 1 {
        return price;
    }
    let r = price.rem_euclid(tick);
    if r == 0 {
        price
    } else if up {
        price - r + tick
    } else {
        price - r
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rounding_is_half_up_and_symmetric() {
        assert_eq!(div_round_half_up(5, 10), 1);
        assert_eq!(div_round_half_up(4, 10), 0);
        assert_eq!(div_round_half_up(-5, 10), -1);
        assert_eq!(apply_rate(200_000_00, 25_000), 5_000); // 0.025 % STT on Rs 2 lakh = Rs 50.00
        assert_eq!(apply_rate(1, 50_000_000), 1); // 0.5 paise -> 1 paisa (half-up)
    }
    #[test]
    fn notional_and_ticks() {
        assert_eq!(notional(10, 150_05, 100), 15_005_0); // 10 x Rs 150.05 = Rs 1,500.50
        assert_eq!(notional(1000, 83_2525, 10_000), 8_325_250); // CDS 4-decimal price: Rs 83,252.50
        assert_eq!(round_to_tick(150_03, 5, true), 150_05);
        assert_eq!(round_to_tick(150_03, 5, false), 150_00);
        assert_eq!(round_to_tick(-7, 5, false), -10);
        assert_eq!(round_to_tick(150_05, 5, true), 150_05);
    }
}
