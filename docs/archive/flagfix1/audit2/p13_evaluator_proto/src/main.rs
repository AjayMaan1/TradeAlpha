// Feasibility prototype for the Phase 1.3 evaluator: a compiled DAG of streaming kernels stepped once per bar
// (batch = the same step() run over the whole array, so batch == streaming by construction).
use std::time::Instant;

#[derive(Clone)]
enum Op {
    Close,
    Ema { src: usize, alpha: f64, n: usize },
    Sma { src: usize, n: usize },
    Sub(usize, usize),
    Gt(usize, usize),
    CrossAbove(usize, usize),
    And(usize, usize),
    Rsi { src: usize, n: usize },
}

struct State {
    ema: f64, cnt: usize, sum: f64, ring: Vec<f64>, pos: usize,
    prev_a: f64, prev_b: f64, prev_src: f64, up: f64, dn: f64,
}

fn new_state(op: &Op) -> State {
    let n = match op { Op::Sma { n, .. } => *n, _ => 0 };
    State { ema: f64::NAN, cnt: 0, sum: 0.0, ring: vec![0.0; n.max(1)], pos: 0, prev_a: f64::NAN, prev_b: f64::NAN,
            prev_src: f64::NAN, up: 0.0, dn: 0.0 }
}

struct Program { ops: Vec<Op>, st: Vec<State>, val: Vec<f64> }

impl Program {
    fn new(ops: Vec<Op>) -> Self {
        let st = ops.iter().map(new_state).collect();
        let n = ops.len();
        Program { ops, st, val: vec![f64::NAN; n] }
    }
    #[inline]
    fn step(&mut self, close: f64) {
        for i in 0..self.ops.len() {
            let v = match self.ops[i] {
                Op::Close => close,
                Op::Ema { src, alpha, n } => {
                    let x = self.val[src]; let s = &mut self.st[i];
                    if x.is_nan() { f64::NAN } else if s.cnt < n { // TA-Lib seeding: SMA of the first n values
                        s.sum += x; s.cnt += 1;
                        if s.cnt == n { s.ema = s.sum / n as f64; s.ema } else { f64::NAN }
                    } else { s.ema += alpha * (x - s.ema); s.ema }
                }
                Op::Sma { src, n } => {
                    let x = self.val[src]; let s = &mut self.st[i];
                    s.sum += x - s.ring[s.pos]; s.ring[s.pos] = x; s.pos = (s.pos + 1) % n; s.cnt += 1;
                    if s.cnt >= n { s.sum / n as f64 } else { f64::NAN }
                }
                Op::Sub(a, b) => self.val[a] - self.val[b],
                Op::Gt(a, b) => if self.val[a] > self.val[b] { 1.0 } else { 0.0 },
                Op::CrossAbove(a, b) => {
                    let (x, y) = (self.val[a], self.val[b]); let s = &mut self.st[i];
                    let r = s.prev_a <= s.prev_b && x > y; s.prev_a = x; s.prev_b = y;
                    if r { 1.0 } else { 0.0 }
                }
                Op::And(a, b) => if self.val[a] != 0.0 && self.val[b] != 0.0 { 1.0 } else { 0.0 },
                Op::Rsi { src, n } => {
                    let x = self.val[src]; let s = &mut self.st[i];
                    if s.prev_src.is_nan() { s.prev_src = x; f64::NAN } else {
                        let d = x - s.prev_src; s.prev_src = x; s.cnt += 1;
                        let (u, dd) = (d.max(0.0), (-d).max(0.0));
                        if s.cnt <= n { s.up += u; s.dn += dd; if s.cnt < n { return_nan() } else { s.up /= n as f64; s.dn /= n as f64; 100.0 - 100.0 / (1.0 + s.up / s.dn) } }
                        else { s.up = (s.up * (n - 1) as f64 + u) / n as f64; s.dn = (s.dn * (n - 1) as f64 + dd) / n as f64; 100.0 - 100.0 / (1.0 + s.up / s.dn) }
                    }
                }
            };
            self.val[i] = v;
        }
    }
}
#[inline] fn return_nan() -> f64 { f64::NAN }

