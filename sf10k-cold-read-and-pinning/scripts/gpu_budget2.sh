#!/bin/bash
# GPU-pin cost: set E's host pins plus a lineitem copy on the GPU that no top-5 scan can use; top 5, 2 iterations.
set -u
S=$(cd "$(dirname "$0")" && pwd)
OUT=/tmp/aocsa/tpch22/pin10k/gpu-budget; mkdir -p $OUT
BIN=$(readlink -f ${SIRIUS:-/tmp/aocsa/wt/dpo-rank-pin}/sirius-duckdb/build/release/duckdb)
export PATH=$HOME/.pixi/bin:$PATH
unset AI_AGENT CLAUDECODE CLAUDE_CODE
cd ${SIRIUS:-/tmp/aocsa/wt/dpo-rank-pin}
node_free() { awk '/MemFree|FilePages/ {s+=$4} END {print int(s/1048576)}' /sys/devices/system/node/node$1/meminfo; }
for arm in g40e:l_extendedprice; do
  tag=${arm%%:*}; gcols=${arm#*:}
  extra=(); [ -n "$gcols" ] && extra=(--pin-extra "lineitem_gpu:lineitem:gpu:$gcols")
  until ! pgrep -x duckdb >/dev/null && [ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ] && [ "$(node_free 0)" -ge 350 ] && [ "$(node_free 1)" -ge 350 ]; do sleep 10; done
  rm -rf /tmp/aocsa/sirius-disk/*
  $S/mem_guard_dpo.sh $OUT/mem_$tag.log 50 & G=$!
  $S/numa_log.sh $OUT/numa_$tag.log & N=$!
  echo "$(date +%T) start $tag ${extra[*]}" >> $OUT/run.log
  pixi run python bench/dist-tpch/run_suite.py --out $OUT --tag _$tag --config /tmp/aocsa/tpch22/pin10k/pin-E.yaml --host-pool 190Gi \
    --data /scratch/sirius/datasets/tpch_sf10000 --sf 10000 --pin-tier host --ref-dir /tmp/aocsa/tpch22/dpo/sf10k/run-fix-orig \
    --pin-plans /tmp/aocsa/tpch22/pin10k/plans-sf10k-g --iters 2 --queries 9,21,18,7,14 \
    --pin-only lineitem,orders,partsupp,supplier,nation,region --pin-replicate nation,region \
    --pin-exclude l_partkey,l_quantity,l_extendedprice,l_discount,l_tax,l_returnflag,l_linestatus,l_shipdate,l_shipinstruct,l_shipmode,o_comment \
    "${extra[@]}" >> $OUT/run.log 2>&1
  echo "$(date +%T) run_suite $tag exit $?" >> $OUT/run.log
  kill $G $N
  for p in $(pgrep -x duckdb); do [ "$(readlink -f /proc/$p/exe 2>/dev/null)" = "$BIN" ] && kill -9 $p; done
done
echo "$(date +%T) done2" >> $OUT/run.log
