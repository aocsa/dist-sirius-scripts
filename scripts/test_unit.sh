#!/usr/bin/env bash
# Runs the C++ unit tests of the distributed pieces: the exchange (wire, transport, operators,
# collectives, byte ranges, query abort), dynamic filters, and parquet byte ranges.
#   scripts/test_unit.sh
# The 2 hidden [host_allocation_fault] tests need scripts/run_unit_tests.py's fault hook, so they
# are excluded here; [parquet_byte_range] has one known [!shouldfail] test ("failed as expected").
set -uo pipefail
source "$(dirname "$0")/common.sh"
cd "$SIRIUS_SRC"
status=0
for tag in "[exchange]" "[dynamic_filter]~[host_allocation_fault]" "[parquet_byte_range]" \
           "[substrait_scan_ranges]"; do
  echo "== $tag"
  # The test binary takes its libraries from the pixi environment, then NIXL's ahead of them.
  pixi run bash -c "source scripts/nixl_env.sh && '$UNITTEST' '$tag'" 2>&1 |
    grep -E "^test cases|All tests passed|FAILED|failed"
  [ "${PIPESTATUS[0]}" = 0 ] || status=1
done
exit $status
