//! hbt_core: Python bindings of hbt-core / hbt-ir. Data enters through the Arrow C stream interface
//! (`bars_from_arrow`: DuckDB relations, Polars frames) or as numpy arrays (dicts built by `lab/hbt`).
//!
//! * `run` / `run_batch`   boolean signal arrays (Phase 1.2 API)
//! * `compile` -> Program  Strategy IR v1 (checked once); `run_ir`, `sweep_ir`, `eval_signals`
//! * `run_py`              Python-tier strategies (a callback per bar, GIL held)

use std::collections::{BTreeMap, HashMap};

use arrow_array::{Array, Float64Array, Int64Array};
use engine_core::Config;
use engine_core::decision::{BarCtx, DecisionSource, Intent, IntentKind, Level, OrderType, Ov, PosState, Sizing};
use engine_core::engine::{Bars, CaEvent, InstrumentChange, Outcome, REJECT_NAMES};
use engine_core::eval::{Frame, IrSource, MarketEval, Sessions, SigData, SweepLayout, precompute};
use engine_core::ir::ops::{FRAMES, frame_code, frame_rank};
use engine_core::ir::{Plan, Program as IrProgram, build_plan, compile as ir_compile};
use engine_core::metrics;
use numpy::{IntoPyArray, PyArray1, PyArrayMethods, PyReadonlyArray1, PyReadonlyArray2, PyUntypedArrayMethods};
use pyo3::create_exception;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use pyo3_arrow::PyRecordBatchReader;
use rayon::prelude::*;

const IST_OFFSET: i64 = 19_800;

create_exception!(hbt_core, IRError, PyValueError, "Strategy IR compile error: args = (message, code, pointer, hint)");

fn err(e: String) -> PyErr {
    PyValueError::new_err(e)
}

fn ir_err(e: engine_core::ir::IrError) -> PyErr {
    IRError::new_err((e.to_string(), e.code, e.pointer, e.hint))
}

// ------------------------------------------------------------------------------------------------ Arrow ingestion

/// Arrow stream with columns ts (int64, epoch s), open/high/low/close (float64, rupees), volume (int64) ->
/// dict of int64 numpy arrays: ts, open, high, low, close (price units), volume, day, minute.
/// Prices must be finite exact multiples of 1/price_scale (never silently rounded); NULLs are refused (A2-M3).
#[pyfunction]
#[pyo3(signature = (stream, price_scale=100))]
fn bars_from_arrow<'py>(
    py: Python<'py>,
    stream: PyRecordBatchReader,
    price_scale: i64,
) -> PyResult<Bound<'py, PyDict>> {
    let reader = stream.into_reader()?;
    let (mut ts, mut o, mut h, mut l, mut c, mut v) = (vec![], vec![], vec![], vec![], vec![], vec![]);
    let scale = price_scale as f64;
    for batch in reader {
        let b = batch.map_err(|e| err(e.to_string()))?;
        let col_i = |name: &str| -> PyResult<Int64Array> {
            let a = b.column_by_name(name).ok_or_else(|| err(format!("missing column {name}")))?;
            let a = arrow_array::cast::AsArray::as_primitive_opt::<arrow_array::types::Int64Type>(a.as_ref())
                .ok_or_else(|| err(format!("column {name} must be int64")))?;
            Ok(a.clone())
        };
        let col_f = |name: &str| -> PyResult<Float64Array> {
            let a = b.column_by_name(name).ok_or_else(|| err(format!("missing column {name}")))?;
            let a = arrow_array::cast::AsArray::as_primitive_opt::<arrow_array::types::Float64Type>(a.as_ref())
                .ok_or_else(|| err(format!("column {name} must be float64")))?;
            Ok(a.clone())
        };
        let t = col_i("ts")?;
        let vol = col_i("volume")?;
        let prices = [col_f("open")?, col_f("high")?, col_f("low")?, col_f("close")?];
        if t.null_count() > 0 || vol.null_count() > 0 || prices.iter().any(|p| p.null_count() > 0) {
            return Err(err("bars contain NULL ts / prices / volume".into()));
        }
        ts.extend_from_slice(t.values());
        v.extend_from_slice(vol.values());
        for (dst, src) in [&mut o, &mut h, &mut l, &mut c].into_iter().zip(prices.iter()) {
            for &p in src.values().iter() {
                if !p.is_finite() {
                    return Err(err(format!("price {p} is not a finite number")));
                }
                let u = p * scale;
                let r = u.round();
                if (u - r).abs() > 1e-6 * scale.max(1.0) {
                    return Err(err(format!("price {p} is not a multiple of 1/{price_scale} rupee")));
                }
                dst.push(r as i64);
            }
        }
    }
    let day: Vec<i64> = ts.iter().map(|t| (t + IST_OFFSET).div_euclid(86_400)).collect();
    let minute: Vec<i64> = ts.iter().map(|t| (t + IST_OFFSET).rem_euclid(86_400) / 60).collect();
    let d = PyDict::new(py);
    d.set_item("ts", ts.into_pyarray(py))?;
    d.set_item("open", o.into_pyarray(py))?;
    d.set_item("high", h.into_pyarray(py))?;
    d.set_item("low", l.into_pyarray(py))?;
    d.set_item("close", c.into_pyarray(py))?;
    d.set_item("volume", v.into_pyarray(py))?;
    d.set_item("day", day.into_pyarray(py))?;
    d.set_item("minute", minute.into_pyarray(py))?;
    Ok(d)
}

/// Multi-instrument Arrow stream (instrument_id int64 + the bars_from_arrow columns, any order) ->
/// {instrument_id: bars dict}. One pass over the stream, no per-symbol queries (symbol-major Universe loader, T1.6.1).
#[pyfunction]
#[pyo3(signature = (stream, price_scale=100))]
fn bars_from_arrow_multi<'py>(
    py: Python<'py>,
    stream: PyRecordBatchReader,
    price_scale: i64,
) -> PyResult<Bound<'py, PyDict>> {
    type Cols = (Vec<i64>, Vec<i64>, Vec<i64>, Vec<i64>, Vec<i64>, Vec<i64>);
    let reader = stream.into_reader()?;
    let scale = price_scale as f64;
    let mut by: HashMap<i64, Cols> = HashMap::new();
    let mut order: Vec<i64> = vec![];
    let to_units = |p: f64| -> PyResult<i64> {
        if !p.is_finite() {
            return Err(err(format!("price {p} is not a finite number")));
        }
        let u = p * scale;
        let r = u.round();
        if (u - r).abs() > 1e-6 * scale.max(1.0) {
            return Err(err(format!("price {p} is not a multiple of 1/{price_scale} rupee")));
        }
        Ok(r as i64)
    };
    for batch in reader {
        let b = batch.map_err(|e| err(e.to_string()))?;
        let col_i = |name: &str| -> PyResult<Int64Array> {
            let a = b.column_by_name(name).ok_or_else(|| err(format!("missing column {name}")))?;
            Ok(arrow_array::cast::AsArray::as_primitive_opt::<arrow_array::types::Int64Type>(a.as_ref())
                .ok_or_else(|| err(format!("column {name} must be int64")))?
                .clone())
        };
        let col_f = |name: &str| -> PyResult<Float64Array> {
            let a = b.column_by_name(name).ok_or_else(|| err(format!("missing column {name}")))?;
            Ok(arrow_array::cast::AsArray::as_primitive_opt::<arrow_array::types::Float64Type>(a.as_ref())
                .ok_or_else(|| err(format!("column {name} must be float64")))?
                .clone())
        };
        let (id, ts, vol) = (col_i("instrument_id")?, col_i("ts")?, col_i("volume")?);
        let (o, h, l, c) = (col_f("open")?, col_f("high")?, col_f("low")?, col_f("close")?);
        if id.null_count()
            + ts.null_count()
            + vol.null_count()
            + o.null_count()
            + h.null_count()
            + l.null_count()
            + c.null_count()
            > 0
        {
            return Err(err("bars contain NULL values".into()));
        }
        let (idv, tsv, vv) = (id.values(), ts.values(), vol.values());
        let (ov, hv, lv, cv) = (o.values(), h.values(), l.values(), c.values());
        for i in 0..b.num_rows() {
            let e = by.entry(idv[i]).or_insert_with(|| {
                order.push(idv[i]);
                Default::default()
            });
            e.0.push(tsv[i]);
            e.1.push(to_units(ov[i])?);
            e.2.push(to_units(hv[i])?);
            e.3.push(to_units(lv[i])?);
            e.4.push(to_units(cv[i])?);
            e.5.push(vv[i]);
        }
    }
    let out = PyDict::new(py);
    for id in order {
        let (ts, o, h, l, c, v) = by.remove(&id).unwrap();
        let day: Vec<i64> = ts.iter().map(|t| (t + IST_OFFSET).div_euclid(86_400)).collect();
        let minute: Vec<i64> = ts.iter().map(|t| (t + IST_OFFSET).rem_euclid(86_400) / 60).collect();
        let d = PyDict::new(py);
        d.set_item("ts", ts.into_pyarray(py))?;
        d.set_item("open", o.into_pyarray(py))?;
        d.set_item("high", h.into_pyarray(py))?;
        d.set_item("low", l.into_pyarray(py))?;
        d.set_item("close", c.into_pyarray(py))?;
        d.set_item("volume", v.into_pyarray(py))?;
        d.set_item("day", day.into_pyarray(py))?;
        d.set_item("minute", minute.into_pyarray(py))?;
        out.set_item(id, d)?;
    }
    Ok(out)
}

