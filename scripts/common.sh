# Shared settings for every script. Source it; override any value through the environment.
#
# Layout under $WORK:
#   tools/nvda_nixl, tools/ucx-install   NIXL 1.3.2 + UCX 1.21.0 (build_nixl_ucx.sh)
#   sirius/                              the Sirius checkout and build (build_sirius.sh)
#   tpchgen-rs/                          the TPC-H generator (gen_tpch.sh)

: "${WORK:=$HOME/dist-sirius}"
: "${TOOLS:=$WORK/tools}"
: "${SIRIUS_SRC:=$WORK/sirius}"
: "${SIRIUS_REPO:=https://github.com/aocsa/sirius.git}"
: "${SIRIUS_BRANCH:=exchange-operator-v2}"
: "${CUDA_HOME:=/usr/local/cuda}"

# TPC-H data: <DATA_ROOT>/tpch_parquet_sf<SF>/<table>/part.N.parquet. Put it on fast local NVMe.
: "${DATA_ROOT:=$WORK/data}"
# Disk spill tier (fast local NVMe); every rank on a host may share it. bench.sh empties it
# between runs, only if it holds the marker file make_config.sh writes.
: "${SPILL_DIR:=$WORK/spill}"
SPILL_MARKER=".dist-sirius-spill"
# Where configs, logs and results go.
: "${OUT:=$WORK/runs}"

: "${NIXL_PREFIX:=$TOOLS/nvda_nixl}"
: "${UCX_PREFIX:=$TOOLS/ucx-install}"
export NIXL_PREFIX UCX_PREFIX

SHELL_BIN="$SIRIUS_SRC/sirius-duckdb/build/release/duckdb"
EXTENSION="$SIRIUS_SRC/sirius-duckdb/build/release/extension/sirius/sirius.duckdb_extension"
UNITTEST="$SIRIUS_SRC/build/release/test/cpp/sirius_unittest"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# NIXL's libraries and UCX ahead of the pixi environment's UCX, plus the NIXL plugin dir.
nixl_env()
{
  # shellcheck disable=SC1091
  source "$SIRIUS_SRC/scripts/nixl_env.sh"
}

die() { echo "error: $*" >&2; exit 1; }

drop_page_cache()
{
  sync
  sudo -n sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null ||
    echo "warning: could not drop the page cache (needs passwordless sudo); runs are not cold" >&2
}
