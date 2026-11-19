//! hbt-core (evaluation build): the prototype's execution kernel ported to Rust.
//! Same semantics as backtest_evaluation/prototype/hbt/kernel.py::simulate (see that docstring).
use numpy::{PyArray2, PyReadonlyArray1, PyReadonlyArray2, ToPyArray};
use numpy::ndarray::{Array2, ArrayView2};
use pyo3::prelude::*;
use rayon::prelude::*;

pub const N_RESULT: usize = 10;

#[inline(always)]
fn fee(value: f64, pct: f64, brok_pct: f64, brok_cap: f64) -> f64 {
    let mut b = brok_pct * value;
    if brok_cap > 0.0 && b > brok_cap { b = brok_cap; }
    value * pct + b
}

#[allow(clippy::too_many_arguments)]
fn simulate_core(o: &[f64], h: &[f64], l: &[f64], c: &[f64], entry: &[bool], exit: &[bool], sq: &[bool],
                 rates: &[f64], stop_pct: f64, target_pct: f64, cash0: f64, lot: f64) -> [f64; N_RESULT] {
    let n = c.len();
    let (o, h, l, entry, exit, sq) = (&o[..n], &h[..n], &l[..n], &entry[..n], &exit[..n], &sq[..n]);
    let (mut cash, mut qty, mut stop, mut target, mut pending) = (cash0, 0.0f64, 0.0f64, 0.0f64, 0i8);
    let (mut ntr, mut nst, mut ntg, mut nsq) = (0u32, 0u32, 0u32, 0u32);
    let (mut fees, mut prev_eq, mut peak, mut maxdd) = (0.0f64, cash0, cash0, 0.0f64);
    let (mut rn, mut rmean, mut rm2) = (0u64, 0.0f64, 0.0f64);
    for t in 0..n {
        let rr = &rates[t * 4..t * 4 + 4];
        let (bp, sp, bk, cap) = (rr[0], rr[1], rr[2], rr[3]);
        if qty > 0.0 && sq[t] {
            let f = fee(qty * o[t], sp, bk, cap); cash += qty * o[t] - f; fees += f; qty = 0.0; nsq += 1; pending = 0;
        }
        if pending == -1 && qty > 0.0 {
            let f = fee(qty * o[t], sp, bk, cap); cash += qty * o[t] - f; fees += f; qty = 0.0;
        }
        let mut entered_now = false;
        if pending == 1 && qty == 0.0 && !sq[t] {
            let px = o[t];
            let mut q = (cash / (px * (1.0 + bp + bk)) / lot).floor() * lot;
            while q > 0.0 && q * px + fee(q * px, bp, bk, cap) > cash { q -= lot; }
            if q > 0.0 {
                let f = fee(q * px, bp, bk, cap); cash -= q * px + f; fees += f; qty = q; ntr += 1; entered_now = true;
                stop = if stop_pct > 0.0 { px * (1.0 - stop_pct) } else { 0.0 };
                target = if target_pct > 0.0 { px * (1.0 + target_pct) } else { 0.0 };
            }
        }
        pending = 0;
        if qty > 0.0 {
            let mut xpx = -1.0;
            if stop > 0.0 && !entered_now && o[t] <= stop { xpx = o[t]; nst += 1; }
            else if target > 0.0 && !entered_now && o[t] >= target { xpx = o[t]; ntg += 1; }
            else if stop > 0.0 && l[t] <= stop { xpx = stop; nst += 1; }
            else if target > 0.0 && h[t] >= target { xpx = target; ntg += 1; }
            if xpx > 0.0 { let f = fee(qty * xpx, sp, bk, cap); cash += qty * xpx - f; fees += f; qty = 0.0; }
        }
        if t + 1 < n { if entry[t] { pending = 1; } else if exit[t] { pending = -1; } }
        let eq = cash + qty * c[t];
        let r = eq / prev_eq - 1.0; prev_eq = eq;
        rn += 1; let d = r - rmean; rmean += d / rn as f64; rm2 += d * (r - rmean);
        if eq > peak { peak = eq; }
        let dd = 1.0 - eq / peak; if dd > maxdd { maxdd = dd; }
    }
    [cash + qty * c[n - 1], ntr as f64, nst as f64, ntg as f64, nsq as f64, rn as f64, rmean, rm2, maxdd, fees]
}