// ------------------------------------------------------------------------------------------------ borrowed inputs
// Arrays are BORROWED from numpy (no copy; the Python side makes them C-contiguous). Only `minute` is converted
// (int64 -> int32). The Rust views (Bars / SigData) are built before the GIL is released.

type AI<'py> = PyReadonlyArray1<'py, i64>;
type AF<'py> = PyReadonlyArray1<'py, f64>;

fn arr_i<'py>(d: &Bound<'py, PyDict>, k: &str) -> PyResult<AI<'py>> {
    let a = d.get_item(k)?.ok_or_else(|| err(format!("missing '{k}'")))?;
    let arr: AI<'py> = a.extract().map_err(|_| err(format!("'{k}' must be an int64 numpy array")))?;
    arr.as_slice().map_err(|_| err(format!("'{k}' must be C-contiguous")))?;
    Ok(arr)
}

fn arr_f<'py>(d: &Bound<'py, PyDict>, k: &str) -> PyResult<AF<'py>> {
    let a = d.get_item(k)?.ok_or_else(|| err(format!("missing '{k}'")))?;
    let arr: AF<'py> = a.extract().map_err(|_| err(format!("'{k}' must be a float64 numpy array")))?;
    arr.as_slice().map_err(|_| err(format!("'{k}' must be C-contiguous")))?;
    Ok(arr)
}

fn sub<'py>(d: &Bound<'py, PyDict>, k: &str) -> PyResult<Option<Bound<'py, PyDict>>> {
    match d.get_item(k)? {
        None => Ok(None),
        Some(x) if x.is_none() => Ok(None),
        Some(x) => Ok(Some(x.cast_into::<PyDict>().map_err(|_| err(format!("'{k}' must be a dict")))?)),
    }
}

struct BarsView<'py> {
    ts: AI<'py>,
    o: AI<'py>,
    h: AI<'py>,
    l: AI<'py>,
    c: AI<'py>,
    v: AI<'py>,
    day: AI<'py>,
    minute: Vec<i32>,
    bar_secs: i64,
}

impl<'py> BarsView<'py> {
    fn from_dict(d: &Bound<'py, PyDict>, bar_secs: i64) -> PyResult<Self> {
        let minute = arr_i(d, "minute")?.as_slice()?.iter().map(|m| *m as i32).collect();
        Ok(BarsView {
            ts: arr_i(d, "ts")?,
            o: arr_i(d, "open")?,
            h: arr_i(d, "high")?,
            l: arr_i(d, "low")?,
            c: arr_i(d, "close")?,
            v: arr_i(d, "volume")?,
            day: arr_i(d, "day")?,
            minute,
            bar_secs,
        })
    }
    fn len(&self) -> usize {
        self.ts.len()
    }
    fn bars(&self) -> Bars<'_> {
        Bars {
            ts: self.ts.as_slice().unwrap(),
            open: self.o.as_slice().unwrap(),
            high: self.h.as_slice().unwrap(),
            low: self.l.as_slice().unwrap(),
            close: self.c.as_slice().unwrap(),
            volume: self.v.as_slice().unwrap(),
            day: self.day.as_slice().unwrap(),
            minute: &self.minute,
            bar_secs: self.bar_secs,
        }
    }
}

fn events_from(d: Option<Bound<'_, PyDict>>) -> PyResult<Vec<CaEvent>> {
    let Some(d) = d else { return Ok(vec![]) };
    let (day, num, den, cash) =
        (arr_i(&d, "day")?, arr_i(&d, "qty_num")?, arr_i(&d, "qty_den")?, arr_i(&d, "cash_paise")?);
    let (day, num, den, cash) = (day.as_slice()?, num.as_slice()?, den.as_slice()?, cash.as_slice()?);
    if num.len() != day.len() || den.len() != day.len() || cash.len() != day.len() {
        return Err(err("events arrays must have equal lengths".into()));
    }
    let mut ev: Vec<CaEvent> = (0..day.len())
        .map(|i| CaEvent { day: day[i], qty_num: num[i], qty_den: den[i], cash_paise: cash[i] })
        .collect();
    if ev.iter().any(|e| e.qty_num < 1 || e.qty_den < 1 || e.cash_paise < 0) {
        return Err(err("events need qty_num, qty_den >= 1 and cash_paise >= 0".into()));
    }
    ev.sort_by_key(|e| e.day);
    Ok(ev)
}

fn changes_from(d: Option<Bound<'_, PyDict>>) -> PyResult<Vec<InstrumentChange>> {
    let Some(d) = d else { return Ok(vec![]) };
    let (day, lot, tick) = (arr_i(&d, "day")?, arr_i(&d, "lot")?, arr_i(&d, "tick")?);
    let (day, lot, tick) = (day.as_slice()?, lot.as_slice()?, tick.as_slice()?);
    if lot.len() != day.len() || tick.len() != day.len() {
        return Err(err("instrument changes: day / lot / tick of equal length".into()));
    }
    let mut v: Vec<InstrumentChange> =
        (0..day.len()).map(|i| InstrumentChange { day: day[i], lot: lot[i], tick: tick[i] }).collect();
    v.sort_by_key(|c| c.day);
    Ok(v)
}

struct FrameView<'py> {
    ts: AI<'py>,
    o: AF<'py>,
    h: AF<'py>,
    l: AF<'py>,
    c: AF<'py>,
    v: AF<'py>,
    avail: AI<'py>,
}

impl<'py> FrameView<'py> {
    fn from_dict(d: &Bound<'py, PyDict>, ts: Option<AI<'py>>) -> PyResult<Self> {
        Ok(FrameView {
            ts: match ts {
                Some(t) => t,
                None => arr_i(d, "ts")?,
            },
            o: arr_f(d, "open")?,
            h: arr_f(d, "high")?,
            l: arr_f(d, "low")?,
            c: arr_f(d, "close")?,
            v: arr_f(d, "volume")?,
            avail: arr_i(d, "available_at")?,
        })
    }
    fn frame(&self) -> Frame<'_> {
        Frame {
            ts: self.ts.as_slice().unwrap(),
            open: self.o.as_slice().unwrap(),
            high: self.h.as_slice().unwrap(),
            low: self.l.as_slice().unwrap(),
            close: self.c.as_slice().unwrap(),
            volume: self.v.as_slice().unwrap(),
            avail: self.avail.as_slice().unwrap(),
        }
    }
}

