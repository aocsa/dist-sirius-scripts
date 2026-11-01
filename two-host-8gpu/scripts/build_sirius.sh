#!/usr/bin/env bash
# Builds Sirius with NIXL in WT, inside its pixi environment. Configures once, then builds.
# With LIB_ONLY=1, relinks only libsirius.so and installs it into build/release/install/lib by
# rename: ranks that loaded the old file keep their mapping. Never build while ranks run.
#   build_sirius.sh            env: WT, NIXL_PREFIX (~/tools/nvda_nixl), LIB_ONLY
set -euo pipefail
wt=${WT:-/scratch/prestouser/aocsa/wt/xnode-staging}
nixl=${NIXL_PREFIX:-$HOME/tools/nvda_nixl}
export PATH=$HOME/.pixi/bin:$PATH
unset AI_AGENT CLAUDECODE CLAUDE_CODE CURSOR_AGENT
cd "$wt"
echo "start $(date) $(git rev-parse --short HEAD)"
if [ "${LIB_ONLY:-0}" = 1 ]; then
  pixi run ninja -C build/release libsirius.so
  lib=build/release/install/lib
  cp build/release/libsirius.so.0.0.0 "$lib/.libsirius.so.0.0.0.new"
  mv -f "$lib/.libsirius.so.0.0.0.new" "$lib/libsirius.so.0.0.0"
else
  if [ ! -f build/release/CMakeCache.txt ]; then
    pixi run cmake --preset release -DSIRIUS_DUCKDB_SOURCE_DIR="$PWD/duckdb" \
      -DSIRIUS_WITH_NIXL=ON -DSIRIUS_NIXL_PREFIX="$nixl"
  fi
  pixi run make
fi
echo "done $(date)"
