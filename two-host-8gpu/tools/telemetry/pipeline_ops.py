#!/usr/bin/env python3
"""One line per pipeline of one rank: its operator ids, task count, wall span (seconds from the
rank's first task), summed reservation wait and compute time, and input bytes.

usage: pipeline_ops.py <rank telemetry dir, e.g. <run>/q18/telemetry_data/<uuid>>
"""
import collections, glob, json, sys
d = sys.argv[1]
def records(kind):
    for path in glob.glob(f"{d}/{kind}/*.ndjson"):
        for line in open(path):
            yield json.loads(line)
evs = collections.defaultdict(list); pipe_of = {}
for r in records("Task"):
    kind, body = next(iter(r["data"].items()))
    if kind == "Created": pipe_of[r["id"]] = body["pipeline_uuid"]["target"]
    evs[r["id"]].append((r["timestamp"], kind, body))
t0 = min(e[0] for v in evs.values() for e in v)
st = collections.defaultdict(lambda: dict(n=0, a=1e30, b=0, comp=0, wait=0, prep=0, ops=set(), inb=0))
for t, e in evs.items():
    if t not in pipe_of: continue
    e.sort(key=lambda x: x[0]); s = st[pipe_of[t]]; s["n"] += 1
    s["a"] = min(s["a"], e[0][0]); s["b"] = max(s["b"], e[-1][0])
    k = {kind: ts for ts, kind, _ in e}
    for i, (ts, kind, body) in enumerate(e):
        if kind == "Computing":
            s["ops"].add(body.get("current_operator_id"))
            if i + 1 < len(e): s["comp"] += e[i + 1][0] - ts
        if kind == "Preparing":
            s["inb"] += body.get("input_bytes", 0)
    if "Reserving" in k and "Preparing" in k: s["wait"] += k["Preparing"] - k["Reserving"]
for p, s in sorted(st.items(), key=lambda kv: (min(kv[1]["ops"]) if kv[1]["ops"] else 999)):
    print("ops %-14s tasks %5d  %6.1f-%6.1f s  wait %6.1f comp %7.1f  in %7.1f GB" % (sorted(s["ops"]), s["n"], (s["a"]-t0)/1e9, (s["b"]-t0)/1e9, s["wait"]/1e9, s["comp"]/1e9, s["inb"]/1e9))