/// A Dataset (lab/hbt/dataset.py `Dataset.core()`), borrowed from numpy.
struct DatasetView<'py> {
    exec: BarsView<'py>,
    sig: FrameView<'py>,
    factor: AF<'py>,
    sessions: Sessions,
    frames: Vec<(u8, FrameView<'py>)>,
    extras: Vec<(String, AF<'py>)>,
    events: Vec<CaEvent>,
    changes: Vec<InstrumentChange>,
    price_scale: i64,
    trade_start: usize,
    interval: String,
}

impl<'py> DatasetView<'py> {
    fn from_dict(d: &Bound<'py, PyDict>) -> PyResult<Self> {
        let get_i = |k: &str| -> PyResult<i64> {
            d.get_item(k)?.ok_or_else(|| err(format!("dataset lacks '{k}'")))?.extract::<i64>()
        };
        let interval: String =
            d.get_item("interval")?.ok_or_else(|| err("dataset lacks 'interval'".into()))?.extract()?;
        let rank = frame_rank(&interval).ok_or_else(|| err(format!("unknown interval '{interval}'")))?;
        let bar_secs = FRAMES[rank as usize].1;
        let exec = BarsView::from_dict(&sub(d, "bars")?.ok_or_else(|| err("dataset lacks 'bars'".into()))?, bar_secs)?;
        let sd = sub(d, "sig")?.ok_or_else(|| err("dataset lacks 'sig'".into()))?;
        let sig = FrameView::from_dict(&sd, Some(arr_i(&sub(d, "bars")?.unwrap(), "ts")?))?;
        let factor = arr_f(&sd, "factor")?;
        if factor.as_slice()?.iter().any(|f| !(f.is_finite() && *f > 0.0)) {
            return Err(err("adjustment factors must be finite and > 0".into()));
        }
        for (k, col) in [("open", &sig.o), ("high", &sig.h), ("low", &sig.l), ("close", &sig.c), ("volume", &sig.v)] {
            if col.as_slice()?.iter().any(|x| !x.is_finite()) {
                return Err(err(format!("signal column {k} has non-finite values")));
            }
        }
        let sessions = match sub(d, "sessions")? {
            None => Sessions::default(),
            Some(s) => {
                let day = arr_i(&s, "day")?.as_slice()?.to_vec();
                let open: Vec<i32> = arr_i(&s, "open")?.as_slice()?.iter().map(|x| *x as i32).collect();
                let close: Vec<i32> = arr_i(&s, "close")?.as_slice()?.iter().map(|x| *x as i32).collect();
                if open.len() != day.len() || close.len() != day.len() || day.windows(2).any(|w| w[1] <= w[0]) {
                    return Err(err("sessions: day / open / close of equal length, days ascending".into()));
                }
                Sessions { day, open, close }
            }
        };
        let mut frames = vec![];
        if let Some(fs) = sub(d, "frames")? {
            for (k, v) in fs.iter() {
                let code: String = k.extract()?;
                let r = frame_rank(&code).ok_or_else(|| err(format!("unknown frame '{code}'")))?;
                let fd = v.cast_into::<PyDict>().map_err(|_| err(format!("frame {code} must be a dict")))?;
                frames.push((r, FrameView::from_dict(&fd, None)?));
            }
        }
        let mut extras = vec![];
        if let Some(xs) = sub(d, "extras")? {
            for (k, v) in xs.iter() {
                let name: String = k.extract()?;
                let arr: AF<'py> = v.extract()?;
                arr.as_slice().map_err(|_| err(format!("extra column {name} must be C-contiguous")))?;
                extras.push((name, arr));
            }
        }
        let events = events_from(sub(d, "events")?)?;
        let changes = changes_from(sub(d, "changes")?)?;
        Ok(DatasetView {
            exec,
            changes,
            sig,
            factor,
            sessions,
            frames,
            extras,
            events,
            price_scale: get_i("price_scale")?,
            trade_start: get_i("trade_start")?.max(0) as usize,
            interval,
        })
    }

    fn sig_data(&self, plan: &Plan) -> PyResult<SigData<'_>> {
        let mut extras = vec![];
        for name in &plan.extras {
            let col = self
                .extras
                .iter()
                .find(|(k, _)| k == name)
                .ok_or_else(|| err(format!("the program reads ${name}; the dataset has no such column")))?;
            extras.push(col.1.as_slice()?);
        }
        Ok(SigData {
            base: self.sig.frame(),
            day: self.exec.day.as_slice()?,
            minute: &self.exec.minute,
            bar_secs: self.exec.bar_secs,
            frames: self.frames.iter().map(|(r, f)| (*r, f.frame())).collect(),
            extras,
            factor: self.factor.as_slice()?,
            sessions: self.sessions.clone(),
            price_scale: self.price_scale,
            trade_start: self.trade_start,
        })
    }

    fn extra_names(&self) -> Vec<String> {
        self.extras.iter().map(|(k, _)| k.clone()).collect()
    }
}

// ------------------------------------------------------------------------------------------------ results

fn metrics_dict<'py>(py: Python<'py>, m: &metrics::Metrics) -> PyResult<Bound<'py, PyDict>> {
    let md = PyDict::new(py);
    for (k, v) in [
        ("total_return", m.total_return),
        ("cagr", m.cagr),
        ("ann_vol", m.ann_vol),
        ("sharpe", m.sharpe),
        ("sortino", m.sortino),
        ("max_drawdown", m.max_drawdown),
        ("calmar", m.calmar),
        ("win_rate", m.win_rate),
        ("profit_factor", m.profit_factor),
        ("expectancy_paise", m.expectancy_paise),
        ("avg_bars_held", m.avg_bars_held),
        ("exposure", m.exposure),
    ] {
        md.set_item(k, v)?;
    }
    md.set_item("trades", m.trades)?;
    md.set_item("max_dd_bars", m.max_dd_bars)?;
    md.set_item("fees_paise", m.fees_paise)?;
    Ok(md)
}

