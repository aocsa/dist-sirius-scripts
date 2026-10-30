#!/usr/bin/env python3
"""The per-query table of an SF30000 run on 8 ranks, against the 4-GPU run, as Markdown.

usage: report.py <run dir>     (e.g. runs/sf30k; reads results.jsonl, run.log, numa-*.log, q*/logs)
"""
import glob
import json
import os
import re
import sys

here = os.path.dirname(os.path.abspath(__file__))
run = sys.argv[1]
hosts = {"10.87.140.52": "gcn-17", "10.87.140.53": "gcn-18"}

old = {}
for line in open(f"{here}/../results/sf30k-4gpu.md"):
    m = re.match(r"\| Q(\d+) \| ([0-9.]+) \| ([0-9.]+) \|.*\| (\d+) GB \| (\d+) GiB \| (\S+) \|$", line)
    if m:
        old[int(m[1])] = dict(cold=float(m[2]), warm=float(m[3]), spill=int(m[4]), peak=int(m[5]), free=m[6])

res = {}
for line in open(f"{run}/results.jsonl"):
    r = json.loads(line)
    res[r["query"]] = r  # the last run of a query wins

windows = {}
for line in open(f"{run}/run.log"):
    m = re.match(r"(\d\d:\d\d:\d\d) Q(\d+) (start|end)", line)
    if m:
        windows.setdefault(int(m[2]), {})[m[3]] = m[1]


def secs(t):
    h, mi, s = map(int, t.split(":"))
    return h * 3600 + mi * 60 + s


def min_free(q):
    w = windows.get(q, {})
    if "start" not in w or "end" not in w:
        return "–"
    a, b = secs(w["start"]), secs(w["end"])
    out = []
    for ip, name in hosts.items():
        lo = {}
        for line in open(f"{run}/numa-{ip}.log"):
            f = line.split()
            if len(f) < 2 or not re.match(r"\d\d:\d\d:\d\d$", f[0]) or not a <= secs(f[0]) <= b:
                continue
            for kv in f[1:]:
                k, v = kv.split("=")
                lo[k] = min(lo.get(k, 10**9), int(v.rstrip("G")))
        out.append(f"{lo.get('node0_free', '?')}G/{lo.get('node1_free', '?')}G")
    return " · ".join(out)


pool = re.compile(r"\[host_pool\] (\w+:\d) \w+ instance=(\S+) .*?peak=(\d+)")


def mem(q):
    to_disk, peak = 0, 0
    for f in glob.glob(f"{run}/q{q:02d}/logs/*/*.log"):
        for line in open(f, errors="replace"):
            m = re.search(r"to_disk: \d+/(\d+)", line)
            if m:
                to_disk += int(m[1])
            m = pool.search(line)
            if m:
                peak = max(peak, int(m[3]))
    return to_disk, peak


def answers(r):
    if all(x == "ok" for x in r["vs_ref"]) and all(x == "ok" for x in r["same_as_first"]):
        return "match"
    if all(x == "same size" for x in r["vs_ref"]):
        return "same row count (size only)"
    return f"run1 {r['vs_ref'][0]}, run2 {r['vs_ref'][-1]}"


print("| Query | Cold (s) | Warm (s) | 4-GPU cold / warm (s) | Speedup cold / warm | Rows | Answers vs 4-GPU | Disk spill (4-GPU) | Host-pool peak (4-GPU) | Min free, gcn-17 · gcn-18 |")
print("|---|---:|---:|---:|---:|---:|---|---:|---:|---|")
tot = dict(cold=0.0, warm=0.0, ocold=0.0, owarm=0.0, spill=0, ospill=0)
for q in range(1, 23):
    r, o = res.get(q), old.get(q)
    if not r or not o:
        print(f"| Q{q} | – | – | – | – | – | not run | – | – | – |")
        continue
    ok = r["rc"] == 0 and len(r["times_s"]) == 2
    cold, warm = (r["times_s"] + [float("nan")] * 2)[:2]
    d, p = mem(q)
    if ok:
        tot["cold"] += cold
        tot["warm"] += warm
        tot["ocold"] += o["cold"]
        tot["owarm"] += o["warm"]
    tot["spill"] += d
    tot["ospill"] += o["spill"] * 10**9
    print(
        f"| Q{q} | {cold:.1f} | {warm:.1f} | {o['cold']:.1f} / {o['warm']:.1f} | "
        f"{o['cold'] / cold:.2f}× / {o['warm'] / warm:.2f}× | {r['rows'][0]:,} | "
        f"{answers(r) if ok else 'FAILED rc=' + str(r['rc'])} | {d / 1e9:.0f} GB ({o['spill']} GB) | "
        f"{p / 2**30:.0f} GiB ({o['peak']} GiB) | {min_free(q)} |"
    )
print(
    f"| **Total** | **{tot['cold']:.1f}** | **{tot['warm']:.1f}** | **{tot['ocold']:.1f} / {tot['owarm']:.1f}** | "
    f"**{tot['ocold'] / max(tot['cold'], 1e-9):.2f}× / {tot['owarm'] / max(tot['warm'], 1e-9):.2f}×** | | | "
    f"**{tot['spill'] / 1e9:.0f} GB ({tot['ospill'] / 1e9:.0f} GB)** | | |"
)
walls = sum(r["wall_s"] for r in res.values())
print(f"\nWall time over the {len(res)} queries, startup and teardown included: {walls:.0f} s ({walls / 60:.0f} min).")
