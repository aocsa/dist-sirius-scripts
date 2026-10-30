#!/usr/bin/env bash
# Logs free CPU memory (MemFree+FilePages) per CPU NUMA node every 2 s; if any node drops below
# MIN_GB, kills this build's duckdb ranks on this host.   usage: numa_guard.sh LOG [MIN_GB=15]
LOG=$1
MIN_GB=${2:-15}
BIN=$(readlink -f "${WT:-/scratch/prestouser/aocsa/wt/xnode-staging}/sirius-duckdb/build/release/duckdb")
cpu_nodes=$(for n in /sys/devices/system/node/node*; do [ -n "$(cat "$n/cpulist")" ] && echo "$n"; done)
while true; do
  line="$(date +%T)"
  low=0
  for n in $cpu_nodes; do
    f=$(awk '/MemFree|FilePages/ {s+=$4} END {print int(s/1048576)}' "$n/meminfo")
    line="$line $(basename "$n")_free=${f}G"
    [ "$f" -lt "$MIN_GB" ] && low=1
  done
  echo "$line" >> "$LOG"
  if [ $low = 1 ]; then
    for p in $(pgrep -u "$(id -u)" -x duckdb); do
      [ "$(readlink -f /proc/$p/exe 2>/dev/null)" = "$BIN" ] && kill -9 "$p" && echo "$(date +%T) GUARD killed $p" >> "$LOG"
    done
  fi
  sleep 2
done
