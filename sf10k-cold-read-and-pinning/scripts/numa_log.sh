#!/bin/bash
# Every 2 s: free memory (MemFree + FilePages) and anon use per CPU NUMA node, in GiB.
#   numa_log.sh LOG
LOG=$1
nodes=$(for n in /sys/devices/system/node/node*; do [ -n "$(cat $n/cpulist)" ] && basename $n; done)
while true; do
  line="$(date +%T)"
  for n in $nodes; do
    line+=" $(awk -v n=$n '/MemFree|FilePages/ {f+=$4} /AnonPages/ {a=$4} /MemTotal/ {t=$4} END {printf "%s free=%dG anon=%dG total=%dG", n, f/1048576, a/1048576, t/1048576}' /sys/devices/system/node/$n/meminfo)"
  done
  echo "$line" >> "$LOG"
  sleep 2
done
