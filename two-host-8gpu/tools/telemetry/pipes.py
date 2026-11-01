#!/usr/bin/env python3
"""Per-pipeline timing from one rank's Sirius telemetry, for each query run in it.

usage: pipes.py <telemetry dir of one rank, e.g. q14/telemetry_data/<uuid>>
For each pipeline: tasks, its wall span (first task created to last task finalized), and the
summed time tasks spent waiting for a memory reservation, preparing their input (moving it to the
GPU) and computing, plus the largest reservation asked for.
"""
import collections
import glob
import json
import sys

d = sys.argv[1]


def records(kind):
    for path in glob.glob(f"{d}/{kind}/*.ndjson"):
        for line in open(path):
            yield json.loads(line)


label, plan_of = {}, {}
for r in records("Operator"):
    dec = r["data"].get("Declaration")
    if dec:
        label[r["id"]] = (dec["type_name"].replace("Pipeline Id ", "#"), dec["label"])
        plan_of[r["id"]] = dec["plan_id"]["target"]

events = collections.defaultdict(dict)
pipe_of, reserve = {}, {}
for r in records("Task"):
    kind, body = next(iter(r["data"].items()))
    if kind == "Created":
        pipe_of[r["id"]] = body["pipeline_uuid"]["target"]
    if kind == "Reserving":
        reserve[r["id"]] = body["requested_bytes"]
    events[r["id"]].setdefault(kind, r["timestamp"])
    if kind == "Finalizing":
        events[r["id"]]["end"] = r["timestamp"]

stats = collections.defaultdict(lambda: dict(n=0, t0=None, t1=0, wait=0, prep=0, comp=0, res=0))
for t, e in events.items():
    if t not in pipe_of or "end" not in e:
        continue
    s = stats[pipe_of[t]]
    s["n"] += 1
    s["t0"] = e["Created"] if s["t0"] is None else min(s["t0"], e["Created"])
    s["t1"] = max(s["t1"], e["end"])
    if "Reserving" in e and "Preparing" in e:
        s["wait"] += e["Preparing"] - e["Reserving"]
    if "Preparing" in e and "Computing" in e:
        s["prep"] += e["Computing"] - e["Preparing"]
    if "Computing" in e:
        s["comp"] += e["end"] - e["Computing"]
    s["res"] = max(s["res"], reserve.get(t, 0))

for p in stats:
    label.setdefault(p, ("#999", "(no declaration flushed)"))
by_plan = collections.defaultdict(list)
for p, s in stats.items():
    by_plan[plan_of.get(p)].append((p, s))
for i, (plan, pipes) in enumerate(sorted(by_plan.items(), key=lambda kv: min(s["t0"] for _, s in kv[1]))):
    start = min(s["t0"] for _, s in pipes)
    print(f"run {i + 1}: {(max(s['t1'] for _, s in pipes) - start) / 1e9:.1f} s")
    print(f"  {'pipeline':<5} {'tasks':>6} {'from':>7} {'to':>7} {'wait':>8} {'prepare':>8} {'compute':>8} {'max res':>8}  operators")
    for p, s in sorted(pipes, key=lambda x: int(label[x[0]][0][1:])):
        print(f"  {label[p][0]:<5} {s['n']:>6} {(s['t0'] - start) / 1e9:>7.1f} {(s['t1'] - start) / 1e9:>7.1f}"
              f" {s['wait'] / 1e9:>8.1f} {s['prep'] / 1e9:>8.1f} {s['comp'] / 1e9:>8.1f} {s['res'] / 2**30:>7.1f}G  {label[p][1]}")
