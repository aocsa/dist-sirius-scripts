#!/usr/bin/env python3
"""GPFS ingress over each warm run: net.txt (ns, 4-byte words) + rank-0 query start/end from telemetry.
usage: netrate.py RUN_DIR [bin_s=1] [ranks=4]
RUN_DIR holds net.txt (netsample.sh) and telemetry_data/ (Quent telemetry of every rank)."""
import collections, glob, json, sys
d = sys.argv[1]; bin_s = float(sys.argv[2]) if len(sys.argv) > 2 else 1
ranks = int(sys.argv[3]) if len(sys.argv) > 3 else 4
net = [tuple(map(int, l.split())) for l in open(f"{d}/net.txt") if len(l.split()) == 2]
qs = collections.defaultdict(dict)
for f in glob.glob(f"{d}/telemetry_data/*/Query/*.ndjson"):
    for l in open(f):
        e = json.loads(l); k = next(iter(e["data"])); qs[e["id"]][k] = e["timestamp"]
runs = sorted((v["Executing"], v["Finished"]) for v in qs.values() if "Finished" in v)
runs = [runs[i:i + ranks] for i in range(0, len(runs), ranks)]  # one Query per rank per execution
for i, r in enumerate(runs):
    s, e = min(x[0] for x in r), max(x[1] for x in r)
    pts = [(t, w * 4) for t, w in net if s - 1e9 <= t <= e + 1e9]
    tot = (pts[-1][1] - pts[0][1]) / 1e9
    print(f"run{i+1}: {(e-s)/1e9:.1f} s, {tot:.0f} GB in, avg {tot/((e-s)/1e9):.1f} GB/s")
    b = collections.Counter()
    for (t0, b0), (t1, b1) in zip(pts, pts[1:]):
        b[int((t0 - s) / 1e9 // bin_s)] += b1 - b0
    print("   " + " ".join(f"{b[k]/bin_s/1e9:.0f}" for k in sorted(b)))
