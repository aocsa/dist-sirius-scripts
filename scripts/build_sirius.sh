#!/usr/bin/env bash
# Clones (or updates) Sirius at $SIRIUS_BRANCH and builds it with the NIXL exchange: the library,
# the C++ unit tests, and the DuckDB shell + loadable extension. Needs pixi >= 0.78 and the NIXL
# install from build_nixl_ucx.sh. A clean build takes ~30-40 min on 48 cores.
#   scripts/build_sirius.sh [--clean]
set -euo pipefail
source "$(dirname "$0")/common.sh"

[ -f "$NIXL_PREFIX/include/nixl.h" ] || die "no NIXL at $NIXL_PREFIX; run build_nixl_ucx.sh first"

if [ ! -d "$SIRIUS_SRC/.git" ]; then
  git clone --branch "$SIRIUS_BRANCH" "$SIRIUS_REPO" "$SIRIUS_SRC"
else
  git -C "$SIRIUS_SRC" fetch origin "$SIRIUS_BRANCH"
  git -C "$SIRIUS_SRC" checkout "$SIRIUS_BRANCH"
  git -C "$SIRIUS_SRC" merge --ff-only "origin/$SIRIUS_BRANCH" ||
    die "origin/$SIRIUS_BRANCH was rewritten; with no local changes to keep, run:
  git -C $SIRIUS_SRC reset --hard origin/$SIRIUS_BRANCH"
fi
cd "$SIRIUS_SRC"
# Only what the engine needs (the StarRocks/Doris submodules are large and unused here).
git submodule update --init --recursive duckdb cucascade substrait \
  sirius-duckdb/duckdb sirius-duckdb/extension-ci-tools sirius-duckdb/vcpkg
git log -1 --format='Sirius %h %s'

[ "${1:-}" = "--clean" ] && rm -rf build sirius-duckdb/build

# The presets set sccache as the compiler launcher; an empty launcher builds without it.
NOLAUNCH=(-DCMAKE_C_COMPILER_LAUNCHER= -DCMAKE_CXX_COMPILER_LAUNCHER= -DCMAKE_CUDA_COMPILER_LAUNCHER=)
pixi run cmake --preset release -DSIRIUS_DUCKDB_SOURCE_DIR="$PWD/duckdb" "${NOLAUNCH[@]}" \
  -DSIRIUS_WITH_NIXL=ON -DSIRIUS_NIXL_PREFIX="$NIXL_PREFIX"
pixi run cmake --build --preset release --target sirius_library sirius_unittest
pixi run cmake --install build/release --prefix "$PWD/build/release/install" --component sirius_library
# The top-level Makefile hardcodes sccache for this step, so it is run directly.
pixi run make -C sirius-duckdb release SIRIUS_DUCKDB_LINKAGE=shared \
  SIRIUS_INSTALL_DIR="$PWD/build/release/install" \
  EXT_FLAGS="-DCMAKE_C_COMPILER_LAUNCHER= -DCMAKE_CXX_COMPILER_LAUNCHER="

ls -la "$SHELL_BIN" "$EXTENSION" "$UNITTEST"
echo "Built. After rebuilding only the library, re-run the 'cmake --install' step: the extension"
echo "loads libsirius.so from build/release/install/lib."
