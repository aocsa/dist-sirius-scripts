#!/bin/bash
# rewrite_one.sh SRC DST : one Parquet file -> same rows and column types, ZSTD, RG-row row groups.
# Skips DST if it exists (resumable); writes DST.tmp and renames it, so a killed run leaves no
# partial DST. rewrite_zstd.sh runs it over a whole dataset.
# Env: DUCKDB (default $SIRIUS_REPO/sirius-duckdb/build/release/duckdb, SIRIUS_REPO default
# /tmp/aocsa/wt/dist-plan-opt), DUCKDB_FLAGS (default -no-agent, an option of that build; set it
# empty for a stock DuckDB CLI), THREADS=6, MEM=24GB per process, LEVEL=3 (ZSTD level),
# RG=114688 (rows per row group; the value used for the SF10000 copy).
set -euo pipefail
src=$1; dst=$2
[ -s "$dst" ] && exit 0
tmp="$dst.tmp"
"${DUCKDB:-${SIRIUS_REPO:-/tmp/aocsa/wt/dist-plan-opt}/sirius-duckdb/build/release/duckdb}" ${DUCKDB_FLAGS--no-agent} -c "
SET threads = ${THREADS:-6}; SET memory_limit = '${MEM:-24GB}'; SET preserve_insertion_order = true;
COPY (SELECT * FROM read_parquet('$src')) TO '$tmp'
  (FORMAT parquet, COMPRESSION zstd, COMPRESSION_LEVEL ${LEVEL:-3}, ROW_GROUP_SIZE ${RG:-114688});" > /dev/null
mv "$tmp" "$dst"