fn ema(x: &[f64], n: i64) -> Vec<f64> {
    let a = 2.0 / (n as f64 + 1.0);
    let mut out = Vec::with_capacity(x.len()); let mut prev = x[0]; out.push(prev);
    for &v in &x[1..] { prev = a * v + (1.0 - a) * prev; out.push(prev); }
    out
}

/// Single backtest (parity with the numba kernel).
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn simulate<'py>(py: Python<'py>, o: PyReadonlyArray1<f64>, h: PyReadonlyArray1<f64>, l: PyReadonlyArray1<f64>,
                 c: PyReadonlyArray1<f64>, entry: PyReadonlyArray1<bool>, exit: PyReadonlyArray1<bool>,
                 sq: PyReadonlyArray1<bool>, rates: PyReadonlyArray2<f64>, stop_pct: f64, target_pct: f64,
                 cash0: f64, lot: f64) -> PyResult<Vec<f64>> {
    let (o, h, l, c) = (o.as_slice()?, h.as_slice()?, l.as_slice()?, c.as_slice()?);
    let (e, x, s) = (entry.as_slice()?, exit.as_slice()?, sq.as_slice()?);
    let r = rates.as_slice()?;
    Ok(py.detach(|| simulate_core(o, h, l, c, e, x, s, r, stop_pct, target_pct, cash0, lot).to_vec()))
}

fn col(m: &ArrayView2<f64>, j: usize, lo: usize, hi: usize) -> Vec<f64> { (lo..hi).map(|t| m[[t, j]]).collect() }

/// EMA(fast) x EMA(slow) x stop grid for every symbol on bars [lo, hi), parallel over (symbol, fast, slow) with rayon.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn sweep_ema_cross<'py>(py: Python<'py>, o: PyReadonlyArray2<f64>, h: PyReadonlyArray2<f64>, l: PyReadonlyArray2<f64>,
                        c: PyReadonlyArray2<f64>, tod: PyReadonlyArray1<i64>, same_next: PyReadonlyArray1<bool>,
                        sq: PyReadonlyArray1<bool>, rates: PyReadonlyArray2<f64>, fasts: Vec<i64>, slows: Vec<i64>,
                        stops: Vec<f64>, lo: usize, hi: usize, no_entry_after: i64, warmup: usize, cash0: f64,
                        lot: f64) -> PyResult<Bound<'py, PyArray2<f64>>> {
    let (o, h, l, c) = (o.as_array(), h.as_array(), l.as_array(), c.as_array());
    let (tod, same_next, sq) = (tod.as_slice()?, same_next.as_slice()?, sq.as_slice()?);
    let rates = rates.as_slice()?;
    let (nsym, nf, ns, nk) = (c.ncols(), fasts.len(), slows.len(), stops.len());
    let rows: Vec<[f64; N_RESULT]> = py.detach(|| {
        (0..nsym * nf * ns).into_par_iter().flat_map_iter(|job| {
            let (sym, rest) = (job / (nf * ns), job % (nf * ns));
            let (i, j) = (rest / ns, rest % ns);
            let mut res = vec![[0.0f64; N_RESULT]; nk];
            if fasts[i] < slows[j] {
                let cc: Vec<f64> = (0..c.nrows()).map(|t| c[[t, sym]]).collect();
                let (ef, es) = (ema(&cc, fasts[i]), ema(&cc, slows[j]));
                let m = hi - lo;
                let (mut en, mut ex) = (vec![false; m], vec![false; m]);
                for t in lo.max(1).max(warmup)..hi {
                    let (a, p) = (ef[t] > es[t], ef[t - 1] > es[t - 1]);
                    if a && !p && tod[t] <= no_entry_after && same_next[t] { en[t - lo] = true; }
                    else if p && !a && same_next[t] { ex[t - lo] = true; }
                }
                let (oo, hh, ll, c2) = (col(&o, sym, lo, hi), col(&h, sym, lo, hi), col(&l, sym, lo, hi), col(&c, sym, lo, hi));
                let rv = &rates[lo * 4..hi * 4];
                for (k, &st) in stops.iter().enumerate() {
                    res[k] = simulate_core(&oo, &hh, &ll, &c2, &en, &ex, &sq[lo..hi], rv, st, 0.0, cash0, lot);
                }
            }
            res.into_iter()
        }).collect()
    });
    let mut out = Array2::<f64>::zeros((rows.len(), N_RESULT));
    for (r, v) in rows.iter().enumerate() { for k in 0..N_RESULT { out[[r, k]] = v[k]; } }
    Ok(out.to_pyarray(py))
}

