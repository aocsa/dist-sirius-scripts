#!/bin/bash
# Control: Q1, Q6, Q14, Q17, Q19 in one run_suite process with no pins, set E config, 190Gi pool.
set -u
S=$(cd "$(dirname "$0")" && pwd)
OUT=/tmp/aocsa/tpch22/pin10k
BIN=$(readlink -f ${SIRIUS:-/tmp/aocsa/wt/dpo-rank-pin}/sirius-duckdb/build/release/duckdb)
export PATH=$HOME/.pixi/bin:$PATH
unset AI_AGENT CLAUDECODE CLAUDE_CODE
cd ${SIRIUS:-/tmp/aocsa/wt/dpo-rank-pin}
node_free() { awk '/MemFree|FilePages/ {s+=$4} END {print int(s/1048576)}' /sys/devices/system/node/node$1/meminfo; }
until ! pgrep -x duckdb >/dev/null && [ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ] && [ "$(node_free 0)" -ge 350 ] && [ "$(node_free 1)" -ge 350 ]; do sleep 10; done
rm -rf /tmp/aocsa/sirius-disk/*
# CPU memory in use every 2 s (pin growth, query peaks); kills this build's ranks below 50 GB free.
$S/mem_guard_dpo.sh $OUT/memC5.log 50 & G=$!
$S/numa_log.sh $OUT/numaC5.log & N=$!
trap "kill $G $N" EXIT
echo "$(date +%T) start" > $OUT/C5.log
pixi run python bench/dist-tpch/run_suite.py --out $OUT --tag _C5 --config $OUT/pin-E.yaml --host-pool 190Gi \
  --data /scratch/sirius/datasets/tpch_sf10000 --sf 10000 --pin-tier host --ref-dir /tmp/aocsa/tpch22/dpo/sf10k/run-fix-orig \
  --iters 2 --queries 1,6,14,17,19 --pin-skip lineitem,part >> $OUT/C5.log 2>&1
echo "$(date +%T) run_suite exit $?" >> $OUT/C5.log
for p in $(pgrep -x duckdb); do [ "$(readlink -f /proc/$p/exe 2>/dev/null)" = "$BIN" ] && kill -9 $p; done
echo "$(date +%T) done" >> $OUT/C5.log