fn outcome_dict<'py>(py: Python<'py>, out: Outcome, cash0: i64, ppy: f64) -> PyResult<Bound<'py, PyDict>> {
    let m = metrics::compute(&out, cash0, ppy);
    let d = PyDict::new(py);
    d.set_item("metrics", metrics_dict(py, &m)?)?;
    let f = &out.fills;
    let fd = PyDict::new(py);
    fd.set_item("bar", f.iter().map(|x| x.bar as i64).collect::<Vec<_>>().into_pyarray(py))?;
    fd.set_item("ts", f.iter().map(|x| x.ts).collect::<Vec<_>>().into_pyarray(py))?;
    fd.set_item("buy", f.iter().map(|x| x.buy as u8).collect::<Vec<_>>().into_pyarray(py))?;
    fd.set_item("qty", f.iter().map(|x| x.qty).collect::<Vec<_>>().into_pyarray(py))?;
    fd.set_item("price", f.iter().map(|x| x.price).collect::<Vec<_>>().into_pyarray(py))?;
    fd.set_item("value", f.iter().map(|x| x.value).collect::<Vec<_>>().into_pyarray(py))?;
    fd.set_item("fees", f.iter().map(|x| x.charges.total()).collect::<Vec<_>>().into_pyarray(py))?;
    fd.set_item("reason", f.iter().map(|x| x.reason as u8).collect::<Vec<_>>().into_pyarray(py))?;
    for (k, g) in [("brokerage", 0), ("stt", 1), ("exch", 2), ("sebi", 3), ("stamp", 4), ("gst", 5), ("dp", 6)] {
        let col: Vec<i64> = f
            .iter()
            .map(|x| {
                let c = &x.charges;
                [c.brokerage, c.stt, c.exch, c.sebi, c.stamp, c.gst, c.dp][g]
            })
            .collect();
        fd.set_item(k, col.into_pyarray(py))?;
    }
    d.set_item("fills", fd)?;
    let t = &out.trades;
    let td = PyDict::new(py);
    td.set_item("dir", t.iter().map(|x| x.dir as i64).collect::<Vec<_>>().into_pyarray(py))?;
    td.set_item("qty", t.iter().map(|x| x.qty).collect::<Vec<_>>().into_pyarray(py))?;
    td.set_item("entry_bar", t.iter().map(|x| x.entry_bar as i64).collect::<Vec<_>>().into_pyarray(py))?;
    td.set_item("exit_bar", t.iter().map(|x| x.exit_bar as i64).collect::<Vec<_>>().into_pyarray(py))?;
    td.set_item("entry_ts", t.iter().map(|x| x.entry_ts).collect::<Vec<_>>().into_pyarray(py))?;
    td.set_item("exit_ts", t.iter().map(|x| x.exit_ts).collect::<Vec<_>>().into_pyarray(py))?;
    td.set_item("entry_value", t.iter().map(|x| x.entry_value).collect::<Vec<_>>().into_pyarray(py))?;
    td.set_item("exit_value", t.iter().map(|x| x.exit_value).collect::<Vec<_>>().into_pyarray(py))?;
    td.set_item("fees", t.iter().map(|x| x.fees).collect::<Vec<_>>().into_pyarray(py))?;
    td.set_item("income", t.iter().map(|x| x.income).collect::<Vec<_>>().into_pyarray(py))?;
    td.set_item("pnl", t.iter().map(|x| x.pnl).collect::<Vec<_>>().into_pyarray(py))?;
    td.set_item("exit_reason", t.iter().map(|x| x.exit_reason as u8).collect::<Vec<_>>().into_pyarray(py))?;
    d.set_item("trades", td)?;
    let id = PyDict::new(py);
    let it = &out.intents;
    id.set_item("bar", it.iter().map(|x| x.bar as i64).collect::<Vec<_>>().into_pyarray(py))?;
    id.set_item("kind", it.iter().map(|x| x.kind).collect::<Vec<_>>().into_pyarray(py))?;
    id.set_item("order", it.iter().map(|x| x.order).collect::<Vec<_>>().into_pyarray(py))?;
    id.set_item("price", it.iter().map(|x| x.price).collect::<Vec<_>>().into_pyarray(py))?;
    id.set_item("limit", it.iter().map(|x| x.limit).collect::<Vec<_>>().into_pyarray(py))?;
    d.set_item("intents", id)?;
    let rj = PyDict::new(py);
    for (k, n) in REJECT_NAMES.iter().zip(out.rejects.iter()) {
        rj.set_item(*k, *n)?;
    }
    d.set_item("rejects", rj)?;
    let ch = PyDict::new(py);
    let c = &out.charges;
    for (k, v) in [
        ("brokerage", c.brokerage),
        ("stt", c.stt),
        ("exch", c.exch),
        ("sebi", c.sebi),
        ("stamp", c.stamp),
        ("gst", c.gst),
        ("dp", c.dp),
    ] {
        ch.set_item(k, v)?;
    }
    d.set_item("charges", ch)?;
    let cn = PyDict::new(py);
    for (k, v) in &out.counters {
        cn.set_item(*k, *v)?;
    }
    d.set_item("counters", cn)?;
    d.set_item("equity", out.equity.into_pyarray(py))?;
    d.set_item("cash", out.cash)?;
    d.set_item("position", out.position)?;
    d.set_item("active_stop", out.active_stop)?;
    d.set_item("active_target", out.active_target)?;
    d.set_item("bars_in_market", out.bars_in_market)?;
    d.set_item("warnings", out.warnings)?;
    Ok(d)
}

fn u8s(a: &PyReadonlyArray1<u8>) -> PyResult<Vec<u8>> {
    Ok(a.as_slice()?.to_vec())
}

// ------------------------------------------------------------------------------------------------ array API

/// One backtest with boolean signal arrays. `bars` / `m1`: dicts of int64 arrays (see bars_from_arrow); signals:
/// uint8 arrays; `events`: corporate actions {day, qty_num, qty_den, cash_paise} (int64 arrays).
#[pyfunction]
#[pyo3(signature = (bars, bar_secs, long_entry, long_exit, short_entry, short_exit, spec_json, plan_json, inst_json,
                    costs_json, cash0_paise, periods_per_year, m1=None, events=None))]
#[allow(clippy::too_many_arguments)]
fn run<'py>(
    py: Python<'py>,
    bars: Bound<'py, PyDict>,
    bar_secs: i64,
    long_entry: PyReadonlyArray1<u8>,
    long_exit: PyReadonlyArray1<u8>,
    short_entry: PyReadonlyArray1<u8>,
    short_exit: PyReadonlyArray1<u8>,
    spec_json: &str,
    plan_json: &str,
    inst_json: &str,
    costs_json: &str,
    cash0_paise: i64,
    periods_per_year: f64,
    m1: Option<Bound<'py, PyDict>>,
    events: Option<Bound<'py, PyDict>>,
) -> PyResult<Bound<'py, PyDict>> {
    let bv = BarsView::from_dict(&bars, bar_secs)?;
    let mv = m1.map(|d| BarsView::from_dict(&d, 60)).transpose()?;
    let ev = events_from(events)?;
    let (le, lx, se, sx) = (u8s(&long_entry)?, u8s(&long_exit)?, u8s(&short_entry)?, u8s(&short_exit)?);
    let cfg = Config::from_json(spec_json, plan_json, inst_json, costs_json).map_err(err)?;
    let (b, mb) = (bv.bars(), mv.as_ref().map(|x| x.bars()));
    let out = py
        .detach(|| {
            let mut src = engine_core::decision::ArraySignals::new(&le, &lx, &se, &sx, &cfg.plan);
            src.check(b.len())?;
            cfg.run(cash0_paise, &b, mb.as_ref(), &ev, &mut src)
        })
        .map_err(err)?;
    outcome_dict(py, out, cash0_paise, periods_per_year)
}

fn metrics_columns<'py>(py: Python<'py>, ms: &[metrics::Metrics], d: &Bound<'py, PyDict>) -> PyResult<()> {
    macro_rules! col {
        ($name:literal, $f:expr) => {
            d.set_item($name, ms.iter().map($f).collect::<Vec<f64>>().into_pyarray(py))?;
        };
    }
    col!("total_return", |m| m.total_return);
    col!("cagr", |m| m.cagr);
    col!("sharpe", |m| m.sharpe);
    col!("sortino", |m| m.sortino);
    col!("max_drawdown", |m| m.max_drawdown);
    col!("calmar", |m| m.calmar);
    col!("win_rate", |m| m.win_rate);
    col!("profit_factor", |m| m.profit_factor);
    col!("trades", |m| m.trades as f64);
    col!("fees_paise", |m| m.fees_paise as f64);
    col!("exposure", |m| m.exposure);
    Ok(())
}

/// Many signal sets over the same bars in parallel (sweeps). Signal matrices are [bars, sets] uint8; `plans_json`
/// holds one OrderPlan per set. Returns metrics arrays (one value per set).
#[pyfunction]
#[pyo3(signature = (bars, bar_secs, long_entry, long_exit, short_entry, short_exit, spec_json, plans_json, inst_json,
                    costs_json, cash0_paise, periods_per_year, m1=None, events=None))]
