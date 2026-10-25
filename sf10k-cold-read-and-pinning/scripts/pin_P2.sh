#!/bin/bash
# Stage 3 group P2 (plans/pin-sf10k-plan.md): Q2, Q4, Q11, Q12, Q16, Q21 at SF10000, host, plans-v2, set E config, 190Gi.
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
# CPU memory in use every 2 s; kills this build's ranks below 50 GB free.
$S/mem_guard_dpo.sh $OUT/mem_P2.log 50 & G=$!
$S/numa_log.sh $OUT/numa_P2.log & N=$!
trap "kill $G $N" EXIT
echo "$(date +%T) start" > $OUT/P2.log
pixi run python bench/dist-tpch/run_suite.py --out $OUT --tag _P2 --config $OUT/pin-E.yaml --host-pool 190Gi \
  --data /scratch/sirius/datasets/tpch_sf10000 --sf 10000 --pin-tier host --ref-dir /tmp/aocsa/tpch22/dpo/sf10k/run-fix-orig \
  --pin-plans /tmp/aocsa/tpch22/pin10k/plans-v2 --iters 2 --queries 2,4,11,12,16,21 --pin-replicate nation,region \
  >> $OUT/P2.log 2>&1
echo "$(date +%T) run_suite exit $?" >> $OUT/P2.log
for p in $(pgrep -x duckdb); do [ "$(readlink -f /proc/$p/exe 2>/dev/null)" = "$BIN" ] && kill -9 $p; done
echo "$(date +%T) done" >> $OUT/P2.log
