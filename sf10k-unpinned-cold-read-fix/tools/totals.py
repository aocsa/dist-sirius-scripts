#!/usr/bin/env python3
"""Per-query and total cold/warm times of one or more runs, side by side.

usage: totals.py RUN [RUN ...]
  RUN is a results.jsonl file, or a directory searched recursively for results.jsonl files
  (one per query, as run_query.py writes them). The last line for each query wins.

example: tools/totals.py results/sf10k-unpinned-original-integ.jsonl results/sf10k-unpinned-cap.jsonl
"""
import glob
import json
import os
import sys


def load(path):
    files = sorted(glob.glob(os.path.join(path, "**", "results.jsonl"), recursive=True)) if os.path.isdir(path) else [path]
    rows = {}
    for f in files:
        for line in open(f):
            r = json.loads(line)
            rows[r["query"]] = r
    return rows


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    runs = [(os.path.basename(p.rstrip("/")), load(p)) for p in sys.argv[1:]]
    queries = sorted(set().union(*(r.keys() for _, r in runs)))
    print("query  " + "  ".join(f"{name[:28]:>28}" for name, _ in runs))
    print("       " + "  ".join(f"{'cold':>13} {'warm':>14}" for _ in runs))
    for q in queries:
        cells = []
        for _, rows in runs:
            r = rows.get(q)
            if r is None or r.get("cold_s") is None:
                cells.append(f"{'-':>13} {'-':>14}")
                continue
            ok = all(v == "ok" for v in r.get("vs_ref", []))
            cells.append(f"{r['cold_s']:>12.1f}s {r['warm_s']:>12.1f}s{' ' if ok else '*'}")
        print(f"Q{q:02d}    " + "  ".join(cells))
    totals = []
    for _, rows in runs:
        done = [r for r in rows.values() if r.get("cold_s") is not None and r.get("warm_s") is not None]
        totals.append(f"{sum(r['cold_s'] for r in done):>12.1f}s {sum(r['warm_s'] for r in done):>12.1f}s ")
    print("total  " + "  ".join(totals))
    print("* answer differs from the reference (Q3 and Q18 differ only in tied rows at SF10000)")


if __name__ == "__main__":
    main()