#[allow(clippy::too_many_arguments)]
fn run_batch<'py>(
    py: Python<'py>,
    bars: Bound<'py, PyDict>,
    bar_secs: i64,
    long_entry: PyReadonlyArray2<u8>,
    long_exit: PyReadonlyArray2<u8>,
    short_entry: PyReadonlyArray2<u8>,
    short_exit: PyReadonlyArray2<u8>,
    spec_json: &str,
    plans_json: Vec<String>,
    inst_json: &str,
    costs_json: &str,
    cash0_paise: i64,
    periods_per_year: f64,
    m1: Option<Bound<'py, PyDict>>,
    events: Option<Bound<'py, PyDict>>,
) -> PyResult<Bound<'py, PyDict>> {
    let bv = BarsView::from_dict(&bars, bar_secs)?;
    let mv = m1.map(|d| BarsView::from_dict(&d, 60)).transpose()?;
    let ev = events_from(events)?;
    let sets = plans_json.len();
    let nbars = bv.len();
    let cols = |a: &PyReadonlyArray2<u8>| -> PyResult<Vec<Vec<u8>>> {
        let v = a.as_array();
        if v.ncols() != sets || v.nrows() != nbars {
            return Err(err(format!("signal matrix must be [{nbars} bars, {sets} sets]")));
        }
        Ok((0..sets).map(|j| v.column(j).to_vec()).collect())
    };
    let (le, lx, se, sx) = (cols(&long_entry)?, cols(&long_exit)?, cols(&short_entry)?, cols(&short_exit)?);
    let cfgs: Vec<Config> = plans_json
        .iter()
        .map(|p| Config::from_json(spec_json, p, inst_json, costs_json))
        .collect::<Result<_, _>>()
        .map_err(err)?;
    let (b, mb) = (bv.bars(), mv.as_ref().map(|x| x.bars()));
    let results: Vec<Result<metrics::Metrics, String>> = py.detach(|| {
        (0..sets)
            .into_par_iter()
            .map(|j| {
                let mut src = engine_core::decision::ArraySignals::new(&le[j], &lx[j], &se[j], &sx[j], &cfgs[j].plan);
                cfgs[j]
                    .run_full(cash0_paise, &b, mb.as_ref(), &ev, &[], true, &mut src)
                    .map(|o| metrics::compute(&o, cash0_paise, periods_per_year))
            })
            .collect()
    });
    let ms: Vec<metrics::Metrics> = results.into_iter().collect::<Result<_, _>>().map_err(err)?;
    let d = PyDict::new(py);
    metrics_columns(py, &ms, &d)?;
    Ok(d)
}

// ------------------------------------------------------------------------------------------------ Strategy IR

/// A checked Strategy IR v1 document.
#[pyclass(frozen, module = "hbt_core")]
struct Program {
    prog: IrProgram,
}

fn parse_variant(json: Option<&str>) -> PyResult<BTreeMap<String, f64>> {
    match json {
        None => Ok(BTreeMap::new()),
        Some(s) => {
            let v: BTreeMap<String, serde_json::Value> =
                serde_json::from_str(s).map_err(|e| err(format!("params: {e}")))?;
            v.into_iter()
                .map(|(k, x)| {
                    let f = match x {
                        serde_json::Value::Bool(b) => b as u8 as f64,
                        serde_json::Value::Number(n) => n.as_f64().unwrap_or(f64::NAN),
                        _ => return Err(err(format!("param {k} must be a number or bool"))),
                    };
                    Ok((k, f))
                })
                .collect()
        }
    }
}

fn plan_for(prog: &IrProgram, variants: &[BTreeMap<String, f64>], ds: &DatasetView) -> PyResult<Plan> {
    build_plan(prog, variants, &ds.interval, &ds.extra_names()).map_err(ir_err)
}

#[pymethods]
impl Program {
    #[getter]
    fn sha256(&self) -> String {
        self.prog.ir_sha256.clone()
    }
    #[getter]
    fn canonical(&self) -> String {
        self.prog.canonical.clone()
    }
    #[getter]
    fn name(&self) -> String {
        self.prog.doc.name.clone()
    }
    #[getter]
    fn interval(&self) -> Option<String> {
        self.prog.doc.interval.clone()
    }
    #[getter]
    fn product(&self) -> Option<String> {
        self.prog.doc.product.clone()
    }
    #[getter]
    fn adjustment(&self) -> Option<String> {
        self.prog.doc.adjustment.clone()
    }
    fn explain(&self) -> String {
        engine_core::ir::explain::explain(&self.prog.doc)
    }
    /// {name: {type, default, min, max, step, choices}}
    fn params<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, p) in &self.prog.doc.params {
            let x = PyDict::new(py);
            x.set_item("type", format!("{:?}", p.ty).to_lowercase())?;
            x.set_item("default", p.default)?;
            x.set_item("min", p.min)?;
            x.set_item("max", p.max)?;
            x.set_item("step", p.step)?;
            x.set_item("choices", p.choices.clone())?;
            d.set_item(k, x)?;
        }
        Ok(d)
    }
    /// Compile-time view on an interval: nodes, lookback, shared subexpressions.
    #[pyo3(signature = (interval, params_json=None))]
    fn plan<'py>(&self, py: Python<'py>, interval: &str, params_json: Option<&str>) -> PyResult<Bound<'py, PyDict>> {
        let plan = build_plan(&self.prog, &[parse_variant(params_json)?], interval, &[]).map_err(ir_err)?;
        let d = PyDict::new(py);
        let nodes = PyList::empty(py);
        for n in &plan.nodes {
            let x = PyDict::new(py);
            x.set_item("label", &n.label)?;
            x.set_item("op", format!("{:?}", n.op))?;
            x.set_item(
                "frame",
                if n.frame == u8::MAX { "const".to_string() } else { frame_code(n.frame).to_string() },
            )?;
            x.set_item("position", n.position)?;
            x.set_item("lookback", n.lookback)?;
            x.set_item("slot", n.slot)?;
            nodes.append(x)?;
        }
        d.set_item("nodes", nodes)?;
        let v = plan.variants.first();
        d.set_item("lookback", v.map(|v| v.lookback))?;
        d.set_item("rejected", plan.rejected.iter().map(|r| r.1.clone()).collect::<Vec<_>>())?;
        d.set_item("frames", plan.frames.iter().map(|f| frame_code(*f)).collect::<Vec<_>>())?;
        d.set_item("extras", plan.extras.clone())?;
        d.set_item("plan_sha256", plan.plan_sha256)?;
        d.set_item("n_slots", plan.n_slots)?;
        Ok(d)
    }
    fn __repr__(&self) -> String {
        format!("<hbt_core.Program '{}' {}>", self.prog.doc.name, &self.prog.ir_sha256[..12])
    }
}

/// Check an IR v1 document (JSON text). Raises IRError(message, code, pointer, hint).
#[pyfunction]
fn compile(text: &str) -> PyResult<Program> {
    Ok(Program { prog: ir_compile(text).map_err(ir_err)? })
}

/// One IR run. mode "live": market nodes stepped inside the bar loop (streaming); "pre": precomputed first (batch).
#[pyfunction]
#[pyo3(signature = (program, dataset, params_json, spec_json, plan_json, inst_json, costs_json, cash0_paise,
                    periods_per_year, m1=None, mode="live", trace=false))]
