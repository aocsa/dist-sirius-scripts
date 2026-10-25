#!/usr/bin/env bash
# Copy of scripts/exchange_sql_ranks.sh for the dpo build that gives each rank its own SIRIUS_LOG_DIR.
#   ranks_trace.sh <ranks> <file.sql> <base port> <out dir>
set -euo pipefail
ranks=$1; sql=$2; port=$3; out=$4
repo=${SIRIUS:-/tmp/aocsa/wt/dpo-rank-pin}
source "$repo/scripts/nixl_env.sh"
build="$repo/sirius-duckdb/build/release"
peers=""
for ((i = 0; i < ranks; i++)); do peers+="${peers:+,}127.0.0.1:$((port + i))"; done
pids=()
for ((i = 0; i < ranks; i++)); do
  mkdir -p "$out/logs/rank$i"
  {
    echo "LOAD '$build/extension/sirius/sirius.duckdb_extension';"
    echo "SET enable_duckdb_fallback = false;"
    echo "SET sirius_exchange_peers = '$peers';"
    echo "SET sirius_exchange_rank = $i;"
    cat "$sql"
  } | SIRIUS_LOG_DIR="$out/logs/rank$i" CUDA_VISIBLE_DEVICES=$i timeout -s KILL 1800 "$build/duckdb" -unsigned \
    > "$out/exchange_rank_$i.log" 2>&1 &
  pids+=($!)
done
status=0
for ((i = 0; i < ranks; i++)); do wait "${pids[$i]}" || { status=1; echo "rank $i failed"; }; done
exit $status
