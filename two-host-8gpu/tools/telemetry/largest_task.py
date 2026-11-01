#!/usr/bin/env python3
"""The full event chain of the task with the largest reservation in the pipelines whose label
contains WANT.

usage: largest_task.py <rank telemetry dir> <label substring>
"""
import collections, glob, json, sys
d, want = sys.argv[1], sys.argv[2]
def records(kind):
    for path in glob.glob(f"{d}/{kind}/*.ndjson"):
        for line in open(path):
            yield json.loads(line)
label = {}
for r in records("Operator"):
    dec = r["data"].get("Declaration")
    if dec: label[r["id"]] = dec["label"]
evs = collections.defaultdict(list); pipe_of = {}; res = {}
for r in records("Task"):
    kind, body = next(iter(r["data"].items()))
    if kind == "Created": pipe_of[r["id"]] = body["pipeline_uuid"]["target"]
    if kind == "Reserving": res[r["id"]] = body["requested_bytes"]
    evs[r["id"]].append((r["timestamp"], kind, body))
t0 = min(e[0][0] for e in evs.values())
cands = [t for t, p in pipe_of.items() if want in label.get(p, "")]
t = max(cands, key=lambda t: res.get(t, 0))
for ts, kind, body in sorted(evs[t], key=lambda x: x[0]):
    b = {k: v for k, v in body.items() if not isinstance(v, dict)}
    print("%.1f %s %s" % ((ts - t0) / 1e9, kind, json.dumps(b)[:300]))