#[allow(clippy::too_many_arguments)]
fn run_ir<'py>(
    py: Python<'py>,
    program: &Program,
    dataset: Bound<'py, PyDict>,
    params_json: Option<&str>,
    spec_json: &str,
    plan_json: &str,
    inst_json: &str,
    costs_json: &str,
    cash0_paise: i64,
    periods_per_year: f64,
    m1: Option<Bound<'py, PyDict>>,
    mode: &str,
    trace: bool,
) -> PyResult<Bound<'py, PyDict>> {
    let ds = DatasetView::from_dict(&dataset)?;
    let mv = m1.map(|d| BarsView::from_dict(&d, 60)).transpose()?;
    let plan = plan_for(&program.prog, &[parse_variant(params_json)?], &ds)?;
    if plan.variants.is_empty() {
        return Err(IRError::new_err((
            format!("the parameters violate a constraint: {}", plan.rejected[0].1),
            "E-PARAM-RANGE",
            "/constraints",
            None::<String>,
        )));
    }
    let cfg = Config::from_json(spec_json, plan_json, inst_json, costs_json).map_err(err)?;
    let sd = ds.sig_data(&plan)?;
    if ds.exec.len() < plan.variants[0].lookback + 1 {
        return Err(IRError::new_err((
            format!("{} bars are fewer than the program's lookback {}", ds.exec.len(), plan.variants[0].lookback),
            "E-LOOKBACK-DATA",
            "",
            Some("load more history before the range (Dataset start)".to_string()),
        )));
    }
    let (eb, mb) = (ds.exec.bars(), mv.as_ref().map(|x| x.bars()));
    let (events, changes) = (&ds.events, &ds.changes);
    let (out, tr) = py
        .detach(|| -> Result<(Outcome, Option<Vec<f64>>), String> {
            let (matrix, layout);
            let mut src = if mode == "pre" {
                layout = SweepLayout::new(&plan);
                matrix = precompute(&plan, &sd, &layout.cols, Some(&layout))?;
                IrSource::pre(&plan, 0, &sd, &layout, &matrix)?
            } else {
                IrSource::live(&plan, 0, &sd)?
            };
            if trace {
                src = src.with_trace();
            }
            let out = cfg.run_full(cash0_paise, &eb, mb.as_ref(), events, changes, false, &mut src)?;
            Ok((out, src.trace.take()))
        })
        .map_err(err)?;
    let d = outcome_dict(py, out, cash0_paise, periods_per_year)?;
    d.set_item("lookback", plan.variants[0].lookback)?;
    d.set_item("plan_sha256", plan.plan_sha256.clone())?;
    if let Some(tr) = tr {
        let n = ds.exec.len();
        let arr = PyArray1::from_vec(py, tr).reshape([n, plan.n_slots])?;
        d.set_item("trace", arr)?;
        d.set_item("slot_labels", slot_labels(&plan))?;
    }
    Ok(d)
}

/// Portfolio mode: one IR program on several instruments sharing ONE account (engine_core::portfolio). Returns
/// {metrics (portfolio), ts, equity (paise, union timeline), cash, peak_open, legs: [outcome dict per instrument]};
/// a leg's `equity` / `cash` are its P&L contribution on top of the starting cash.
#[pyfunction]
#[pyo3(signature = (program, datasets, params_json, spec_jsons, plan_json, inst_jsons, costs_json, cash0_paise,
                    periods_per_year, max_positions=None, m1s=None))]
#[allow(clippy::too_many_arguments)]
fn run_ir_portfolio<'py>(
    py: Python<'py>,
    program: &Program,
    datasets: Vec<Bound<'py, PyDict>>,
    params_json: Option<&str>,
    spec_jsons: Vec<String>,
    plan_json: &str,
    inst_jsons: Vec<String>,
    costs_json: &str,
    cash0_paise: i64,
    periods_per_year: f64,
    max_positions: Option<u32>,
    m1s: Option<Vec<Option<Bound<'py, PyDict>>>>,
) -> PyResult<Bound<'py, PyDict>> {
    if datasets.is_empty() || datasets.len() != inst_jsons.len() || datasets.len() != spec_jsons.len() {
        return Err(err("run_ir_portfolio: one ExecSpec and one instrument per dataset, at least one".into()));
    }
    let dss: Vec<DatasetView> = datasets.iter().map(DatasetView::from_dict).collect::<PyResult<_>>()?;
    let variant = parse_variant(params_json)?;
    let mut plans = Vec::with_capacity(dss.len());
    for ds in &dss {
        let plan = plan_for(&program.prog, std::slice::from_ref(&variant), ds)?;
        if plan.variants.is_empty() {
            return Err(err(format!("the parameters violate a constraint: {}", plan.rejected[0].1)));
        }
        if ds.exec.len() < plan.variants[0].lookback + 1 {
            return Err(err(format!(
                "{} bars are fewer than the program's lookback {}",
                ds.exec.len(),
                plan.variants[0].lookback
            )));
        }
        plans.push(plan);
    }
    let cfgs: Vec<Config> = inst_jsons
        .iter()
        .zip(&spec_jsons)
        .map(|(ij, sj)| Config::from_json(sj, plan_json, ij, costs_json))
        .collect::<Result<_, _>>()
        .map_err(err)?;
    let sds: Vec<SigData> = dss.iter().zip(&plans).map(|(ds, p)| ds.sig_data(p)).collect::<PyResult<_>>()?;
    let m1v: Vec<Option<BarsView>> = match m1s {
        None => dss.iter().map(|_| None).collect(),
        Some(v) if v.len() == dss.len() => {
            v.into_iter().map(|d| d.map(|d| BarsView::from_dict(&d, 60)).transpose()).collect::<PyResult<_>>()?
        }
        Some(_) => return Err(err("run_ir_portfolio: m1s needs one entry (or None) per dataset".into())),
    };
    let m1b: Vec<Option<Bars>> = m1v.iter().map(|m| m.as_ref().map(|x| x.bars())).collect();
    let bars: Vec<Bars> = dss.iter().map(|ds| ds.exec.bars()).collect();
    let evs: Vec<(&[CaEvent], &[InstrumentChange])> =
        dss.iter().map(|ds| (ds.events.as_slice(), ds.changes.as_slice())).collect();
    let out = py
        .detach(|| -> Result<engine_core::portfolio::PortfolioOutcome, String> {
            let legs: Vec<engine_core::portfolio::Leg> = bars
                .iter()
                .zip(&evs)
                .zip(&m1b)
                .map(|((b, (e, c)), m)| engine_core::portfolio::Leg { bars: b, m1: m.as_ref(), events: e, changes: c })
                .collect();
            let mut srcs: Vec<Box<dyn DecisionSource + '_>> = Vec::with_capacity(legs.len());
            for (p, sd) in plans.iter().zip(&sds) {
                srcs.push(Box::new(IrSource::live(p, 0, sd)?));
            }
            engine_core::portfolio::run_portfolio(&cfgs, &legs, &mut srcs, cash0_paise, max_positions)
        })
        .map_err(err)?;
    let comb = engine_core::portfolio::combined(&out);
    let m = metrics::compute(&comb, cash0_paise, periods_per_year);
    let d = PyDict::new(py);
    d.set_item("metrics", metrics_dict(py, &m)?)?;
    d.set_item("ts", out.ts.clone().into_pyarray(py))?;
    d.set_item("equity", out.equity.clone().into_pyarray(py))?;
    d.set_item("cash", out.cash)?;
    d.set_item("peak_open", out.peak_open)?;
    d.set_item("lookback", plans.iter().map(|p| p.variants[0].lookback).max().unwrap_or(0))?;
    d.set_item("plan_sha256", plans[0].plan_sha256.clone())?;
    let legs = PyList::empty(py);
    for mut leg in out.legs {
        for e in leg.equity.iter_mut() {
            *e += cash0_paise;
        }
        leg.cash += cash0_paise;
        legs.append(outcome_dict(py, leg, cash0_paise, periods_per_year)?)?;
    }
    d.set_item("legs", legs)?;
    Ok(d)
}

fn slot_labels(plan: &Plan) -> Vec<String> {
    let mut v = vec![String::new(); plan.n_slots];
    for n in &plan.nodes {
        let def = engine_core::ir::ops::def_of(n.op);
        for k in 0..n.nout {
            v[n.slot + k] = match def {
                Some(d) if !d.outs.is_empty() => format!("{}.{}", n.label, d.outs[k]),
                _ => n.label.clone(),
            };
        }
    }
    v
}

/// A parameter sweep: variants bound into ONE plan (shared market nodes computed once), engines in parallel.
/// Returns metrics arrays + the request index of every variant kept (constraints may remove some).
#[pyfunction]
#[pyo3(signature = (program, dataset, variants_json, spec_json, plan_json, inst_json, costs_json, cash0_paise,
                    periods_per_year, m1=None))]
