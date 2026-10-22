#!/usr/bin/env bash
# Builds UCX 1.21.0 (with CUDA) and NIXL 1.3.2 (UCX plugin only) into $TOOLS, with the same
# options as the reference box. Needs: gcc, make, git, curl, pixi, CUDA at $CUDA_HOME.
#   scripts/build_nixl_ucx.sh
set -euo pipefail
source "$(dirname "$0")/common.sh"

: "${UCX_VERSION:=1.21.0}"
: "${NIXL_REF:=v1.3.2}"
: "${JOBS:=$(nproc)}"
mkdir -p "$TOOLS"
cd "$TOOLS"

# meson, ninja, hwloc, numactl and pybind11 for both builds, from a small pixi environment.
mkdir -p toolenv
cat > toolenv/pixi.toml <<'EOF'
[workspace]
channels = ["conda-forge"]
name = "toolenv"
platforms = ["linux-64", "linux-aarch64"]
version = "0.1.0"

[dependencies]
meson = ">=1.12.0,<2"
ninja = ">=1.13.2,<2"
pkg-config = ">=0.29.2,<0.30"
libhwloc = ">=2.13.0,<3"
numactl = ">=2.0.18,<3"
pybind11 = ">=3.1.0,<4"
python = ">=3.14.7,<3.15"
EOF
(cd toolenv && pixi install)
TOOLENV="$TOOLS/toolenv/.pixi/envs/default"

# UCX
if [ ! -x "$UCX_PREFIX/bin/ucx_info" ]; then
  curl -fsSL -o "ucx-$UCX_VERSION.tar.gz" \
    "https://github.com/openucx/ucx/releases/download/v$UCX_VERSION/ucx-$UCX_VERSION.tar.gz"
  tar xzf "ucx-$UCX_VERSION.tar.gz"
  (cd "ucx-$UCX_VERSION" &&
    ./configure --prefix="$UCX_PREFIX" --with-cuda="$CUDA_HOME" --with-hwloc="$TOOLENV" --enable-mt &&
    make -j"$JOBS" && make install)
fi
"$UCX_PREFIX/bin/ucx_info" -v | head -1

# NIXL
[ -d nixl-src ] || git clone https://github.com/ai-dynamo/nixl.git nixl-src
git -C nixl-src fetch --tags
git -C nixl-src checkout "$NIXL_REF"
(cd nixl-src && pixi run --manifest-path "$TOOLS/toolenv/pixi.toml" meson setup build --reconfigure \
  -Dprefix="$NIXL_PREFIX" -Dbuildtype=release \
  -Ducx_path="$UCX_PREFIX" \
  -Dcudapath_inc="$CUDA_HOME/include" -Dcudapath_lib="$CUDA_HOME/lib64" \
  -Dcudapath_stub="$CUDA_HOME/lib64/stubs" \
  -Denable_plugins=UCX -Ddisable_gds_backend=true -Ddisable_mooncake_backend=true \
  -Ddisable_infinia_backend=true -Dbuild_tests=false -Dbuild_examples=false -Dbuild_docs=false &&
  pixi run --manifest-path "$TOOLS/toolenv/pixi.toml" ninja -C build install)
ls "$NIXL_PREFIX"/lib/*/libnixl.so "$NIXL_PREFIX"/lib/*/plugins/libplugin_UCX.so
echo "NIXL at $NIXL_PREFIX, UCX at $UCX_PREFIX"
