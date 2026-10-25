#!/bin/bash
# Run TPC-H SF3000 queries on 4 ranks, each query/arm in its own fresh processes; arm X uses OUT/direct-io-spread-X.yaml.
#   spread_ab_sf3k.sh <out dir> <iters> <query>:<arm>[,<arm>...]...   e.g. spread_ab_sf3k.sh /tmp/x 2 9:base,cap4m 21:base,cap4m
set -u
S=$(cd "$(dirname "$0")" && pwd)
O=$1; ITERS=$2; shift 2
REF=/tmp/aocsa/tpch22/dpo/sf3k-hostpin
BIN=$(readlink -f ${SIRIUS:-/tmp/aocsa/wt/dpo-rank-pin}/sirius-duckdb/build/release/duckdb)
export PATH=$HOME/.pixi/bin:$PATH
unset AI_AGENT CLAUDECODE CLAUDE_CODE
cd ${SIRIUS:-/tmp/aocsa/wt/dpo-rank-pin}
mkdir -p $O
for arm in on off; do
  [ -f $O/direct-io-spread-$arm.yaml ] && continue
  v=$([ $arm = on ] && echo true || echo false)
  sed "s|      cache: { mode: none }|      cache: { mode: none }\n      uring: { spread_reads: $v }|" bench/dist-tpch/direct-io.yaml > $O/direct-io-spread-$arm.yaml
done
note() { echo "$(date +%T) $*" | tee -a $O/run.log.txt; }
node_free() { awk '/MemFree|FilePages/ {s+=$4} END {print int(s/1048576)}' /sys/devices/system/node/node$1/meminfo; }
quiet() { ! pgrep -x tpchgen-cli >/dev/null && ! pgrep -x duckdb >/dev/null && [ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ] && [ "$(node_free 0)" -ge 350 ] && [ "$(node_free 1)" -ge 350 ]; }
$S/mem_guard_dpo.sh $O/mem_guard.log 50 & G=$!
trap "kill $G" EXIT
n=0
for spec in "$@"; do
  q=${spec%%:*}; qq=$(printf %02d $q)
  for arm in $(tr ',' ' ' <<< "${spec#*:}"); do
    n=$((n + 1)); w=0
    until quiet; do sleep 10; w=$((w+10)); [ $w -ge 600 ] && { note "Q$q: host not quiet after 10 min"; exit 1; }; done
    rm -rf /tmp/aocsa/sirius-disk/*
    out=$O/r$n-$arm; mkdir -p $out
    pixi run python bench/dist-tpch/run_query.py $q --out $out --sf 3000 --ranks 4 --iters $ITERS \
      --config $O/direct-io-spread-$arm.yaml --host-pool 210Gi --data /scratch/sirius/datasets/tpch_sf3000 \
      --tag _direct --ref $REF/q${qq}_direct/exchange_rank_0.log >> $O/run.log 2>&1 || note "Q$q $arm failed"
    for p in $(pgrep -x duckdb); do [ "$(readlink -f /proc/$p/exe 2>/dev/null)" = "$BIN" ] && kill -9 $p; done
    note "Q$q $arm r$n $(tail -1 $out/results.jsonl | python3 -c 'import json,sys; r=json.loads(sys.stdin.read()); print("rc=%s times=%s ref=%s" % (r.get("rc"), r.get("times_s"), r.get("vs_ref")))')"
  done
done
note "spread A/B done"