#[allow(clippy::too_many_arguments)]
fn sweep_ir<'py>(
    py: Python<'py>,
    program: &Program,
    dataset: Bound<'py, PyDict>,
    variants_json: &str,
    spec_json: &str,
    plan_json: &str,
    inst_json: &str,
    costs_json: &str,
    cash0_paise: i64,
    periods_per_year: f64,
    m1: Option<Bound<'py, PyDict>>,
) -> PyResult<Bound<'py, PyDict>> {
    let ds = DatasetView::from_dict(&dataset)?;
    let mv = m1.map(|d| BarsView::from_dict(&d, 60)).transpose()?;
    let raw: Vec<serde_json::Value> = serde_json::from_str(variants_json).map_err(|e| err(format!("variants: {e}")))?;
    let variants: Vec<BTreeMap<String, f64>> =
        raw.iter().map(|v| parse_variant(Some(&v.to_string()))).collect::<PyResult<_>>()?;
    let plan = plan_for(&program.prog, &variants, &ds)?;
    let cfg = Config::from_json(spec_json, plan_json, inst_json, costs_json).map_err(err)?;
    let sd = ds.sig_data(&plan)?;
    let layout = SweepLayout::new(&plan);
    let (eb, mb) = (ds.exec.bars(), mv.as_ref().map(|x| x.bars()));
    let (events, changes) = (&ds.events, &ds.changes);
    let ms: Vec<metrics::Metrics> = py
        .detach(|| -> Result<Vec<metrics::Metrics>, String> {
            let matrix = precompute(&plan, &sd, &layout.cols, Some(&layout))?;
            (0..plan.variants.len())
                .into_par_iter()
                .map(|vi| {
                    let mut src = IrSource::pre(&plan, vi, &sd, &layout, &matrix)?;
                    cfg.run_full(cash0_paise, &eb, mb.as_ref(), events, changes, true, &mut src)
                        .map(|o| metrics::compute(&o, cash0_paise, periods_per_year))
                })
                .collect()
        })
        .map_err(err)?;
    let d = PyDict::new(py);
    metrics_columns(py, &ms, &d)?;
    d.set_item("index", plan.variants.iter().map(|v| v.index as i64).collect::<Vec<_>>().into_pyarray(py))?;
    d.set_item("rejected", plan.rejected.iter().map(|r| r.0 as i64).collect::<Vec<_>>())?;
    d.set_item("shared_nodes", layout.shared.iter().filter(|x| **x).count())?;
    d.set_item("matrix_columns", layout.cols.len())?;
    d.set_item("plan_sha256", plan.plan_sha256.clone())?;
    Ok(d)
}

/// Values of named signals for every bar (market-only signals; batch evaluation).
#[pyfunction]
#[pyo3(signature = (program, dataset, names, params_json=None))]
fn eval_signals<'py>(
    py: Python<'py>,
    program: &Program,
    dataset: Bound<'py, PyDict>,
    names: Vec<String>,
    params_json: Option<&str>,
) -> PyResult<Bound<'py, PyDict>> {
    let ds = DatasetView::from_dict(&dataset)?;
    let plan = plan_for(&program.prog, &[parse_variant(params_json)?], &ds)?;
    let v = plan.variants.first().ok_or_else(|| err("the parameters violate a constraint".into()))?;
    let mut cols = vec![];
    for name in &names {
        let slot = v
            .signals
            .iter()
            .find(|(k, _)| k == name)
            .map(|x| x.1)
            .ok_or_else(|| err(format!("no signal named '{name}'")))?;
        if plan.nodes[plan.slot_node[slot]].position {
            return Err(err(format!("signal '{name}' depends on the position; it only exists inside a run")));
        }
        cols.push(slot);
    }
    let sd = ds.sig_data(&plan)?;
    let mat = py.detach(|| precompute(&plan, &sd, &cols, None)).map_err(err)?;
    let d = PyDict::new(py);
    let k = cols.len();
    for (j, name) in names.iter().enumerate() {
        let col: Vec<f64> = (0..ds.exec.len()).map(|t| mat[t * k + j]).collect();
        d.set_item(name, col.into_pyarray(py))?;
    }
    Ok(d)
}

/// Streaming check helper: market values of named signals evaluated bar by bar from a fresh state, returned as the
/// values seen at each bar (identical to eval_signals by construction; used by the batch == streaming test).
#[pyfunction]
#[pyo3(signature = (program, dataset, names, params_json=None))]
fn stream_signals<'py>(
    py: Python<'py>,
    program: &Program,
    dataset: Bound<'py, PyDict>,
    names: Vec<String>,
    params_json: Option<&str>,
) -> PyResult<Bound<'py, PyDict>> {
    let ds = DatasetView::from_dict(&dataset)?;
    let plan = plan_for(&program.prog, &[parse_variant(params_json)?], &ds)?;
    let v = plan.variants.first().ok_or_else(|| err("the parameters violate a constraint".into()))?;
    let slots: Vec<usize> = names
        .iter()
        .map(|n| v.signals.iter().find(|(k, _)| k == n).map(|x| x.1).ok_or_else(|| err(format!("no signal '{n}'"))))
        .collect::<PyResult<_>>()?;
    let sd = ds.sig_data(&plan)?;
    let mut m = MarketEval::new(&plan, &sd).map_err(err)?;
    let mut cols: Vec<Vec<f64>> = vec![Vec::with_capacity(ds.exec.len()); names.len()];
    for t in 0..ds.exec.len() {
        m.advance(t).map_err(err)?;
        for (j, s) in slots.iter().enumerate() {
            cols[j].push(m.slots[*s]);
        }
    }
    let d = PyDict::new(py);
    for (name, col) in names.iter().zip(cols) {
        d.set_item(name, col.into_pyarray(py))?;
    }
    Ok(d)
}

// ------------------------------------------------------------------------------------------------ Python tier

struct PySource<'py> {
    cb: Bound<'py, PyAny>,
    fill_cb: Option<Bound<'py, PyAny>>,
    factor: Vec<f64>,
    price_scale: f64,
    err: Option<PyErr>,
}

fn opt_f64(d: &Bound<'_, PyDict>, k: &str) -> PyResult<Option<f64>> {
    match d.get_item(k)? {
        None => Ok(None),
        Some(x) if x.is_none() => Ok(None),
        Some(x) => Ok(Some(x.extract::<f64>()?)),
    }
}

impl PySource<'_> {
    fn raw(&self, v: f64, t: usize) -> f64 {
        v / self.factor[(t + 1).min(self.factor.len() - 1)] * self.price_scale
    }
    fn level(&self, d: &Bound<'_, PyDict>, k: &str, t: usize) -> PyResult<Ov<Level>> {
        Ok(match d.get_item(k)? {
            None => Ov::Default,
            Some(x) if x.is_none() => Ov::Default,
            Some(x) => {
                if let Ok(s) = x.extract::<String>() {
                    if s == "off" {
                        Ov::Off
                    } else {
                        return Err(err(format!("{k}: 'off' or (\"pct\"|\"price\", value)")));
                    }
                } else {
                    let (kind, v): (String, f64) = x.extract()?;
                    match kind.as_str() {
                        "pct" if v > 0.0 && v < 1.0 => Ov::Set(Level::Pct(v)),
                        "price" if v.is_finite() && v > 0.0 => Ov::Set(Level::Price(self.raw(v, t))),
                        _ => return Err(err(format!("{k}: bad level ({kind}, {v})"))),
                    }
                }
            }
        })
    }
    fn intent(&self, d: &Bound<'_, PyDict>, t: usize) -> PyResult<Intent> {
        let kind: String = d.get_item("kind")?.ok_or_else(|| err("intent needs 'kind'".into()))?.extract()?;
        let kind = match kind.as_str() {
            "enter_long" => IntentKind::EnterLong,
            "enter_short" => IntentKind::EnterShort,
            "exit_long" => IntentKind::ExitLong,
            "exit_short" => IntentKind::ExitShort,
            "exit_all" => IntentKind::ExitAll,
            k => return Err(err(format!("unknown intent kind '{k}'"))),
        };
        let mut it = Intent::entry(kind);
        if !kind.is_entry() {
            return Ok(it);
        }
        it.order = match d.get_item("order")?.map(|x| x.extract::<String>()).transpose()?.as_deref() {
            None | Some("market") => OrderType::Market,
            Some("limit") => OrderType::Limit,
            Some("stop") => OrderType::Stop,
            Some(o) => return Err(err(format!("unknown order type '{o}'"))),
        };
        it.price = opt_f64(d, "price")?.map(|p| self.raw(p, t));
        it.offset_pct = opt_f64(d, "offset_pct")?;
        if let Some(x) = d.get_item("size")?
            && !x.is_none()
        {
            let (mode, v): (String, f64) = x.extract()?;
            it.size = Some(match mode.as_str() {
                "all_in" => Sizing::AllIn,
                "pct_equity" => Sizing::PctEquity(v),
                "fixed_qty" => Sizing::FixedQty(v as i64),
                "fixed_value" => Sizing::FixedValue(v),
                "risk_pct" => Sizing::RiskPct(v),
                m => return Err(err(format!("unknown sizing '{m}'"))),
            });
        }
        it.stop = self.level(d, "stop", t)?;
        it.target = self.level(d, "target", t)?;
        let ov_num = |k: &str| -> PyResult<Ov<f64>> {
            Ok(match d.get_item(k)? {
                None => Ov::Default,
                Some(x) if x.is_none() => Ov::Default,
                Some(x) => match x.extract::<String>() {
                    Ok(s) if s == "off" => Ov::Off,
                    _ => Ov::Set(x.extract::<f64>()?),
                },
            })
        };
        it.trail_pct = ov_num("trail_pct")?;
        it.time_stop_bars = match ov_num("time_stop_bars")? {
            Ov::Set(v) => Ov::Set(v as u32),
            Ov::Off => Ov::Off,
            Ov::Default => Ov::Default,
        };
        it.expiry_bars = opt_f64(d, "expiry_bars")?.map(|v| v as u32);
        Ok(it)
    }
}

