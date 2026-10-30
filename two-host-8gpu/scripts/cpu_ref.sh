#!/usr/bin/env bash
# Writes DuckDB's CPU answer to TPC-H Q at SF, without Sirius, to <out dir>/q<QQ>_sf<SF>.log.
#   cpu_ref.sh <q> <out dir>     env: SF (100), DATA (/scratch/sirius/datasets/tpch_sf$SF)
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
q=$1
sf=${SF:-100}
data=${DATA:-/scratch/sirius/datasets/tpch_sf$sf}
wt=${WT:-/scratch/prestouser/aocsa/wt/xnode-staging}
unset AI_AGENT CLAUDECODE CLAUDE_CODE CURSOR_AGENT
mkdir -p "$2"
log=$2/q$(printf %02d "$q")_sf$sf.log
python3 "$here/gen_sql.py" "$q" "$sf" "$data" 1 \
  | "$HOME/.pixi/bin/pixi" run --manifest-path "$wt/pixi.toml" \
    "$wt/sirius-duckdb/build/release/duckdb" -no-agent > "$log" 2> "$log.err"
echo "$log"
