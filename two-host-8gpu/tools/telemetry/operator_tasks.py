#!/usr/bin/env python3
"""Every task that computed operator OP on one rank, as its event chain (seconds from the rank's
first task) with tier moves, reservation sizes and shortfalls. Shows where a slow task waited.

usage: operator_tasks.py <rank telemetry dir> <operator id>
"""
import collections, glob, json, sys
d, op = sys.argv[1], int(sys.argv[2])
def records(kind):
    for path in glob.glob(f"{d}/{kind}/*.ndjson"):
        for line in open(path):
            yield json.loads(line)
evs = collections.defaultdict(list)
for r in records("Task"):
    kind, body = next(iter(r["data"].items()))
    evs[r["id"]].append((r["timestamp"], kind, body))
t0 = min(e[0] for v in evs.values() for e in v)
for t, e in evs.items():
    e.sort(key=lambda x: x[0])
    if not any(k == "Computing" and b.get("current_operator_id") == op for _, k, b in e): continue
    out = []
    for ts, kind, body in e:
        b = {k: v for k, v in body.items() if k in ("origin_tier", "target_tier", "requested_bytes", "shortfall_bytes", "peak_allocated_bytes")}
        out.append("%.1f %s %s" % ((ts - t0) / 1e9, kind, b if b else ""))
    print(" | ".join(out)); print()