impl DecisionSource for PySource<'_> {
    fn on_fill(&mut self, bar: usize, buy: bool, qty: i64, price: i64) {
        if self.err.is_some() {
            return;
        }
        if let Some(cb) = &self.fill_cb {
            let px = price as f64 * self.factor[bar] / self.price_scale;
            if let Err(e) = cb.call1((bar, buy, qty, px)) {
                self.err = Some(e);
            }
        }
    }
    fn decide(&mut self, ctx: &BarCtx, pos: &PosState, out: &mut Vec<Intent>) -> Result<(), String> {
        let f = self.factor[ctx.t] / self.price_scale;
        let flat = pos.qty == 0;
        let nan = f64::NAN;
        let args = (
            ctx.t,
            pos.qty,
            if flat { nan } else { pos.avg_entry * f },
            pos.bars_in_trade,
            if flat { nan } else { pos.highest as f64 * f },
            if flat { nan } else { pos.lowest as f64 * f },
        );
        let r = match self.cb.call1(args) {
            Ok(r) => r,
            Err(e) => {
                self.err = Some(e);
                return Err("the strategy raised an exception".into());
            }
        };
        if r.is_none() {
            return Ok(());
        }
        let items: Vec<Bound<'_, PyAny>> = match r.extract() {
            Ok(v) => v,
            Err(e) => {
                self.err = Some(e);
                return Err("on_bar must return None or a list of intents".into());
            }
        };
        for x in items {
            let parsed = x.cast_into::<PyDict>().map_err(PyErr::from).and_then(|d| self.intent(&d, ctx.t));
            match parsed {
                Ok(it) => out.push(it),
                Err(e) => {
                    self.err = Some(e);
                    return Err("invalid intent".into());
                }
            }
        }
        Ok(())
    }
}

/// Python-tier run: `callback(t, qty, entry, bars_in_trade, highest, lowest)` is called after each bar's intrabar
/// execution and returns None or a list of intent dicts (prices in the dataset's signal scale); `on_fill(bar, buy,
/// qty, price)` (optional) sees every fill before the next decision.
#[pyfunction]
#[pyo3(signature = (callback, dataset, spec_json, plan_json, inst_json, costs_json, cash0_paise, periods_per_year,
                    m1=None, on_fill=None))]
#[allow(clippy::too_many_arguments)]
fn run_py<'py>(
    py: Python<'py>,
    callback: Bound<'py, PyAny>,
    dataset: Bound<'py, PyDict>,
    spec_json: &str,
    plan_json: &str,
    inst_json: &str,
    costs_json: &str,
    cash0_paise: i64,
    periods_per_year: f64,
    m1: Option<Bound<'py, PyDict>>,
    on_fill: Option<Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyDict>> {
    let ds = DatasetView::from_dict(&dataset)?;
    let mv = m1.map(|d| BarsView::from_dict(&d, 60)).transpose()?;
    let cfg = Config::from_json(spec_json, plan_json, inst_json, costs_json).map_err(err)?;
    let mut src = PySource {
        cb: callback,
        fill_cb: on_fill,
        factor: ds.factor.as_slice()?.to_vec(),
        price_scale: ds.price_scale as f64,
        err: None,
    };
    let mb = mv.as_ref().map(|x| x.bars());
    let res = cfg.run_full(cash0_paise, &ds.exec.bars(), mb.as_ref(), &ds.events, &ds.changes, false, &mut src);
    if let Some(e) = src.err.take() {
        return Err(e);
    }
    let out = res.map_err(err)?;
    outcome_dict(py, out, cash0_paise, periods_per_year)
}

/// Operator catalogue: {name: {"args", "optional", "kinds", "opt_kinds", "variadic", "outputs", "class", "returns"}}
/// (kinds: S numeric series, B boolean series, N number, I integer >= 1, Q number in [0, 1])
#[pyfunction]
fn operators<'py>(py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    for o in engine_core::ir::ops::OPS {
        let x = PyDict::new(py);
        x.set_item("args", o.args.len())?;
        x.set_item("optional", o.opt.len())?;
        x.set_item("kinds", o.args.iter().map(|a| format!("{a:?}")).collect::<Vec<_>>())?;
        x.set_item("opt_kinds", o.opt.iter().map(|a| format!("{:?}", a.0)).collect::<Vec<_>>())?;
        x.set_item("variadic", o.variadic)?;
        x.set_item("outputs", o.outs.to_vec())?;
        x.set_item("class", format!("{:?}", o.class).to_lowercase())?;
        x.set_item("returns", format!("{:?}", o.ret).to_lowercase())?;
        d.set_item(o.name, x)?;
    }
    Ok(d)
}

#[pymodule]
fn hbt_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add("OPS_VERSION", engine_core::ir::ops::OPS_VERSION)?;
    m.add("EXIT_REASONS", vec!["entry", "signal", "stop", "target", "trail", "time_stop", "square_off", "forced"])?;
    m.add("IRError", m.py().get_type::<IRError>())?;
    let codes: HashMap<&str, ()> = engine_core::ir::error::CODES.iter().map(|c| (*c, ())).collect();
    m.add("ERROR_CODES", codes.keys().copied().collect::<Vec<_>>())?;
    m.add_class::<Program>()?;
    m.add_function(wrap_pyfunction!(bars_from_arrow, m)?)?;
    m.add_function(wrap_pyfunction!(bars_from_arrow_multi, m)?)?;
    m.add_function(wrap_pyfunction!(run, m)?)?;
    m.add_function(wrap_pyfunction!(run_batch, m)?)?;
    m.add_function(wrap_pyfunction!(compile, m)?)?;
    m.add_function(wrap_pyfunction!(run_ir, m)?)?;
    m.add_function(wrap_pyfunction!(run_ir_portfolio, m)?)?;
    m.add_function(wrap_pyfunction!(sweep_ir, m)?)?;
    m.add_function(wrap_pyfunction!(eval_signals, m)?)?;
    m.add_function(wrap_pyfunction!(stream_signals, m)?)?;
    m.add_function(wrap_pyfunction!(run_py, m)?)?;
    m.add_function(wrap_pyfunction!(operators, m)?)?;
    Ok(())
}
