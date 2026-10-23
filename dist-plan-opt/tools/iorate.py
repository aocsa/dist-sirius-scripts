#!/usr/bin/env python3
"""Aggregate read rate and CPU over time from sample.sh output. usage: iorate.py SAMPLES [bin_s=2]"""
import collections, sys
rows = [l.split() for l in open(sys.argv[1])]
bin_s = float(sys.argv[2]) if len(sys.argv) > 2 else 2
prev = {}; rb = collections.Counter(); rc = collections.Counter(); cpu = collections.Counter()
t0 = int(rows[0][0])
for r in rows:
    t, p = int(r[0]), r[1]; v = list(map(int, r[2:6]))
    if p in prev:
        b = int((t - t0) / 1e9 / bin_s)
        rc[b] += v[0] - prev[p][0]; rb[b] += v[1] - prev[p][1]; cpu[b] += (v[2] + v[3]) - (prev[p][2] + prev[p][3])
    prev[p] = v
print(f"{'t(s)':>6} {'rchar GB/s':>10} {'read_bytes GB/s':>15} {'cores busy':>10}")
for b in sorted(rc):
    print(f"{b*bin_s:6.0f} {rc[b]/bin_s/1e9:10.1f} {rb[b]/bin_s/1e9:15.1f} {cpu[b]/100/bin_s:10.1f}")
