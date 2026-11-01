#!/usr/bin/env bash
# Runs TPC-H queries at SF100000, each capped at RATIO (default 4) x its SF30000 cold+hot time plus
# 60 s. The SF30000 times come from SF30K_RESULTS (default ../results/sf30k-results.jsonl).
#   run_rest_sf100k.sh <out dir> <q>...
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=$1
shift
for q in "$@"; do
  cap=$(python3 - "$q" "${RATIO:-4}" "${SF30K_RESULTS:-$here/../results/sf30k-results.jsonl}" <<'EOF'
import json, sys
q, ratio, path = int(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
ref = {}
for line in open(path):
    r = json.loads(line)
    ref[r["query"]] = r["times_s"]
print(int(ratio * sum(ref[q]) + 60))
EOF
)
  echo "$(date +%T) Q$q cap ${cap}s" >> "$out.driver.log"
  TIMEOUT=$cap "$here/run_sf100k.sh" "$out" "$q"
done
echo "$(date +%T) done" >> "$out.driver.log"
