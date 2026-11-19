#!/bin/bash
# For each candidate: fresh venv on given python, install project lock + candidate, record result.
PY=$1
REQ=/workspace/historify-master/historify/requirements.txt
OUT=/tmp/eval/install_$PY.txt
: > $OUT
for pkg in "vectorbt" "backtesting" "backtrader" "nautilus_trader" "bt" "zipline-reloaded" "lib-pybroker" "qstrader" "hftbacktest" "quantstats" "empyrical-reloaded" "pyfolio-reloaded" "optuna" "TA-Lib" "pandas-ta" "polars" "numba" "riskfolio-lib"; do
  d=~/venvs/c_${PY}_$(echo $pkg | tr -c 'a-zA-Z0-9\n' '_')
  rm -rf $d; uv venv $d --python $PY -q
  res=$(VIRTUAL_ENV=$d timeout 600 uv pip install -q -r $REQ "$pkg" 2>&1 | tail -3 | tr '\n' ' ')
  if [ -z "$res" ]; then
    v=$(VIRTUAL_ENV=$d uv pip list 2>/dev/null | grep -i "^$(echo $pkg | tr '_' '-') " | head -1)
    echo "OK   | $pkg | $v" >> $OUT
  else
    echo "FAIL | $pkg | $res" >> $OUT
  fi
done
echo DONE >> $OUT
