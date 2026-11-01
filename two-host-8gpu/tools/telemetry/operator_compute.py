#!/usr/bin/env python3
"""The first 20 compute spans of operator OP on one rank: start, duration, input GB and input
batch count. Used for CONCAT and HASH_JOIN build tasks.

usage: operator_compute.py <rank telemetry dir> <operator id>
"""
import collections, glob, json, sys
d = sys.argv[1]
def records(kind):
    for path in glob.glob(f"{d}/{kind}/*.ndjson"):
        for line in open(path):
            yield json.loads(line)
evs = collections.defaultdict(list)
for r in records("Task"):
    kind, body = next(iter(r["data"].items()))
    evs[r["id"]].append((r["timestamp"], kind, body))
t0 = min(e[0] for v in evs.values() for e in v)
rows = []
for t, e in evs.items():
    e.sort(key=lambda x: x[0])
    for i, (ts, kind, body) in enumerate(e):
        if kind == "Computing" and body.get("current_operator_id") == int(sys.argv[2]):
            end = e[i + 1][0] if i + 1 < len(e) else None
            rows.append(((ts - t0) / 1e9, (end - ts) / 1e9 if end else -1, body.get("input_bytes", 0) / 1e9, len(body.get("input_batch_ids", []))))
for r in sorted(rows)[:20]:
    print("start %.1f dur %.1f in %.2f GB batches %d" % r)
