#!/usr/bin/env python3
"""Markdown report of an SF30000 sf30k_ab.sh run: per-query cold/hot, answers, spill, memory,
totals, and the ratio to the SF10000 best unpinned run.
usage: report.py RUN_DIR SF10K_RESULTS_JSONL OUT_MD"""
import glob
import json
import os
import re
import sys

run_dir, sf10k_path, out_md = sys.argv[1:4]
peak_re = re.compile(r"\[host_pool\] \w+:\d \w+ instance=(\S+) .*?peak=(\d+)")
disk_re = re.compile(r"to_disk: \d+/(\d+)")

mem = []
for line in open(f"{run_dir}/numa_mem.log"):
    f = re.findall(r"node\d_free=(\d+)G", line)
    if f:
        mem.append((line.split()[0], [int(v) for v in f]))
win = {}
for line in open(f"{run_dir}/run.log.txt"):
    m = re.match(r"(\S+) Q(\d+) \S+ r\d+ (start|errors=)", line)
    if m:
        win.setdefault(int(m[2]), {})["start" if m[3] == "start" else "end"] = m[1]


def tsec(t):
    h, m_, s = map(int, t.split(":"))
    return h * 3600 + m_ * 60 + s


sf10k = {}
for line in open(sf10k_path):
    r = json.loads(line)
    sf10k[r["query"]] = r

rows = []
for qd in sorted(glob.glob(f"{run_dir}/r*-*/q*_sf30000")):
    if not os.path.exists(f"{qd}/result.json"):
        continue
    r = json.load(open(f"{qd}/result.json"))
    q = r["query"]
    errs = sum(open(f, errors="replace").read().count("Error:")
               for f in glob.glob(f"{qd}/exchange_rank_*.log"))
    peaks, to_disk = {}, 0
    for f in glob.glob(f"{qd}/logs/*.log"):
        for line in open(f, errors="replace"):
            m = peak_re.search(line)
            if m:
                peaks[m[1]] = max(peaks.get(m[1], 0), int(m[2]))
            m = disk_re.search(line)
            if m:
                to_disk += int(m[1])
    lo = None
    w = win.get(q, {})
    if "start" in w and "end" in w:
        sel = [v for t, v in mem if tsec(w["start"]) <= tsec(t) <= tsec(w["end"])]
        if sel:
            lo = [min(x[i] for x in sel) for i in range(len(sel[0]))]
    rows.append(dict(q=q, cold=r["cold_s"], warm=r["warm_s"], rows=r["rows"], rc=r["rc"],
                     same=all(s == "ok" for s in r["same_as_first"]), errs=errs,
                     peak=max(peaks.values()) / 2**30 if peaks else 0, disk=to_disk / 1e9, lo=lo,
                     c10=sf10k.get(q, {}).get("cold_s"), w10=sf10k.get(q, {}).get("warm_s")))
rows.sort(key=lambda x: x["q"])

ok = [x for x in rows if x["rc"] == 0 and x["errs"] == 0 and x["same"]]
tc, tw = sum(x["cold"] for x in ok), sum(x["warm"] for x in ok)
t10c = sum(x["c10"] for x in ok if x["c10"])
t10w = sum(x["w10"] for x in ok if x["w10"])
fmt = lambda v: f"{v:.1f}" if v is not None else "–"

out = []
out.append("# TPC-H SF30000, unpinned, distributed Sirius: 22-query run\n")
out.append(f"{len(rows)} of 22 queries reported"
           + ("" if len(rows) == 22 else " (run still in progress)") + ".\n")
out.append("Setup:\n")
out.append("- **Hardware:** 4× GB200, one rank per GPU, one host.")
out.append("- **Data:** `/scratch/prestouser/aocsa/tpch_sf30000` (12.7 TB Parquet on GPFS, O_DIRECT).")
out.append("- **Build:** `perf/sf10k-pinned-repro` plus the Q21 chunked-build fix.")
out.append("- **Config:** p180 (180Gi host pool per rank, 1 TB disk spill per rank on /scratch,")
out.append("  small-read cap 16 below 4 MiB, 4 GiB bloom cap).")
out.append("- **Runs:** each query in fresh processes, twice: cold, then hot.")
out.append("- **Answers:** run 2 is compared with run 1, since no SF30000 reference exists.\n")
out.append("Column notes:\n")
out.append("- **Rows:** result rows.")
out.append("- **Disk spill:** host→disk bytes summed over both runs and all ranks.")
out.append("- **Host peak:** the largest per-rank host-pool peak.")
out.append("- **Min free:** the lowest free memory on NUMA node 0 / node 1 during the query.")
out.append("- **×SF10K:** the ratio to the SF10000 best unpinned run (806.7 s cold / 507.3 s hot).\n")
out.append("| Query | Cold (s) | Hot (s) | ×SF10K cold | ×SF10K hot | Rows | Answers | Disk spill | Host peak | Min free |")
out.append("|---|---:|---:|---:|---:|---:|---|---:|---:|---|")
for x in rows:
    ans = "consistent" if x["same"] and x["rc"] == 0 and x["errs"] == 0 else f"**FAIL** rc={x['rc']} errors={x['errs']}"
    rc = f"{x['cold'] / x['c10']:.2f}" if x["c10"] else "–"
    rw = f"{x['warm'] / x['w10']:.2f}" if x["w10"] and x["warm"] else "–"
    lo = "/".join(f"{v}G" for v in x["lo"]) if x["lo"] else "–"
    out.append(f"| Q{x['q']} | {fmt(x['cold'])} | {fmt(x['warm'])} | {rc} | {rw} | {x['rows']:,} | {ans} | "
               f"{x['disk']:.0f} GB | {x['peak']:.0f} GiB | {lo} |")
out.append(f"| **Total ({len(ok)} queries)** | **{tc:.1f}** | **{tw:.1f}** | "
           f"**{tc / t10c:.2f}** | **{tw / t10w:.2f}** | | | "
           f"**{sum(x['disk'] for x in rows):.0f} GB** | | |")
out.append("")
out.append(f"- **SF10000 best, same queries:** {t10c:.1f} s cold, {t10w:.1f} s hot.")
out.append(f"- **Failures:** {len(rows) - len(ok)}. Guard kills: "
           f"{sum(1 for l in open(f'{run_dir}/numa_mem.log') if 'GUARD' in l)}.")
open(out_md, "w").write("\n".join(out) + "\n")
print("\n".join(out))
