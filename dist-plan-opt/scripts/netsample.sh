#!/bin/bash
# netsample.sh OUT: every 0.1 s: epoch_ns and the sum of port_rcv_data (4-byte words) over the GPFS
# NICs, for tools/netrate.py. Run it in the background around run_q5.sh; kill it after.
# NICS: the RDMA devices that carry GPFS traffic (default: this box's RoCE ports mlx5_0/1/4/5;
# see `ibdev2netdev` and `mmdiag --network`).
out=$1; : > $out
f=""; for n in ${NICS:-mlx5_0 mlx5_1 mlx5_4 mlx5_5}; do f="$f /sys/class/infiniband/$n/ports/1/counters/port_rcv_data"; done
while true; do echo "$(date +%s%N) $(cat $f | awk '{s+=$1} END{print s}')" >> $out; sleep 0.1; done
