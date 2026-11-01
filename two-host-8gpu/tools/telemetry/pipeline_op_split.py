#!/usr/bin/env python3
"""Per-task compute time and input per operator, over the pipelines whose label contains WANT
(e.g. 'CONCAT(4)'). Splits a fused pipeline's time between its operators.

usage: pipeline_op_split.py <rank telemetry dir> <label substring>
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
evs = collections.defaultdict(list); pipe_of = {}
for r in records("Task"):
    kind, body = next(iter(r["data"].items()))
    if kind == "Created": pipe_of[r["id"]] = body["pipeline_uuid"]["target"]
    evs[r["id"]].append((r["timestamp"], kind, body))
tot = collections.Counter(); n = 0; inb = collections.Counter()
for t, p in pipe_of.items():
    if want not in label.get(p, ""): continue
    e = sorted(evs[t], key=lambda x: x[0]); n += 1
    for i, (ts, kind, body) in enumerate(e):
        if kind == "Computing" and i + 1 < len(e):
            tot[body["current_operator_id"]] += e[i + 1][0] - ts
            inb[body["current_operator_id"]] += body.get("input_bytes", 0)
print(n, "tasks")
for op, v in sorted(tot.items()):
    print("op %d: %.3f s/task, input %.2f GB/task" % (op, v / n / 1e9, inb[op] / n / 1e9))
