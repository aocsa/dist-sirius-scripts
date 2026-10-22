#!/usr/bin/env bash
# Runs a SQL file in one Sirius process on one GPU (no exchange): the single-GPU baseline.
#   scripts/run_single.sh <file.sql> <logdir>
#   CONFIG  Sirius YAML (make_config.sh); required.   GPU  GPU id (default 0)
# Logs: <logdir>/out.log (shell output) and <logdir>/log/ (Sirius log).
set -uo pipefail
source "$(dirname "$0")/common.sh"

sql=$(realpath "${1:?usage: run_single.sh <file.sql> <logdir>}")
logs=$(realpath -m "${2:?}")
: "${CONFIG:?set CONFIG to a Sirius YAML (scripts/make_config.sh)}"
CONFIG=$(realpath "$CONFIG")
: "${GPU:=0}"
# The extension links NIXL, so its libraries must be on the path even without an exchange.
nixl_env
mkdir -p "$logs"
cd "$logs"
{
  echo "LOAD '$EXTENSION';"
  echo "SET enable_duckdb_fallback = false;"
  cat "$sql"
} | CUDA_VISIBLE_DEVICES=$GPU SIRIUS_CONFIG_FILE=$CONFIG SIRIUS_LOG_DIR="$logs/log" \
  "$SHELL_BIN" -unsigned -no-agent -csv > "$logs/out.log" 2>&1
status=$?
grep -v 'no version information' "$logs/out.log"
exit $status
