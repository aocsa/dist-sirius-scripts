#!/bin/bash
# run_q5.sh SQL CFG OUTDIR [ranks=4]: one SQL file (e.g. sql/q5_scratch_sf10000_x3.sql, Q5 three
# times) on N ranks with the dist-plan-opt build; prints rank 0's timings, answer and any errors,
# then empties the spill dir. Ranks run in OUTDIR, so Quent telemetry (on by default,
# `telemetry.output_directory: telemetry_data`) lands in OUTDIR/telemetry_data/<rank>/ for
# tools/pipelines.py, taskwait.py, scanbytes.py and netrate.py.
# Env: SIRIUS_REPO (default /tmp/aocsa/wt/dist-plan-opt), SPILL_DIR (default /tmp/aocsa/sirius-disk;
# must match downgrade_root_dirs in CFG).
set -u
sql=$(realpath "$1"); cfg=$(realpath "$2"); out=$(realpath -m "$3"); ranks=${4:-4}
R=${SIRIUS_REPO:-${REPO:-/tmp/aocsa/wt/dist-plan-opt}}
spill=${SPILL_DIR:-/tmp/aocsa/sirius-disk}
if pgrep -x duckdb >/dev/null; then echo "duckdb still running"; pgrep -ax duckdb; exit 1; fi
rm -rf "${spill:?}"/*; mkdir -p $out
export PATH=$HOME/.pixi/bin:$PATH
unset AI_AGENT CLAUDECODE CLAUDE_CODE
cd $out && SIRIUS_CONFIG_FILE=$cfg SIRIUS_LOG_DIR=$out/logs EXCHANGE_TIMEOUT=1500 \
  pixi run --manifest-path $R $R/scripts/exchange_sql_ranks.sh $ranks $sql 31000 $out > $out/stdout.txt 2>&1
echo "rc=$?"
grep -h "Run Time" $out/exchange_rank_0.log
grep -v -E '^\[|no version|WARNING|^estimate' $out/exchange_rank_0.log | head -8
grep -h -i -E "error|exception|timeout" $out/exchange_rank_*.log | head -5
echo "spill: $(du -sh "$spill" | cut -f1)"; rm -rf "${spill:?}"/*
