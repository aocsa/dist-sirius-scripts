#!/usr/bin/env bash
# TPC-H SF100000 on 8 ranks over gcn-17 and gcn-18, each query in fresh processes, run twice, with
# the NUMA guard on both hosts. No reference answers exist: sanity_sf100k.py checks them.
#   run_sf100k.sh <out dir> <q>...
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=$(realpath -m "$1")
shift
read -r -a hosts <<< "${HOSTS:-10.87.140.52 10.87.140.53}"
export HOSTS="${hosts[*]}"
mkdir -p "$out"

guards=()
for h in "${hosts[@]}"; do
  ssh -o BatchMode=yes "$h" "setsid nohup bash $here/numa_guard.sh $out/numa-$h.log 15 >/dev/null 2>&1 & echo \$!" \
    > "$out/guard-$h.pid"
  guards+=("$h:$(cat "$out/guard-$h.pid")")
done
stop_guards() {
  local g
  for g in "${guards[@]}"; do ssh -o BatchMode=yes "${g%%:*}" "kill ${g#*:} 2>/dev/null; true"; done
}
trap stop_guards EXIT

for q in "$@"; do
  echo "$(date +%T) Q$q start" >> "$out/run.log"
  SF=100000 DATA=${DATA:-/scratch/prestouser/aocsa/tpch_sf100000} TIMEOUT=${TIMEOUT:-7200} ITERS=2 \
    "$here/xnode_tpch.sh" "$q" "$out" >> "$out/run.log" 2>&1 \
    || echo "$(date +%T) Q$q failed" >> "$out/run.log"
  echo "$(date +%T) Q$q end" >> "$out/run.log"
done
