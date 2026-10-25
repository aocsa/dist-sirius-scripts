#!/bin/bash
# dpo build, 4 MiB host blocks, SF10000 Q12/Q19/Q21, 4 ranks, cold + warm.
# Per-rank trace logs (operator times) plus SIRIUS_SCAN_TIMING (scan and shuffle phases).
set -u
S=$(cd "$(dirname "$0")" && pwd)
O=/tmp/aocsa/tpch22/dpo-best/shuffle-profile
SQL=/tmp/aocsa/tpch22/integ/sf10k-direct-dpocfg-4r
BIN=$(readlink -f ${SIRIUS:-/tmp/aocsa/wt/dpo-rank-pin}/sirius-duckdb/build/release/duckdb)
unset AI_AGENT CLAUDECODE CLAUDE_CODE
mkdir -p $O
note() { echo "$(date +%T) $*" | tee -a $O/run.log.txt; }
node_free() { awk '/MemFree|FilePages/ {s+=$4} END {print int(s/1048576)}' /sys/devices/system/node/node$1/meminfo; }
quiet() { ! pgrep -x duckdb >/dev/null && [ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ] && [ "$(node_free 0)" -ge 350 ] && [ "$(node_free 1)" -ge 350 ]; }
$S/mem_guard_dpo.sh $O/mem_guard.log 50 & G=$!
trap "kill $G" EXIT
for q in 12 19 21; do
  qq=$(printf %02d $q); d=$O/q$qq; w=0
  until quiet; do sleep 10; w=$((w+10)); [ $w -ge 600 ] && { note "Q$q: host not quiet after 10 min"; exit 1; }; done
  rm -rf /tmp/aocsa/sirius-disk/* $d; mkdir -p $d
  sed -e 's|@SPILL_DIR@|/tmp/aocsa/sirius-disk|' -e 's|capacity_bytes: 250Gi|capacity_bytes: 210Gi|' \
    /tmp/aocsa/tpch22/dpo-best/sf10k-22-b4/direct-io-spread-b4.yaml > $d/sirius.yaml
  note "Q$q start"
  SIRIUS_CONFIG_FILE=$d/sirius.yaml SIRIUS_LOG_LEVEL=trace SIRIUS_SCAN_TIMING=1 \
    $S/ranks_trace.sh 4 $SQL/q${qq}_direct.sql $((35000 + q * 10)) $d > $d/run.out 2>&1 || note "Q$q failed"
  for p in $(pgrep -x duckdb); do [ "$(readlink -f /proc/$p/exe 2>/dev/null)" = "$BIN" ] && kill -9 $p; done
  note "Q$q $(grep -h 'Run Time' $d/exchange_rank_0.log | awk '{print $5}' | tr '\n' ' ')"
done
note "shuffle profile done"
