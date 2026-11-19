# Audit 2 evidence (see /workspace/AUDIT_2_REPORT.md) and Phase 1.3 design checks

| File | Run | Shows |
|---|---|---|
| e_core_evidence.py → e_core_evidence.json | `source ~/venvs/h314/bin/activate && python e_core_evidence.py` | core findings X-01…X-13 (A2-H1…H4, A2-M1…M6, A2-L1) |
| e_app_evidence.py | copy into `historify/tests/integration/`, run with pytest (uses the suite fixtures) | A2-M8 failed restore leaves no meta DB; A2-L2 empty calendar sync recorded as success |
| p13_evaluator_proto/ (result.txt) | `cargo run --release` | streaming DAG evaluator: 213–284 M node-updates/s; 256-variant EMA sharing ×4.4 |
| p13_talib_conventions.py → .json | `~/venvs/c_3.14_TA_Lib/bin/python p13_talib_conventions.py` | streaming EMA/SMA/RSI/ATR == TA-Lib (NaN pattern identical, rel ≤ 4.8e-15) |
