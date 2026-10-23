#!/usr/bin/env python3
"""Per GPU_SCAN pipeline of the Nth query: summed task waits (s) by phase.
usage: taskwait.py RANK_DIR [query_index] [pipeline_regex]"""
import collections, glob, json, re, sys
d, qi = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 2
rx = re.compile(sys.argv[3] if len(sys.argv) > 3 else r"GPU_SCAN")
def ev(kind):
    for f in glob.glob(f"{d}/{kind}/*.ndjson"):
        for l in open(f):
            e = json.loads(l); k = next(iter(e["data"])); yield e["id"], e["timestamp"], k, e["data"][k]
q = collections.defaultdict(dict)
for i, ts, k, v in ev("Query"): q[i][k] = ts
qid, qt = sorted(q.items(), key=lambda kv: kv[1]["Init"])[qi]; t0 = qt["Executing"]
plans = {i for i, ts, k, v in ev("Plan") if k == "Declaration" and v["query_id"]["target"] == qid}
pipes = {i: v["label"] for i, ts, k, v in ev("Operator") if k == "Declaration" and v["plan_id"]["target"] in plans and rx.search(v["label"])}
tasks = collections.defaultdict(dict)
for i, ts, k, v in ev("Task"):
    t = tasks[i]
    if k == "Created": t["pipe"] = v["pipeline_uuid"]["target"]
    t.setdefault(k, ts)
order = ["Created", "Reserving", "Preparing", "Computing", "Finalizing"]
agg = collections.defaultdict(lambda: collections.Counter())
for t in tasks.values():
    p = t.get("pipe")
    if p not in pipes or "Finalizing" not in t: continue
    a = agg[p]; a["n"] += 1; a["first_created"] = min(a["first_created"] or 1e30, t["Created"] - t0)
    for x, y in zip(order, order[1:]):
        if x in t and y in t: a[f"{x[:4]}->{y[:4]}"] += t[y] - t[x]
for p, a in sorted(agg.items(), key=lambda kv: kv[1]["first_created"]):
    print(f"{pipes[p][:40]:40s} n={a['n']:3d} created@{a['first_created']/1e9:6.2f} " +
          " ".join(f"{k}={a[k]/1e9:6.2f}" for k in ["Crea->Rese", "Rese->Prep", "Prep->Comp", "Comp->Fina"]))
