#!/usr/bin/env bash
# Starts ranks FIRST .. FIRST+COUNT-1 on this host, rank FIRST+i on GPU i, and waits for them.
# Run inside the worktree's pixi environment (xnode_tpch.sh does).
#   host_ranks.sh <first> <count> <peers> <file.sql> <sirius.yaml> <out dir>
set -uo pipefail
first=$1 count=$2 peers=$3 sql=$4 cfg=$5 out=$6
wt=${WT:-/scratch/prestouser/aocsa/wt/xnode-staging}
unset AI_AGENT CLAUDECODE CLAUDE_CODE CURSOR_AGENT
source "$wt/scripts/nixl_env.sh"
# Multi-homed GB200 nodes: UCX otherwise advertises addresses the other host cannot reach.
export UCX_NET_DEVICES=${UCX_NET_DEVICES:-bond0}
export SIRIUS_CONFIG_FILE=$cfg
export SIRIUS_LOG_DIR=$out/logs/$(hostname -s)
mkdir -p "$SIRIUS_LOG_DIR"
build=$wt/sirius-duckdb/build/release
# The working directory decides where telemetry_data goes.
cd "$out"
env | grep -E '^(UCX_|NIXL_|SIRIUS_|CUDA_|LD_LIBRARY_PATH=|TIMEOUT=|WT=)' | sort > "$out/env-$(hostname -s).txt"

pids=()
for ((i = 0; i < count; i++)); do
  r=$((first + i))
  {
    echo "LOAD '$build/extension/sirius/sirius.duckdb_extension';"
    # A rank falling back to the CPU alone would return a wrong answer: fail instead.
    echo "SET enable_duckdb_fallback = false;"
    echo "SET sirius_exchange_peers = '$peers';"
    echo "SET sirius_exchange_rank = $r;"
    cat "$sql"
  } | CUDA_VISIBLE_DEVICES=$i timeout -s KILL "${TIMEOUT:-1800}" "$build/duckdb" -unsigned -no-agent \
    > "$out/rank_$r.log" 2>&1 &
  pids+=($!)
done
status=0
for ((i = 0; i < count; i++)); do
  if ! wait "${pids[$i]}"; then status=1; echo "rank $((first + i)) failed: $out/rank_$((first + i)).log"; fi
done
exit $status