#[pymodule]
fn hbt_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(simulate, m)?)?;
    m.add_function(wrap_pyfunction!(sweep_ema_cross, m)?)?;
    m.add_function(wrap_pyfunction!(drive_signals, m)?)?;
    m.add_function(wrap_pyfunction!(drive_cfunc, m)?)?;
    m.add_function(wrap_pyfunction!(drive_python, m)?)?;
    Ok(())
}

// ------------------------------------------------------------------------------------------------ strategy tiers
/// Minimal engine loop driven by a per-bar decision: +1 buy, -1 sell, 0 hold (signal at close t -> fill at open t+1).
fn drive<F: FnMut(usize) -> i8>(o: &[f64], c: &[f64], cash0: f64, fee: f64, mut decide: F) -> (f64, u32) {
    let (mut cash, mut qty, mut pend, mut n) = (cash0, 0.0f64, 0i8, 0u32);
    for t in 0..c.len() {
        if pend == 1 && qty == 0.0 { let q = (cash / (o[t] * (1.0 + fee))).floor(); if q > 0.0 { cash -= q * o[t] * (1.0 + fee); qty = q; n += 1; } }
        else if pend == -1 && qty > 0.0 { cash += qty * o[t] * (1.0 - fee); qty = 0.0; }
        pend = if t + 1 < c.len() { decide(t) } else { 0 };
    }
    (cash + qty * c[c.len() - 1], n)
}

/// Tier A: precomputed signal arrays (vectorised signals) - no callback.
#[pyfunction]
fn drive_signals(o: PyReadonlyArray1<f64>, c: PyReadonlyArray1<f64>, sig: PyReadonlyArray1<i8>, cash0: f64, fee: f64) -> PyResult<(f64, u32)> {
    let (o, c, s) = (o.as_slice()?, c.as_slice()?, sig.as_slice()?);
    Ok(drive(o, c, cash0, fee, |t| s[t]))
}

/// Tier B: user logic compiled to a C function pointer (numba @cfunc): i8 f(const double* close, i64 t)
#[pyfunction]
fn drive_cfunc(o: PyReadonlyArray1<f64>, c: PyReadonlyArray1<f64>, fn_addr: usize, cash0: f64, fee: f64) -> PyResult<(f64, u32)> {
    let (o, c) = (o.as_slice()?, c.as_slice()?);
    let f: extern "C" fn(*const f64, i64) -> i8 = unsafe { std::mem::transmute(fn_addr) };
    let p = c.as_ptr();
    Ok(drive(o, c, cash0, fee, |t| f(p, t as i64)))
}

/// Tier C: a Python callable per bar: cb(t) -> int
#[pyfunction]
fn drive_python(py: Python<'_>, o: PyReadonlyArray1<f64>, c: PyReadonlyArray1<f64>, cb: Py<PyAny>, cash0: f64, fee: f64) -> PyResult<(f64, u32)> {
    let (o, c) = (o.as_slice()?, c.as_slice()?);
    let mut err: Option<PyErr> = None;
    let r = drive(o, c, cash0, fee, |t| match cb.call1(py, (t,)).and_then(|v| v.extract::<i8>(py)) {
        Ok(v) => v, Err(e) => { err.get_or_insert(e); 0 } });
    if let Some(e) = err { return Err(e); }
    Ok(r)
}
