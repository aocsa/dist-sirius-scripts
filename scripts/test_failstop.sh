#!/usr/bin/env bash
# End-to-end check that a query failing on one rank fails on every rank at once (instead of the
# others hanging), and that the next query still runs. Rank 1 gets an injected GPU launch failure
# on every attempt with a retry limit of 1, so its first Q12 fails; then both ranks clear the
# injection and run Q12 again.
#   scripts/test_failstop.sh [SF]      (default SF 1000; needs 2 GPUs)
set -uo pipefail
source "$(dirname "$0")/common.sh"

sf=${1:-1000}
data="$DATA_ROOT/tpch_parquet_sf$sf"
dir="$OUT/failstop-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$dir"
: "${CONFIG:=$dir/sirius.yaml}"
[ -f "$CONFIG" ] || "$REPO_DIR/scripts/make_config.sh" "$CONFIG" > /dev/null
export CONFIG
{
  grep -E "lineitem|orders" "$REPO_DIR/sql/views.sql" | sed "s|@DATA@|$data|g"
  echo ".timer on"
  grep -v '^--' "$REPO_DIR/sql/q12.sql"
  echo "SET sirius_test_inject_gpu_task_launch_error = 0;"
  grep -v '^--' "$REPO_DIR/sql/q12.sql"
} > "$dir/failstop.sql"
printf '%s\n' "SET sirius_test_inject_gpu_task_launch_error = 1000000;" \
  "SET sirius_test_gpu_task_retry_limit = 1;" "SET sirius_test_gpu_task_retry_backoff_ms = 1;" \
  > "$dir/inject.sql"

# sirius_test_* settings exist only with this set.
export SIRIUS_ENABLE_TEST_OPTIONS=1
start=$SECONDS
EXCHANGE_TIMEOUT=600 "$REPO_DIR/scripts/run_ranks.sh" 2 "$dir/failstop.sql" "$dir" 1 "$dir/inject.sql" \
  > /dev/null
echo "both ranks done in $(( SECONDS - start ))s"
r0="$dir/rank0/out.log"
grep -q "failed on rank 1" "$r0" && echo "PASS rank 0's first query failed with rank 1's error" ||
  { echo "FAIL rank 0 did not report rank 1's failure: $r0"; exit 1; }
expected="$REPO_DIR/expected/q12_sf$sf.csv"
if [ ! -f "$expected" ]; then
  grep -q "Run Time" "$r0" && echo "SKIP no $expected to check the second query's answer against"
  exit 0
fi
while read -r row; do
  grep -qx "$row" "$r0" || { echo "FAIL the second query's answer is missing $row"; exit 1; }
done < <(tail -n +2 "$expected")
echo "PASS the second query returned the expected answer"
