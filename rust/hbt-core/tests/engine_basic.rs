//! Engine smoke tests at the Rust level (the full golden / parity / property suites run through Python).
use hbt_core::engine::Bars;

type Cols = (Vec<i64>, Vec<i64>, Vec<i64>, Vec<i64>, Vec<i64>, Vec<i64>, Vec<i64>, Vec<i32>);

fn bars(closes: &[i64]) -> Cols {
    let n = closes.len();
    let ts: Vec<i64> = (0..n as i64).map(|i| 1_743_047_100 + i * 300).collect(); // 2025-03-27 09:15 IST
    let o = closes.to_vec();
    let h: Vec<i64> = closes.iter().map(|c| c + 50).collect();
    let l: Vec<i64> = closes.iter().map(|c| c - 50).collect();
    let day: Vec<i64> = ts.iter().map(|t| (t + 19_800) / 86_400).collect();
    let minute: Vec<i32> = ts.iter().map(|t| (((t + 19_800) % 86_400) / 60) as i32).collect();
    (ts, o, h, l, closes.to_vec(), vec![1_000_000; n], day, minute)
}

const SPEC: &str = r#"{"product":"MIS"}"#;
const ZERO: &str = r#"{"versions":[{"effective_day":0,"gst_e8":0}]}"#;

fn run(closes: &[i64], le: &[u8], lx: &[u8], plan: &str) -> hbt_core::engine::Outcome {
    let (ts, o, h, l, c, v, d, m) = bars(closes);
    let b = Bars { ts: &ts, open: &o, high: &h, low: &l, close: &c, volume: &v, day: &d, minute: &m, bar_secs: 300 };
    let z = vec![0u8; closes.len()];
    hbt_core::run_json(SPEC, plan, "{}", ZERO, 1_000_000, &b, None, [le, lx, &z, &z]).unwrap()
}

#[test]
fn next_open_round_trip_in_paise() {
    let out = run(&[10_000, 10_200, 10_300, 10_300], &[1, 0, 0, 0], &[0, 1, 0, 0], "{}");
    assert_eq!(out.fills.len(), 2);
    assert_eq!((out.fills[0].qty, out.fills[0].price, out.fills[1].price), (98, 10_200, 10_300));
    assert_eq!(out.cash, 1_000_000 + 98 * 100);
    assert_eq!(out.trades[0].pnl, 98 * 100);
}

#[test]
fn stop_is_live_on_the_entry_bar_and_results_are_deterministic() {
    let closes = [10_000, 10_200, 10_100, 10_100];
    let plan = r#"{"stop_pct":0.004}"#; // stop 10200 x 0.996 = 10159.2 -> 10159; entry-bar low 10150 hits it
    let a = run(&closes, &[1, 0, 0, 0], &[0; 4], plan);
    let b = run(&closes, &[1, 0, 0, 0], &[0; 4], plan);
    assert_eq!(a.fills[1].price, 10_159);
    assert_eq!(a.fills[1].bar, 1);
    assert_eq!(a.equity, b.equity);
    assert_eq!(a.cash, b.cash);
}

#[test]
fn bonus_on_the_ex_date_doubles_the_position_and_keeps_equity_continuous() {
    use hbt_core::decision::ArraySignals;
    use hbt_core::engine::CaEvent;
    // daily bars: 1000 -> 500 after a 1:1 bonus on day 3
    let closes = [100_000i64, 100_000, 100_000, 50_000, 50_000];
    let n = closes.len();
    let ts: Vec<i64> = (0..n as i64).map(|i| 1_735_669_800 + i * 86_400).collect(); // 00:00 IST
    let h: Vec<i64> = closes.iter().map(|c| c + 100).collect();
    let l: Vec<i64> = closes.iter().map(|c| c - 100).collect();
    let day: Vec<i64> = ts.iter().map(|t| (t + 19_800) / 86_400).collect();
    let minute = vec![0i32; n];
    let vol = vec![1_000_000i64; n];
    let b = Bars {
        ts: &ts,
        open: &closes,
        high: &h,
        low: &l,
        close: &closes,
        volume: &vol,
        day: &day,
        minute: &minute,
        bar_secs: 86_400,
    };
    let cfg =
        hbt_core::Config::from_json(r#"{"product":"CNC"}"#, r#"{"sizing":"fixed_qty","sizing_value":10}"#, "{}", ZERO)
            .unwrap();
    let (le, z) = ([1u8, 0, 0, 0, 0], [0u8; 5]);
    let mut src = ArraySignals::new(&le, &z, &z, &z, &cfg.plan);
    let ev = [CaEvent { day: day[3], qty_num: 2, qty_den: 1, cash_paise: 0 }];
    let out = cfg.run(100_000_000, &b, None, &ev, &mut src).unwrap();
    assert_eq!(out.position, 20);
    assert_eq!(out.equity[2], out.equity[3]); // 10 x 1000 == 20 x 500
}
