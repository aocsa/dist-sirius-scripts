#!/usr/bin/env python3
"""Memory reservations of the tasks in the pipelines whose label contains WANT: min, median and
max size, and when the first and last tasks were created, reserved, prepared and computed.

usage: pipeline_reservations.py <rank telemetry dir> <label substring>
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
pipe_of, res, ev = {}, {}, collections.defaultdict(dict)
for r in records("Task"):
    kind, body = next(iter(r["data"].items()))
    if kind == "Created": pipe_of[r["id"]] = body["pipeline_uuid"]["target"]
    if kind == "Reserving": res[r["id"]] = body["requested_bytes"]
    ev[r["id"]].setdefault(kind, r["timestamp"])
rows = [(ev[t].get("Reserving", 0), res.get(t, 0), ev[t]) for t, p in pipe_of.items() if want in label.get(p, "")]
rows.sort(key=lambda x: x[0])
t0 = min(e.get("Created", 1e30) for e in ev.values())
import statistics
v = [r[1] / 2**30 for r in rows]
print(len(v), "tasks; reservation GiB: min %.1f median %.1f max %.1f" % (min(v), statistics.median(v), max(v)))
for r in rows[:6] + rows[-4:]:
    e = r[2]
    print("created %.1f res %.1fG reserving %.1f preparing %.1f computing %.1f" % ((e.get("Created", t0) - t0) / 1e9, r[1] / 2**30, (e.get("Reserving", t0) - t0) / 1e9, (e.get("Preparing", t0) - t0) / 1e9, (e.get("Computing", t0) - t0) / 1e9))
