#!/bin/bash
# Copy this directory's configs and Simpatico plan files to the paths the experiment scripts read.
#   scripts/setup.sh            (TPCH_OUT defaults to /tmp/aocsa/tpch22, the scripts' output root)
set -eu
C=$(cd "$(dirname "$0")/../configs" && pwd)
T=${TPCH_OUT:-/tmp/aocsa/tpch22}
mkdir -p $T/pin10k/partial $T/pin10k/sf3k-v2 $T/pin10k/gpu-budget $T/dpo
cp $C/pin-E.yaml $T/pin10k/pin-E.yaml
cp $C/pin-E.yaml $T/pin10k/partial/pin-E-off.yaml
cp $C/pinned-partial25.yaml $T/pin10k/partial/pin-E-partial.yaml
cp $C/pinned-partial40.yaml $T/pin10k/partial/pin-E-partial40.yaml
cp $C/pinned-partial30.yaml $T/pin10k/partial/pin-E-partial30.yaml
cp $C/sf3k-host-pin.yaml $T/pin10k/sf3k-v2/host-pin-d15.yaml
rm -rf $T/pin10k/plans-v2 $T/pin10k/plans-sf10k-g $T/dpo/plans-sf10k
cp -r $C/plans-v2 $T/pin10k/plans-v2
cp -r $C/plans-sf10k-gpu $T/pin10k/plans-sf10k-g
cp -r $C/plans-sf10k $T/dpo/plans-sf10k
echo "configs and plans copied under $T"
