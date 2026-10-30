#!/usr/bin/env bash
# Runs TPC-H query Q distributed on 4 ranks per host, one per GPU, over HOSTS.
# Run it on the first host. The hosts must share this directory, the build, the data and the
# output directory (e.g. on GPFS), and reach each other over SSH.
#   xnode_tpch.sh <q> <out dir>
# Env: SF (100), DATA (/scratch/sirius/datasets/tpch_sf$SF), ITERS (2), PORT (27200: rank r listens
# on PORT+r), CONFIG (../configs/p180.yaml), TIMEOUT (1800 s per rank), REF (an answer log: rank 0's
# of an earlier run, or cpu_ref.sh's),
# HOSTS ("10.87.140.52 10.87.140.53": gcn-17 and gcn-18 on bond0), SPILL_ROOT (a directory per host
# under it, cleared before and after the run), WT (the Sirius checkout and build).
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
q=$1
out=$(realpath -m "$2")/q$(printf %02d "$q")
sf=${SF:-100}
data=${DATA:-/scratch/sirius/datasets/tpch_sf$sf}
iters=${ITERS:-2}
port=${PORT:-27200}
config=${CONFIG:-$here/../configs/p180.yaml}
export WT=${WT:-/scratch/prestouser/aocsa/wt/xnode-staging}
export TIMEOUT=${TIMEOUT:-1800}
read -r -a hosts <<< "${HOSTS:-10.87.140.52 10.87.140.53}"
per_host=4
spill_root=${SPILL_ROOT:-/scratch/prestouser/aocsa/sirius-spill-xnode}
bin=$WT/sirius-duckdb/build/release/duckdb
pixi=$HOME/.pixi/bin/pixi

on() { # on <host> <command>: runs locally for this host's address, else over SSH.
  if ip -4 -o addr show | grep -q " $1/"; then bash -c "$2"; else ssh -o BatchMode=yes "$1" "$2"; fi
}
quiet() { # No duckdb from anyone, no GPU process, and gcn-18's quiet.sh passes.
  local h
  for h in "${hosts[@]}"; do
    on "$h" 'pgrep -x duckdb >/dev/null && exit 1
      [ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ] || exit 1
      q=/tmp/aocsa/tpch22/sf30k/scripts/quiet.sh; [ ! -x $q ] || $q >/dev/null' || return 1
  done
}
reap() { # Kills this build's leftover ranks on both hosts, nobody else's.
  local h
  for h in "${hosts[@]}"; do
    on "$h" "for p in \$(pgrep -u \$(id -u) -x duckdb); do
      [ \"\$(readlink -f /proc/\$p/exe 2>/dev/null)\" = \"$bin\" ] && kill -9 \$p; done; true"
  done
}

w=0
until quiet; do
  sleep 10; w=$((w + 10))
  [ $w -ge 600 ] && { echo "Q$q: hosts not quiet after 10 min"; exit 1; }
done

rm -rf "$out"; mkdir -p "$out"
python3 "$here/gen_sql.py" "$q" "$sf" "$data" "$iters" > "$out/q.sql" || exit 3
peers=""
for ((h = 0; h < ${#hosts[@]}; h++)); do
  for ((g = 0; g < per_host; g++)); do
    peers+="${peers:+,}${hosts[$h]}:$((port + h * per_host + g))"
  done
done
for h in "${hosts[@]}"; do
  mkdir -p "$spill_root/$h"; rm -rf "${spill_root:?}/$h"/*
  sed "s|@SPILL_DIR@|$spill_root/$h|" "$config" > "$out/sirius-$h.yaml"
done
echo "Q$q SF$sf on 8 ranks: $peers" | tee "$out/launch.txt"

t0=$(date +%s.%N)
pids=()
for ((h = 0; h < ${#hosts[@]}; h++)); do
  hh=${hosts[$h]}
  on "$hh" "cd $WT && WT=$WT TIMEOUT=$TIMEOUT $pixi run --manifest-path $WT/pixi.toml bash $here/host_ranks.sh \
    $((h * per_host)) $per_host '$peers' $out/q.sql $out/sirius-$hh.yaml $out" \
    > "$out/host-$hh.log" 2>&1 &
  pids+=($!)
done
rc=0
for p in "${pids[@]}"; do wait "$p" || rc=1; done
t1=$(date +%s.%N)
reap
spill=$(du -sb "$spill_root" | cut -f1)
for h in "${hosts[@]}"; do rm -rf "${spill_root:?}/$h"/*; done
spill=$((spill - $(du -sb "$spill_root" | cut -f1)))
python3 "$here/summarize.py" "$out" "$q" "$sf" "$rc" "$(echo "$t1 - $t0" | bc)" "$spill" "${REF:-}"
exit $rc
