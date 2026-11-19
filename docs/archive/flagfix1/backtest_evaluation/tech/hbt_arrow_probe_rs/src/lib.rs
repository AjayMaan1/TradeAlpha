//! Probe: consume ANY object implementing __arrow_c_stream__ (DuckDB relation, Polars frame) in Rust, zero-copy.
use arrow_array::{Array, Float64Array, Int64Array};
use pyo3::prelude::*;
use pyo3_arrow::PyRecordBatchReader;

/// Stream record batches; return (rows, batches, sum(close), min(ts), max(ts)) computed on the Arrow buffers.
#[pyfunction]
fn scan(py: Python<'_>, stream: PyRecordBatchReader) -> PyResult<(u64, u64, f64, i64, i64)> {
    let reader = stream.into_reader()?;
    py.detach(|| {
        let (mut rows, mut batches, mut sum, mut lo, mut hi) = (0u64, 0u64, 0.0f64, i64::MAX, i64::MIN);
        for b in reader {
            let b = b.map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            let ts = b.column_by_name("ts").unwrap().as_any().downcast_ref::<Int64Array>().unwrap();
            let c = b.column_by_name("close").unwrap().as_any().downcast_ref::<Float64Array>().unwrap();
            for v in c.values().iter() { sum += *v; }
            for v in ts.values().iter() { lo = lo.min(*v); hi = hi.max(*v); }
            rows += b.num_rows() as u64; batches += 1;
        }
        Ok((rows, batches, sum, lo, hi))
    })
}
#[pymodule]
fn hbt_arrow_probe(m: &Bound<'_, PyModule>) -> PyResult<()> { m.add_function(wrap_pyfunction!(scan, m)?)?; Ok(()) }
