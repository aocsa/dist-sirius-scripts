#!/bin/bash
# Run TPC-H SF30000 queries on 4 ranks, each query/arm in its own fresh processes.
#   sf30k_ab.sh <out dir> <iters> <query>:<arm>[,<arm>...]...
# Arm X uses OUT/arm-X.yaml; optional OUT/arm-X.args holds extra run_query.py args
# (e.g. --host-pool 180Gi --spill-dir /scratch/...). Defaults: --host-pool 210Gi, spill /tmp/aocsa/sirius-disk.
# No reference answers exist at SF30000: run_query.py's same_as_first compares run 2 against run 1.
set -u
S=$(cd "$(dirname "$0")" && pwd)
O=$1; ITERS=$2; shift 2
SIRIUS=${SIRIUS:-/tmp/aocsa/wt/dpo-rank-pin}
BIN=$(readlink -f $SIRIUS/sirius-duckdb/build/release/duckdb)
export PATH=$HOME/.pixi/bin:$PATH
unset AI_AGENT CLAUDECODE CLAUDE_CODE
cd $SIRIUS
mkdir -p $O
note() { echo "$(date +%T) $*" | tee -a $O/run.log.txt; }
node_free() { awk '/MemFree|FilePages/ {s+=$4} END {print int(s/1048576)}' /sys/devices/system/node/node$1/meminfo; }
quiet() { ! pgrep -x tpchgen-cli >/dev/null && ! pgrep -x duckdb >/dev/null && [ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ] && [ "$(node_free 0)" -ge 350 ] && [ "$(node_free 1)" -ge 350 ]; }
$S/numa_guard.sh $O/numa_mem.log 15 & G=$!
trap "kill $G" EXIT
n=$(ls -d $O/r*-* 2>/dev/null | wc -l)
for spec in "$@"; do
  q=${spec%%:*}
  for arm in $(tr ',' ' ' <<< "${spec#*:}"); do
    n=$((n + 1)); w=0
    until quiet; do sleep 10; w=$((w+10)); [ $w -ge 600 ] && { note "Q$q: host not quiet after 10 min"; exit 1; }; done
    extra=$(cat $O/arm-$arm.args 2>/dev/null || true)
    spill=$(grep -o -- '--spill-dir [^ ]*' <<< "$extra" | cut -d' ' -f2); spill=${spill:-/tmp/aocsa/sirius-disk}
    mkdir -p $spill; rm -rf $spill/*
    out=$O/r$n-$arm; mkdir -p $out
    note "Q$q $arm r$n start ($extra)"
    # SF / DATA / REF_DIR (a run dir with qNN/exchange_rank_0.log answers) select another scale.
    ref=""; [ -n "${REF_DIR:-}" ] && ref="--ref $REF_DIR/q$(printf %02d $q)/exchange_rank_0.log"
    pixi run python bench/dist-tpch/run_query.py $q --out $out --sf ${SF:-30000} --ranks 4 --iters $ITERS \
      --config $O/arm-$arm.yaml --host-pool 210Gi --data ${DATA:-/scratch/prestouser/aocsa/tpch_sf30000} \
      --timeout 3600 --tag _sf${SF:-30000} $ref $extra >> $O/run.log 2>&1 || note "Q$q $arm failed"
    for p in $(pgrep -x duckdb); do [ "$(readlink -f /proc/$p/exe 2>/dev/null)" = "$BIN" ] && kill -9 $p; done
    rm -rf $spill/*
    errs=$(cat $out/q*/exchange_rank_*.log 2>/dev/null | grep -c "Error:")
    note "Q$q $arm r$n errors=$errs $(tail -1 $out/results.jsonl 2>/dev/null | python3 -c 'import json,sys; r=json.loads(sys.stdin.read()); print("rc=%s times=%s rows=%s same=%s ref=%s spill=%s" % (r.get("rc"), r.get("times_s"), r.get("rows"), r.get("same_as_first"), r.get("vs_ref"), r.get("spill_bytes")))')"
  done
done
note "sf30k A/B done"
