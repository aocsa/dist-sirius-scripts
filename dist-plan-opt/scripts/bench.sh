#!/bin/bash
# bench.sh NAME RANKS CFG QUERIES : one run22_dpo.py invocation with GPU hygiene (refuses to start
# while a duckdb rank is left over; empties the spill dir before and after). A running
# gen_expected.py is paused meanwhile (it competes for the same GPFS bandwidth and cores).
# Env (defaults = the 4x GB200 box): SF=3000, DATA=/scratch/sirius/datasets/tpch_sf$SF,
# EXPECTED=$WORK/expected/sf$SF (used only if it holds all 22 answers), RUNS=$WORK/runs,
# SPILL_DIR=/tmp/aocsa/sirius-disk (must match downgrade_root_dirs in CFG), WORK=/tmp/aocsa/tpch22,
# SIRIUS_REPO (see tools/run22_dpo.py).
set -u
name=$1; ranks=$2; cfg=$(realpath "$3"); qs=$4
here=$(cd "$(dirname "$0")" && pwd)
: "${WORK:=/tmp/aocsa/tpch22}" "${SF:=3000}" "${SPILL_DIR:=/tmp/aocsa/sirius-disk}"
: "${DATA:=/scratch/sirius/datasets/tpch_sf$SF}" "${EXPECTED:=$WORK/expected/sf$SF}" "${RUNS:=$WORK/runs}"
gen=$(pgrep -f 'python[0-9.]* .*gen_expected.py')
if [ -n "$gen" ]; then kill -STOP $gen; trap 'kill -CONT $gen' EXIT; fi
if pgrep -x duckdb >/dev/null; then echo "duckdb still running:"; pgrep -ax duckdb; exit 1; fi
rm -rf "${SPILL_DIR:?}"/*
export PATH=$HOME/.pixi/bin:$PATH
exp=""; [ "$(ls "$EXPECTED" 2>/dev/null | wc -l)" -eq 22 ] && exp="--expected $EXPECTED"
python3 "$here/../tools/run22_dpo.py" --data "$DATA" --sf "$SF" --config "$cfg" \
  --out "$RUNS/$name" --ranks "$ranks" --iters 3 --timeout 1500 --queries "$qs" $exp
echo "spill used: $(du -sh "$SPILL_DIR" 2>/dev/null | cut -f1)"
rm -rf "${SPILL_DIR:?}"/*
