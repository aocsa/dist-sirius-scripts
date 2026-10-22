#!/usr/bin/env bash
# Runs a SQL file as one distributed query per statement on N ranks of this host, one process and
# one GPU per rank, and prints rank 0's output (the result). Every rank runs the whole file.
#   scripts/run_ranks.sh <ranks> <file.sql> <logdir> [<fail_rank> <extra.sql>]
# With <fail_rank>, that rank alone runs <extra.sql> first (fault injection; see test_failstop.sh).
#
#   CONFIG            Sirius YAML for every rank (make_config.sh); required
#   GPUS              GPU ids, rank i on the i-th (default "0 1 ... ranks-1")
#   BASE_PORT         rank i listens on BASE_PORT + i (default 27000)
#   EXCHANGE_TIMEOUT  kill a rank after this many seconds (default 1800)
#   GRACE             once a rank exits with an error, kill the others after this many seconds
#                     (default 60): a rank that died mid-query leaves its peers waiting on it
# Logs: <logdir>/rank<i>/out.log (shell output) and <logdir>/rank<i>/log/ (Sirius log).
set -uo pipefail
source "$(dirname "$0")/common.sh"

ranks=${1:?usage: run_ranks.sh <ranks> <file.sql> <logdir> [<fail_rank> <extra.sql>]}
sql=$(realpath "${2:?}")
logs=$(realpath -m "${3:?}")
fail_rank=${4:-}
extra=${5:+$(realpath "$5")}
: "${CONFIG:?set CONFIG to a Sirius YAML (scripts/make_config.sh)}"
CONFIG=$(realpath "$CONFIG")
: "${BASE_PORT:=27000}"
: "${EXCHANGE_TIMEOUT:=1800}"
: "${GRACE:=60}"
read -r -a gpus <<< "${GPUS:-$(seq -s ' ' 0 $((ranks - 1)))}"
[ "${#gpus[@]}" -ge "$ranks" ] || die "GPUS lists ${#gpus[@]} GPUs for $ranks ranks"
nixl_env

peers=""
for ((i = 0; i < ranks; i++)); do peers+="${peers:+,}127.0.0.1:$((BASE_PORT + i))"; done

# Kills a process and everything under it (each rank is a subshell -> timeout -> duckdb).
kill_tree()
{
  local child
  for child in $(pgrep -P "$1"); do kill_tree "$child"; done
  kill -9 "$1" 2>/dev/null
}

pids=()
# Ranks wait on each other: never leave one running when this script stops.
trap 'for p in "${pids[@]}"; do kill_tree "$p"; done' EXIT
trap 'exit 130' INT TERM
for ((i = 0; i < ranks; i++)); do
  dir="$logs/rank$i"
  mkdir -p "$dir"
  (
    cd "$dir"
    {
      echo "LOAD '$EXTENSION';"
      # A rank falling back to the CPU alone would return a wrong answer: fail instead.
      echo "SET enable_duckdb_fallback = false;"
      echo "SET sirius_exchange_peers = '$peers';"
      echo "SET sirius_exchange_rank = $i;"
      if [ "$i" = "$fail_rank" ] && [ -n "$extra" ]; then cat "$extra"; fi
      cat "$sql"
    } | CUDA_VISIBLE_DEVICES=${gpus[$i]} SIRIUS_CONFIG_FILE=$CONFIG SIRIUS_LOG_DIR="$dir/log" \
      timeout -s KILL "$EXCHANGE_TIMEOUT" "$SHELL_BIN" -unsigned -no-agent -csv \
      > "$dir/out.log" 2>&1
  ) &
  pids+=($!)
done

status=0
declare -A finished=()
failed_at=""
while [ "${#finished[@]}" -lt "$ranks" ]; do
  for ((i = 0; i < ranks; i++)); do
    [ -n "${finished[$i]:-}" ] && continue
    kill -0 "${pids[$i]}" 2>/dev/null && continue
    wait "${pids[$i]}"
    rc=$?
    finished[$i]=$rc
    if [ "$rc" != 0 ]; then
      status=1
      echo "rank $i exited $rc: $logs/rank$i/out.log" >&2
      [ -z "$failed_at" ] && failed_at=$SECONDS
    fi
  done
  if [ -n "$failed_at" ] && [ $((SECONDS - failed_at)) -gt "$GRACE" ] &&
     [ "${#finished[@]}" -lt "$ranks" ]; then
    echo "killing the ranks still running ${GRACE}s after a rank failed" >&2
    status=1
    break
  fi
  [ "${#finished[@]}" -lt "$ranks" ] && sleep 1
done
grep -v 'no version information' "$logs/rank0/out.log"
exit $status
