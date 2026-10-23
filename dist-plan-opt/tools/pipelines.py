#!/usr/bin/env python3
"""Per-pipeline timeline of one rank's Quent telemetry, for the Nth query in it.

usage: pipelines.py RANK_TELEMETRY_DIR [query_index=2]
Per pipeline: first task start / last task end (s from query start), tasks, summed compute
(Computing -> Finalizing) and summed prepare (Preparing -> Computing) seconds.
"""
import collections, glob, json, sys

d, qi = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 2

def events(kind):
    for f in glob.glob(f"{d}/{kind}/*.ndjson"):
        for line in open(f):
            e = json.loads(line)
            k = next(iter(e["data"]))
            yield e["id"], e["timestamp"], k, e["data"][k]

queries = collections.defaultdict(dict)
for i, ts, k, v in events("Query"):
    queries[i][k] = ts
qs = sorted(queries.items(), key=lambda kv: kv[1]["Init"])
qid, qt = qs[qi]
t0, t1 = qt["Executing"], qt["Finished"]

plans = {i for i, ts, k, v in events("Plan") if k == "Declaration" and v["query_id"]["target"] == qid}
pipes = {i: v["label"] for i, ts, k, v in events("Operator")
         if k == "Declaration" and v["plan_id"]["target"] in plans}

tasks = collections.defaultdict(dict)
for i, ts, k, v in events("Task"):
    t = tasks[i]
    if k == "Created": t["pipe"] = v["pipeline_uuid"]["target"]; t["created"] = ts
    elif k == "Preparing": t["prep"] = ts
    elif k == "Computing": t.setdefault("comp", ts)
    elif k == "Finalizing": t["fin"] = ts

stats = {}
for t in tasks.values():
    p = t.get("pipe")
    if p not in pipes or "fin" not in t: continue
    s = stats.setdefault(p, {"n": 0, "start": 1e30, "end": 0, "compute": 0, "prep": 0})
    s["n"] += 1
    start = t.get("comp", t["fin"])
    s["start"] = min(s["start"], start); s["end"] = max(s["end"], t["fin"])
    s["compute"] += t["fin"] - start
    if "prep" in t: s["prep"] += start - t["prep"]

print(f"query {qi}: {(t1 - t0) / 1e9:.2f} s")
print(f"{'start':>7} {'end':>7} {'tasks':>6} {'compute':>8} {'prep':>7}  pipeline")
for p, s in sorted(stats.items(), key=lambda kv: kv[1]["start"]):
    print(f"{(s['start'] - t0) / 1e9:7.2f} {(s['end'] - t0) / 1e9:7.2f} {s['n']:6d} "
          f"{s['compute'] / 1e9:8.2f} {s['prep'] / 1e9:7.2f}  {pipes[p][:110]}")
