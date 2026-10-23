#!/usr/bin/env python3
"""Bytes and batches produced by one pipeline (by label regex) in the Nth query. usage: scanbytes.py RANK_DIR q regex"""
import collections, glob, json, re, sys
d, qi, rx = sys.argv[1], int(sys.argv[2]), re.compile(sys.argv[3])
def ev(kind):
    for f in glob.glob(f"{d}/{kind}/*.ndjson"):
        for l in open(f):
            e = json.loads(l); k = next(iter(e["data"])); yield e["id"], e["timestamp"], k, e["data"][k]
q = collections.defaultdict(dict)
for i, ts, k, v in ev("Query"): q[i][k] = ts
qid = sorted(q.items(), key=lambda kv: kv[1]["Init"])[qi][0]
plans = {i for i, ts, k, v in ev("Plan") if k == "Declaration" and v["query_id"]["target"] == qid}
pipes = {i for i, ts, k, v in ev("Operator") if k == "Declaration" and v["plan_id"]["target"] in plans and rx.search(v["label"])}
mine, size = set(), {}
for i, ts, k, v in ev("DataBatch"):
    if k == "Constructed" and v["producer_pipeline_id"]["target"] in pipes: mine.add(i)
    if "memory" in v and isinstance(v.get("memory"), dict): size[i] = max(size.get(i, 0), (v["memory"].get("data") or {}).get("bytes", 0))
print(len(mine), "batches", sum(size.get(i, 0) for i in mine) / 1e9, "GB")
