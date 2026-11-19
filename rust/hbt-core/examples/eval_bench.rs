//! Evaluator micro-benchmark (design NFR-1): cargo run --release -p hbt-core --example eval_bench
use std::time::Instant;

use hbt_core::eval::{Frame, MarketEval, Sessions, SigData};

fn main() {
    let n = 39_150usize;
    let mut x = 1000.0f64;
    let mut seed = 42u64;
    let close: Vec<f64> = (0..n)
        .map(|_| {
            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            x *= 1.0 + (((seed >> 33) as f64 / (1u64 << 31) as f64) - 0.5) * 0.004;
            (x * 20.0).round() / 20.0
        })
        .collect();
    let ts: Vec<i64> = (0..n as i64).map(|i| 1_704_079_800 + (i / 75) * 86_400 + (i % 75) * 300).collect();
    let avail: Vec<i64> = ts.iter().map(|t| t + 360).collect();
    let day: Vec<i64> = ts.iter().map(|t| (t + 19_800) / 86_400).collect();
    let minute: Vec<i32> = ts.iter().map(|t| (((t + 19_800) % 86_400) / 60) as i32).collect();
    let vol = vec![1e6; n];
    let factor = vec![1.0; n];
    let mut sigs = String::new();
    for k in 0..8 {
        sigs.push_str(&format!(r#""e{k}": {{"fn": "ema", "args": [{{"col": "close"}}, {}]}},"#, 5 + 7 * k));
    }
    for k in 0..4 {
        sigs.push_str(&format!(r#""s{k}": {{"fn": "sma", "args": [{{"col": "close"}}, {}]}},"#, 10 + 20 * k));
        sigs.push_str(&format!(r#""r{k}": {{"fn": "rsi", "args": [{{"col": "close"}}, {}]}},"#, 7 + 7 * k));
    }
    for k in 0..7 {
        sigs.push_str(&format!(
            r#""d{k}": {{"fn": "gt", "args": [{{"fn": "sub", "args": [{{"sig": "e{k}"}}, {{"sig": "e{}"}}]}}, 0]}},"#,
            k + 1
        ));
    }
    sigs.pop();
    let doc = format!(
        r#"{{"ir_version": 1, "name": "b", "signals": {{{sigs}}}, "rules": [{{"when": false, "do": {{"exit_all": {{}}}}}}]}}"#
    );
    let prog = hbt_ir::compile(&doc).unwrap();
    let plan = hbt_ir::build_plan(&prog, &[Default::default()], "5m", &[]).unwrap();
    let nodes = plan.nodes.iter().filter(|n| n.frame != u8::MAX).count();
    let data = SigData {
        base: Frame { ts: &ts, open: &close, high: &close, low: &close, close: &close, volume: &vol, avail: &avail },
        day: &day,
        minute: &minute,
        bar_secs: 300,
        frames: vec![],
        extras: vec![],
        factor: &factor,
        sessions: Sessions::default(),
        price_scale: 100,
        trade_start: 0,
    };
    let mut best = f64::MAX;
    let mut acc = 0.0;
    for _ in 0..10 {
        let t0 = Instant::now();
        let mut m = MarketEval::new(&plan, &data).unwrap();
        for t in 0..n {
            m.advance(t).unwrap();
            acc += m.slots[1];
        }
        best = best.min(t0.elapsed().as_secs_f64());
    }
    println!(
        "{nodes} nodes x {n} bars: {:.2} ms -> {:.1} M node-updates/s ({acc:.0})",
        best * 1e3,
        (nodes * n) as f64 / best / 1e6
    );
    // full IR run (engine + decision source) vs the array source
    let ema = r#"{"ir_version": 1, "name": "ema", "signals": {"f": {"fn": "ema", "args": [{"col": "close"}, 9]},
        "s": {"fn": "ema", "args": [{"col": "close"}, 21]}}, "rules": [
        {"when": {"fn": "cross_below", "args": [{"sig": "f"}, {"sig": "s"}]}, "do": {"exit_long": {}}},
        {"when": {"fn": "cross_above", "args": [{"sig": "f"}, {"sig": "s"}]}, "do": {"enter_long": {}}}]}"#;
    let p2 = hbt_ir::build_plan(&hbt_ir::compile(ema).unwrap(), &[Default::default()], "5m", &[]).unwrap();
    let ci: Vec<i64> = close.iter().map(|c| (c * 100.0).round() as i64).collect();
    let hi: Vec<i64> = ci.iter().map(|c| c + 10).collect();
    let lo: Vec<i64> = ci.iter().map(|c| c - 10).collect();
    let vi = vec![1_000_000i64; n];
    let bars = hbt_core::engine::Bars {
        ts: &ts,
        open: &ci,
        high: &hi,
        low: &lo,
        close: &ci,
        volume: &vi,
        day: &day,
        minute: &minute,
        bar_secs: 300,
    };
    let cfg = hbt_core::Config::from_json(
        r#"{"product":"MIS","squareoff_at":915,"participation_cap":0.1}"#,
        "{}",
        r#"{"tick":1}"#,
        r#"{"versions":[{"effective_day":0,"exch_e8":2970,"stt_sell_e8":25000}]}"#,
    )
    .unwrap();
    let time = |f: &mut dyn FnMut() -> usize| {
        let mut b = f64::MAX;
        let mut r = 0;
        for _ in 0..10 {
            let t0 = Instant::now();
            r = f();
            b = b.min(t0.elapsed().as_secs_f64());
        }
        (b * 1e3, r)
    };
    let (ms, fills) = time(&mut || {
        let mut src = hbt_core::eval::IrSource::live(&p2, 0, &data).unwrap();
        cfg.run(100_000_000, &bars, None, &[], &mut src).unwrap().fills.len()
    });
    println!("IR live run: {ms:.2} ms ({fills} fills)");
    let (ms, _) = time(&mut || {
        let mut m = MarketEval::new(&p2, &data).unwrap();
        for t in 0..n {
            m.advance(t).unwrap();
        }
        0
    });
    println!("  of which market eval: {ms:.2} ms");
    let z = vec![0u8; n];
    let (ms, _) = time(&mut || {
        let mut src = hbt_core::decision::ArraySignals::new(&z, &z, &z, &z, &cfg.plan);
        cfg.run(100_000_000, &bars, None, &[], &mut src).unwrap().fills.len()
    });
    println!("engine with empty array signals: {ms:.2} ms");
}
