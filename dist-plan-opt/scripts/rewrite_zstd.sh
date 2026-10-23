#!/bin/bash
# rewrite_zstd.sh SRC DST [jobs=12]: a ZSTD copy of a TPC-H Parquet dataset. Every file is
# rewritten on its own by rewrite_one.sh (same file names, rows and column types; ZSTD instead of
# Snappy; row groups of RG=114688 rows, as in the SF10000 tpchgen files), largest first, `jobs` at
# a time; top-level *.json (gen-info.json) is copied. Then each table's row count is compared between SRC and DST.
# Resumable: rerun after an interruption and finished files are skipped.
# SF10000 on the 4x GB200 box, SRC and DST on GPFS, 12 jobs (each THREADS=6, MEM=24GB):
#   /scratch/sirius/datasets/tpch_sf10000 (3.7 TB) -> /scratch/nvidia/aocsa/tpch_sf10000_zstd (2.3 TB)
# Env: DUCKDB, DUCKDB_FLAGS, THREADS, MEM, LEVEL, RG (see rewrite_one.sh).
set -euo pipefail
src=$(realpath "${1:?usage: rewrite_zstd.sh SRC DST [jobs]}"); dst=$(realpath -m "${2:?usage: rewrite_zstd.sh SRC DST [jobs]}")
jobs=${3:-12}
here=$(cd "$(dirname "$0")" && pwd)
DUCKDB=${DUCKDB:-${SIRIUS_REPO:-/tmp/aocsa/wt/dist-plan-opt}/sirius-duckdb/build/release/duckdb}
export DUCKDB
mkdir -p "$dst"
for f in "$src"/*.json; do [ -e "$f" ] && [ ! -e "$dst/${f##*/}" ] && cp "$f" "$dst"/; done
list="$dst/.rewrite-files.txt"
(cd "$src" && find . -name '*.parquet' -printf '%s %P\n') | sort -rn | while read -r _ f; do
  mkdir -p "$dst/$(dirname "$f")"; echo "$src/$f $dst/$f"
done > "$list"
echo "$(wc -l < "$list") files, $jobs at a time"
start=$SECONDS
xargs -P "$jobs" -L 1 "$here/rewrite_one.sh" < "$list"
echo "rewritten in $(( SECONDS - start ))s"
rm -f "$list"
bad=0
for t in $(cd "$src" && find . -name '*.parquet' -printf '%h\n' | sed 's|^\./||' | sort -u); do
  n() { "$DUCKDB" ${DUCKDB_FLAGS--no-agent} -noheader -list -c "SELECT count(*) FROM read_parquet('$1/$t/*.parquet')"; }
  a=$(n "$src"); b=$(n "$dst")
  [ "$a" = "$b" ] && echo "$t: $a rows, ok" || { echo "$t: $a rows in SRC, $b in DST"; bad=1; }
done
du -sh "$src" "$dst"
exit $bad
