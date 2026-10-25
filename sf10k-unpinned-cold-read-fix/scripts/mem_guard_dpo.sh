#!/usr/bin/env bash
# Log CPU memory every 2 s; if free CPU memory drops below MIN_GB, kill this build's duckdb ranks.
# CPU memory = the NUMA nodes that have CPUs. On GB200 the GPUs' HBM is also listed as memory
# (CPU-less nodes), so /proc/meminfo's totals would overstate it.
# usage: mem_guard.sh LOG [MIN_GB=60]
LOG=$1; MIN_GB=${2:-60}
BIN=$(readlink -f ${SIRIUS:-/tmp/aocsa/wt/dpo-rank-pin}/sirius-duckdb/build/release/duckdb)
cpu_nodes=$(for n in /sys/devices/system/node/node*; do [ -n "$(cat $n/cpulist)" ] && echo $n; done)
while true; do
  free=0; total=0
  for n in $cpu_nodes; do
    f=$(awk '/MemFree|FilePages/ {s+=$4} END {print int(s/1048576)}' $n/meminfo)
    t=$(awk '/MemTotal/ {print int($4/1048576)}' $n/meminfo)
    free=$((free + f)); total=$((total + t))
  done
  echo "$(date +%T) cpu_used=$((total - free))G cpu_free=${free}G" >> "$LOG"
  if [ "$free" -lt "$MIN_GB" ]; then
    for p in $(pgrep -x duckdb); do
      [ "$(readlink -f /proc/$p/exe 2>/dev/null)" = "$BIN" ] && kill -9 "$p" && echo "$(date +%T) GUARD killed $p (cpu_free ${free}G)" >> "$LOG"
    done
  fi
  sleep 2
done
