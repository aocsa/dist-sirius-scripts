#!/bin/bash
# Phase 1d (plans/partial-pin-serving-design.md): set E pins, top 5 + Q19, partial serving off then on.
#   partial_ab.sh ARM...   (ARM = off | on)
set -u
S=$(cd "$(dirname "$0")" && pwd)
OUT=/tmp/aocsa/tpch22/pin10k/partial
BIN=$(readlink -f ${SIRIUS:-/tmp/aocsa/wt/dpo-rank-pin}/sirius-duckdb/build/release/duckdb)
export PATH=$HOME/.pixi/bin:$PATH
unset AI_AGENT CLAUDECODE CLAUDE_CODE
cd ${SIRIUS:-/tmp/aocsa/wt/dpo-rank-pin}
node_free() { awk '/MemFree|FilePages/ {s+=$4} END {print int(s/1048576)}' /sys/devices/system/node/node$1/meminfo; }
for arm in "$@"; do
  cfg=$OUT/pin-E-off.yaml; [ "$arm" = on ] && cfg=$OUT/pin-E-partial.yaml
  until ! pgrep -x duckdb >/dev/null && [ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ] && [ "$(node_free 0)" -ge 350 ] && [ "$(node_free 1)" -ge 350 ]; do sleep 10; done
  rm -rf /tmp/aocsa/sirius-disk/*
  $S/mem_guard_dpo.sh $OUT/mem_$arm.log 50 & G=$!
  $S/numa_log.sh $OUT/numa_$arm.log & N=$!
  echo "$(date +%T) start $arm" >> $OUT/run.log
  pixi run python bench/dist-tpch/run_suite.py --out $OUT --tag _M155$arm${TAGSUF:-} --config $cfg --host-pool 190Gi \
    --data /scratch/sirius/datasets/tpch_sf10000 --sf 10000 --pin-tier host --ref-dir /tmp/aocsa/tpch22/dpo/sf10k/run-fix-orig \
    --pin-plans /tmp/aocsa/tpch22/pin10k/plans-v2 --iters 2 --queries ${QUERIES:-9,21,18,7,14,19} \
    --pin-only lineitem,orders,part,partsupp,supplier,nation,region --pin-replicate nation,region \
    --pin-exclude l_partkey,l_quantity,l_tax,l_returnflag,l_linestatus,l_shipdate,l_commitdate,l_receiptdate,l_shipinstruct,l_shipmode,o_comment,o_totalprice,o_orderpriority,o_shippriority,p_name,p_container \
    >> $OUT/run.log 2>&1
  echo "$(date +%T) run_suite $arm exit $?" >> $OUT/run.log
  kill $G $N
  for p in $(pgrep -x duckdb); do [ "$(readlink -f /proc/$p/exe 2>/dev/null)" = "$BIN" ] && kill -9 $p; done
done
echo "$(date +%T) done $*" >> $OUT/run.log