fn ema_cross(fast: usize, slow: usize) -> Vec<Op> {
    vec![Op::Close, Op::Ema { src: 0, alpha: 2.0 / (fast as f64 + 1.0), n: fast },
         Op::Ema { src: 0, alpha: 2.0 / (slow as f64 + 1.0), n: slow }, Op::CrossAbove(1, 2), Op::CrossAbove(2, 1)]
}

fn big_program() -> Vec<Op> {
    // ~40 nodes: 8 EMAs, 4 SMAs, 4 RSIs, spreads, comparisons, crosses, conjunctions
    let mut v = vec![Op::Close];
    for k in 0..8 { let n = 5 + 7 * k; v.push(Op::Ema { src: 0, alpha: 2.0 / (n as f64 + 1.0), n }); }
    for k in 0..4 { v.push(Op::Sma { src: 0, n: 10 + 20 * k }); }
    for k in 0..4 { v.push(Op::Rsi { src: 0, n: 7 + 7 * k }); }
    let base = v.len();
    for k in 0..7 { v.push(Op::Sub(1 + k, 2 + k)); }
    for k in 0..7 { v.push(Op::Gt(base + k, 9)); }
    for k in 0..4 { v.push(Op::CrossAbove(1 + k, 9 + k)); }
    let c = v.len();
    for k in 0..6 { v.push(Op::And(c - 11 + k, c - 4 + (k % 4))); }
    v
}

fn main() {
    let n = 39_150usize;
    let mut x = 1000.0f64; let mut seed = 42u64;
    let close: Vec<f64> = (0..n).map(|_| { seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        x *= 1.0 + (((seed >> 33) as f64 / (1u64 << 31) as f64) - 0.5) * 0.004; (x * 20.0).round() / 20.0 }).collect();
    for (name, ops) in [("ema_cross (5 nodes)", ema_cross(9, 21)), ("mixed (40 nodes)", big_program())] {
        let nodes = ops.len();
        let mut best = f64::MAX; let mut fired = 0.0;
        for _ in 0..20 {
            let mut p = Program::new(ops.clone());
            let t = Instant::now();
            let mut acc = 0.0;
            for &c in &close { p.step(c); acc += p.val[nodes - 1]; }
            best = best.min(t.elapsed().as_secs_f64()); fired = acc;
        }
        println!("{name}: {n} bars in {:.3} ms -> {:.1} M node-updates/s (signals fired {fired})",
                 best * 1e3, (n * nodes) as f64 / best / 1e6);
    }
    // sweep with CSE: 16 fast x 16 slow EMA-cross variants; unique EMA nodes 32 instead of 512
    let fasts: Vec<usize> = (3..35).step_by(2).collect(); let slows: Vec<usize> = (10..170).step_by(10).collect();
    let t = Instant::now();
    let mut tot = 0.0;
    for &f in &fasts { for &s in &slows { let mut p = Program::new(ema_cross(f, s)); for &c in &close { p.step(c); } tot += p.val[4]; } }
    let naive = t.elapsed().as_secs_f64();
    let t = Instant::now();
    let mut ops = vec![Op::Close];
    let mut idx = std::collections::HashMap::new();
    for &p in fasts.iter().chain(slows.iter()) { if !idx.contains_key(&p) { idx.insert(p, ops.len()); ops.push(Op::Ema { src: 0, alpha: 2.0 / (p as f64 + 1.0), n: p }); } }
    for &f in &fasts { for &s in &slows { ops.push(Op::CrossAbove(idx[&f], idx[&s])); } }
    let m = ops.len();
    let mut p = Program::new(ops);
    for &c in &close { p.step(c); }
    let cse = t.elapsed().as_secs_f64();
    println!("sweep 256 variants (signals only): naive {:.1} ms, shared-EMA program ({m} nodes) {:.1} ms -> x{:.1} ({tot})",
             naive * 1e3, cse * 1e3, naive / cse);
}
