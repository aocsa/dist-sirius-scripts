#!/usr/bin/env python3
"""Per-rank host/GPU pool peaks from a run's Sirius log.   usage: pool_peaks.py QUERY_DIR..."""
import glob
import re
import sys

pat = re.compile(r"\[(host_pool|gpu_pool)\] (\w+:\d) \w+ instance=(\S+) .*?query=(\d+) .*?peak=(\d+)")
for d in sys.argv[1:]:
    peaks = {}
    for f in glob.glob(f"{d}/logs/*.log"):
        for line in open(f, errors="replace"):
            m = pat.search(line)
            if m:
                k = (m[1], m[2], m[3])
                peaks[k] = max(peaks.get(k, 0), int(m[5]))
    print(d)
    for k in sorted(peaks):
        print(f"  {k[0]} {k[1]} {k[2]} peak={peaks[k] / 2**30:.1f} GiB")
