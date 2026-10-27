#!/usr/bin/env python3
"""Per run of an sf30k_ab.sh output dir: query, arm, cold/warm, rows, answers, spill, errors,
host-pool peak per rank and the lowest free memory per NUMA node while it ran.
usage: status.py OUT_DIR"""
import glob
import json
import os
import re
import sys

out = sys.argv[1]
pat = re.compile(r"\[host_pool\] (\w+:\d) \w+ instance=(\S+) .*?peak=(\d+)")
mem = []
for line in open(f"{out}/numa_mem.log") if os.path.exists(f"{out}/numa_mem.log") else []:
    f = re.findall(r"node(\d)_free=(\d+)G", line)
    if f:
        mem.append((line.split()[0], [int(v) for _, v in f]))
starts = {}
for line in open(f"{out}/run.log.txt"):
    m = re.match(r"(\S+) Q(\d+) (\S+) r(\d+) start", line)
    if m:
        starts[int(m[4])] = m[1]
ends = {}
for line in open(f"{out}/run.log.txt"):
    m = re.match(r"(\S+) Q(\d+) (\S+) r(\d+) (?:errors=\d+ )?rc=", line)
    if m:
        ends[int(m[4])] = m[1]


def tsec(t):
    h, m, s = map(int, t.split(":"))
    return h * 3600 + m * 60 + s


for d in sorted(glob.glob(f"{out}/r*-*"), key=lambda p: int(re.search(r"/r(\d+)-", p)[1])):
    n = int(re.search(r"/r(\d+)-", d)[1])
    arm = d.split("-", 1)[1] if "-" in os.path.basename(d) else "?"
    arm = os.path.basename(d).split("-", 1)[1]
    for qd in glob.glob(f"{d}/q*"):
        if not os.path.isdir(qd):
            continue
        r = json.load(open(f"{qd}/result.json")) if os.path.exists(f"{qd}/result.json") else {}
        log0 = f"{qd}/exchange_rank_0.log"
        errs = sum(open(f, errors="replace").read().count("Error:") for f in glob.glob(f"{qd}/exchange_rank_*.log"))
        peaks = {}
        to_disk = 0
        for f in glob.glob(f"{qd}/logs/*.log"):
            for line in open(f, errors="replace"):
                m = pat.search(line)
                if m:
                    peaks[m[2]] = max(peaks.get(m[2], 0), int(m[3]))
                m = re.search(r"to_disk: \d+/(\d+)", line)
                if m:
                    to_disk += int(m[1])
        lo = ""
        if n in starts and n in ends and mem:
            a, b = tsec(starts[n]), tsec(ends[n])
            w = [v for t, v in mem if a <= tsec(t) <= b]
            if w:
                lo = "min_free=" + "/".join(str(min(x[i] for x in w)) for i in range(len(w[0]))) + "G"
        print(
            f"r{n:<3} {os.path.basename(qd):<12} {arm:<10} rc={r.get('rc')} times={r.get('times_s')} "
            f"rows={r.get('rows')} same={r.get('same_as_first')} spill={r.get('spill_bytes', 0) / 1e9:.1f}GB "
            f"errors={errs} to_disk={to_disk / 1e9:.0f}GB hostpeak={'/'.join(f'{p / 2**30:.0f}' for p in peaks.values())}GiB {lo}"
        )
