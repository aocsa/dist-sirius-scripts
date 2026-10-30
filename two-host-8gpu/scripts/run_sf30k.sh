#!/usr/bin/env bash
# TPC-H SF30000 on 8 ranks over gcn-17 and gcn-18, each query in fresh processes, run twice, with
# the NUMA guard on both hosts. Answers are checked against REF_DIR/q<NN>_sf30000.log (default
# ../refs/sf30k, not in git): rank 0 logs of an earlier run, here the 4-GPU SF30000 run's.
#   run_sf30k.sh <out dir> <q>...
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=$(realpath -m "$1")
shift
read -r -a hosts <<< "${HOSTS:-10.87.140.52 10.87.140.53}"
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
  qq=$(printf %02d "$q")
  echo "$(date +%T) Q$q start" >> "$out/run.log"
  SF=30000 DATA=${DATA:-/scratch/prestouser/aocsa/tpch_sf30000} TIMEOUT=${TIMEOUT:-3600} ITERS=2 \
    REF=${REF_DIR:-$here/../refs/sf30k}/q${qq}_sf30000.log "$here/xnode_tpch.sh" "$q" "$out" >> "$out/run.log" 2>&1 \
    || echo "$(date +%T) Q$q failed" >> "$out/run.log"
  echo "$(date +%T) Q$q end" >> "$out/run.log"
done
