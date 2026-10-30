#!/usr/bin/env python3
"""Per query dir: host->disk spill (downgrade log) and the largest host-pool peak, per host.
usage: mem_stats.py QUERY_DIR..."""
import glob, os, re, sys

pool = re.compile(r"\[host_pool\] (\w+:\d) \w+ instance=(\S+) .*?peak=(\d+)")
for d in sys.argv[1:]:
    for hd in sorted(glob.glob(f"{d}/logs/*")):
        to_disk, peak = 0, 0
        for f in glob.glob(f"{hd}/*.log"):
            for line in open(f, errors="replace"):
                m = re.search(r"to_disk: \d+/(\d+)", line)
                if m:
                    to_disk += int(m[1])
                m = pool.search(line)
                if m:
                    peak = max(peak, int(m[3]))
        print(f"{os.path.basename(d)} {os.path.basename(hd)}: to_disk={to_disk / 1e9:.0f}GB host_pool_peak={peak / 2**30:.0f}GiB")
