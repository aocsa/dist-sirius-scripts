#!/bin/bash
# sample.sh OUT: every 0.2 s, per duckdb rank: epoch_ns pid rchar read_bytes utime stime (ticks),
# for tools/iorate.py. Run it in the background around run_q5.sh; kill it after.
out=$1; : > $out
while true; do
  now=$(date +%s%N)
  for p in $(pgrep -x duckdb); do
    io=$(awk '/^rchar|^read_bytes/{printf "%s ", $2}' /proc/$p/io 2>/dev/null) || continue
    st=$(awk '{print $14, $15}' /proc/$p/stat 2>/dev/null) || continue
    echo "$now $p $io$st" >> $out
  done
  sleep 0.2
done
