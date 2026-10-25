#!/bin/bash
# Stage 2 (plans/pin-sf10k-plan.md): SF3000 all-pinned on the dpo build, repo plans then v2 plans.
set -u
S=$(cd "$(dirname "$0")" && pwd)
OUT=/tmp/aocsa/tpch22/pin10k/sf3k-v2
BIN=$(readlink -f ${SIRIUS:-/tmp/aocsa/wt/dpo-rank-pin}/sirius-duckdb/build/release/duckdb)
export PATH=$HOME/.pixi/bin:$PATH
unset AI_AGENT CLAUDECODE CLAUDE_CODE
cd ${SIRIUS:-/tmp/aocsa/wt/dpo-rank-pin}
node_free() { awk '/MemFree|FilePages/ {s+=$4} END {print int(s/1048576)}' /sys/devices/system/node/node$1/meminfo; }
for arm in repo:src/compression/simpatico_codegen/plans/tpch_sf1000 v2:/tmp/aocsa/tpch22/pin10k/plans-v2; do
  tag=${arm%%:*}; plans=${arm#*:}
  until ! pgrep -x duckdb >/dev/null && [ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ] && [ "$(node_free 0)" -ge 350 ] && [ "$(node_free 1)" -ge 350 ]; do sleep 10; done
  rm -rf /tmp/aocsa/sirius-disk/*
  $S/mem_guard_dpo.sh $OUT/mem_$tag.log 50 & G=$!
  $S/numa_log.sh $OUT/numa_$tag.log & N=$!
  echo "$(date +%T) start $tag" >> $OUT/run.log
  pixi run python bench/dist-tpch/run_suite.py --out $OUT --tag _$tag --config $OUT/host-pin-d15.yaml --host-pool 210Gi \
    --data /scratch/sirius/datasets/tpch_sf3000 --sf 3000 --pin-tier host --pin-plans $plans \
    --pin-exclude o_comment --pin-replicate nation,region,supplier --ref-dir /tmp/aocsa/tpch22/dpo/sf3k-hostpin \
    >> $OUT/run.log 2>&1
  echo "$(date +%T) run_suite $tag exit $?" >> $OUT/run.log
  kill $G $N
  for p in $(pgrep -x duckdb); do [ "$(readlink -f /proc/$p/exe 2>/dev/null)" = "$BIN" ] && kill -9 $p; done
done
echo "$(date +%T) done" >> $OUT/run.log
