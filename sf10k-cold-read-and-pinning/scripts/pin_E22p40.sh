#!/bin/bash
# Set E pins, all 22 queries, partial serving on (min fraction 0.4), 190Gi pool.
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
$S/mem_guard_dpo.sh $OUT/mem_E22p40.log 50 & G=$!
$S/numa_log.sh $OUT/numa_E22p40.log & N=$!
trap "kill $G $N" EXIT
echo "$(date +%T) start" > $OUT/E22p40.log
pixi run python bench/dist-tpch/run_suite.py --out $OUT --tag _E22p40 --config $OUT/partial/pin-E-partial40.yaml --host-pool 190Gi \
  --data /scratch/sirius/datasets/tpch_sf10000 --sf 10000 --pin-tier host --ref-dir /tmp/aocsa/tpch22/dpo/sf10k/run-fix-orig \
  --pin-plans /tmp/aocsa/tpch22/dpo/plans-sf10k --iters 2 \
  --pin-only lineitem,orders,partsupp,supplier,nation,region --pin-replicate nation,region \
  --pin-exclude l_partkey,l_quantity,l_extendedprice,l_discount,l_tax,l_returnflag,l_linestatus,l_shipdate,l_shipinstruct,l_shipmode,o_comment \
  >> $OUT/E22p40.log 2>&1
echo "$(date +%T) run_suite exit $?" >> $OUT/E22p40.log
for p in $(pgrep -x duckdb); do [ "$(readlink -f /proc/$p/exe 2>/dev/null)" = "$BIN" ] && kill -9 $p; done
echo "$(date +%T) done" >> $OUT/E22p40.log
