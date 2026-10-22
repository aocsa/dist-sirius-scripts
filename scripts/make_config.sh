#!/usr/bin/env bash
# Writes a Sirius config (YAML) for one rank, from the environment. Every rank on a host can use
# the same file. See notes/TUNING.md for how to size these for your GPUs, VRAM and host memory.
#   scripts/make_config.sh <out.yaml>
#
#   GPU_USAGE_FRACTION   fraction of each GPU's VRAM Sirius manages               (default 0.95)
#   GPU_DOWNGRADE_AT     spill GPU data to host above this fraction of it          (default 0.8)
#   GPU_DOWNGRADE_STOP   stop spilling at this fraction                            (default 0.6)
#   HOST_CAPACITY        pinned host tier per rank, e.g. 40Gi (unset: Sirius's default, 0.9 of
#                        the NUMA node's RAM, per process -- too much with several ranks a host)
#   DISK_CAPACITY        disk spill tier per rank, e.g. 300G (default 300G; 0 disables it)
#   SPILL_DIR            disk spill directory (default from common.sh)
#   CACHE_MODE           scan reads: os (page cache), none (O_DIRECT), sirius     (default os)
#   PIPELINE_THREADS     GPU pipeline worker threads per rank (unset: Sirius's default)
#   SCAN_THREADS         scan manager threads per rank (unset: Sirius's default)
#   HASH_PARTITION_BYTES target bytes per hash partition, e.g. 768Mi (unset: default 768 MiB)
set -euo pipefail
source "$(dirname "$0")/common.sh"

out=${1:?usage: make_config.sh <out.yaml>}
: "${GPU_USAGE_FRACTION:=0.95}"
: "${GPU_DOWNGRADE_AT:=0.8}"
: "${GPU_DOWNGRADE_STOP:=0.6}"
: "${DISK_CAPACITY:=300G}"
: "${CACHE_MODE:=os}"
mkdir -p "$(dirname "$out")"

{
  echo "sirius:"
  echo "  memory:"
  echo "    gpu:"
  echo "      usage_limit_fraction: $GPU_USAGE_FRACTION"
  echo "      downgrade_trigger_fraction: $GPU_DOWNGRADE_AT"
  echo "      downgrade_stop_fraction: $GPU_DOWNGRADE_STOP"
  if [ -n "${HOST_CAPACITY:-}" ]; then
    echo "    host:"
    echo "      capacity_bytes: $HOST_CAPACITY"
  fi
  if [ "$DISK_CAPACITY" != "0" ]; then
    mkdir -p "$SPILL_DIR"
    touch "$SPILL_DIR/$SPILL_MARKER"
    echo "    disk: { disk_id: 0, capacity_bytes: $DISK_CAPACITY, downgrade_root_dirs: \"$SPILL_DIR\" }"
  fi
  echo "  executor:"
  echo "    scan_manager:"
  [ -n "${SCAN_THREADS:-}" ] && echo "      num_threads: $SCAN_THREADS"
  echo "      cache: { mode: $CACHE_MODE }"
  [ -n "${PIPELINE_THREADS:-}" ] && echo "    pipeline: { num_threads: $PIPELINE_THREADS }"
  if [ -n "${HASH_PARTITION_BYTES:-}" ]; then
    echo "  operator_params:"
    echo "    hash_partition_bytes: $HASH_PARTITION_BYTES"
  fi
} > "$out"
echo "wrote $out"
