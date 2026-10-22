#!/usr/bin/env bash
# Generates TPC-H parquet at scale factor SF with sirius-db/tpchgen-rs (the layout and encodings
# the Sirius benchmarks use), then verifies every table's row count.
#   scripts/gen_tpch.sh <SF> [jobs]
# Output: $DATA_ROOT/tpch_parquet_sf<SF>/<table>/part.N.parquet. Needs cargo (rustup) and python3.
# Disk: about 0.27 GiB per SF unit (SF1000 = 265 GiB, SF3000 = 802 GiB).
set -euo pipefail
source "$(dirname "$0")/common.sh"

SF=${1:?usage: gen_tpch.sh <SF> [jobs]}
JOBS=${2:-$(nproc)}
: "${TPCHGEN_REF:=cdcf74d}"
GEN="$WORK/tpchgen-rs"
OUTDIR="$DATA_ROOT/tpch_parquet_sf$SF"

if [ ! -d "$GEN/.git" ]; then git clone https://github.com/sirius-db/tpchgen-rs.git "$GEN"; fi
git -C "$GEN" checkout -q "$TPCHGEN_REF"
(cd "$GEN" && cargo build --release -p tpchgen-cli)

if [ ! -x "$WORK/venv/bin/python" ]; then python3 -m venv "$WORK/venv"; fi
"$WORK/venv/bin/pip" install -q pyarrow

# Each generator job holds up to ~24 GiB at SF1000 (it was OOM-killed at 48 jobs on 181 GiB):
# cap the jobs by RAM.
mem_gib=$(awk '/MemTotal/{print int($2/1048576)}' /proc/meminfo)
job_gib=$(( SF * 24 / 1000 )); [ "$job_gib" -lt 2 ] && job_gib=2
max_jobs=$(( mem_gib / job_gib )); [ "$max_jobs" -lt 1 ] && max_jobs=1
[ "$JOBS" -gt "$max_jobs" ] && { echo "capping jobs $JOBS -> $max_jobs"; JOBS=$max_jobs; }

need_gib=$(( SF * 27 / 100 + 1 ))
mkdir -p "$DATA_ROOT"
free_gib=$(df -BG --output=avail "$DATA_ROOT" | tail -1 | tr -dc '0-9')
[ "$free_gib" -ge "$need_gib" ] || die "$free_gib GiB free in $DATA_ROOT, need ~$need_gib GiB"

if [ -d "$OUTDIR" ] && [ "${FORCE:-0}" != 1 ] &&
  "$WORK/venv/bin/python" -I "$REPO_DIR/tools/verify_tpch.py" "$OUTDIR" "$SF"; then
  echo "$OUTDIR already verified; FORCE=1 regenerates it"
  exit 0
fi
# Generated beside the dataset and swapped in only once verified, so a failed or interrupted run
# never leaves a partial dataset under the real name.
tmp="$OUTDIR.tmp"
rm -rf "$tmp"
start=$SECONDS
"$WORK/venv/bin/python" "$GEN/scripts/generate_tpch.py" -s "$SF" -f parquet -j "$JOBS" -o "$tmp"
echo "generated in $(( SECONDS - start ))s"
"$WORK/venv/bin/python" -I "$REPO_DIR/tools/verify_tpch.py" "$tmp" "$SF"
rm -rf "$OUTDIR"
mv "$tmp" "$OUTDIR"
echo "dataset at $OUTDIR"
