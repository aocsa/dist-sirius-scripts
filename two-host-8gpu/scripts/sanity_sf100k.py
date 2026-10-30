#!/usr/bin/env python3
"""Checks SF100000 answers that have no reference, from facts of the data and the SF30000 answers.

usage: sanity_sf100k.py <run dir>
- Q13: the custdist column sums to the customer count, SF x 150,000 = 15,000,000,000.
- Q2: 100 rows. Q11: about 10/3 x the SF30000 row count.
- Q16: the supplier_cnt column sums to about 10/3 x SF30000's. Q22: numcust per country code is
  about 10/3 x SF30000's.
"About" is within 2%: these come from uniform random data, not exact ratios.
"""
import json
import os
import re
import sys

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
from results import parse  # noqa: E402

run = sys.argv[1]
glog = re.compile(r"^[EWIF]\d{4} \d\d:\d\d:\d\d")
ratio = 100000 / 30000


def answers(path):
    text = "\n".join(l for l in open(path, errors="replace").read().splitlines() if not glog.match(l))
    return parse(text)


def near(x, want, tol=0.02):
    return abs(x / want - 1) <= tol


def col(rows, header, name):
    return [float(r[header.index(name)]) for r in rows]


res = {}
for line in open(f"{run}/results.jsonl"):
    r = json.loads(line)
    res[r["query"]] = r
refs = os.environ.get("REF_DIR", f"{here}/../refs/sf30k")
old = lambda q: answers(f"{refs}/q{q:02d}_sf30000.log")[-1]


def report(q, ok, detail):
    print(f"Q{q}: {'ok' if ok else 'CHECK'} - {detail}")


for q in sorted(res):
    log = f"{run}/q{q:02d}/rank_0.log"
    if q == 11:
        # SF30000's text rounds the fraction 0.0001/SF to 0.0000000033; SF100000's is exact. Count
        # the SF30000 rows above the exact threshold (its smallest value sits just above the rounded one).
        vals = [float(r[1]) for r in old(11)[0]]
        exact = min(vals) * (1e-4 / 30000) / 3.3e-9
        want = sum(v > exact for v in vals)
        rows = res[11]["rows"]
        report(11, all(near(n, want * ratio) for n in rows),
               f"rows {rows}, SF30000 rows above the exact threshold {want:,} x 10/3 = {want * ratio:,.0f}")
        continue
    runs = answers(log)
    if q == 2:
        report(2, all(len(r[0]) == 100 for r in runs), f"rows {[len(r[0]) for r in runs]}")
    elif q == 13:
        sums = [sum(col(r[0], r[1], "custdist")) for r in runs]
        report(13, all(s == 15_000_000_000 for s in sums), f"sum(custdist) {[f'{s:,.0f}' for s in sums]}, want 15,000,000,000")
    elif q == 16:
        o = sum(col(*old(16)[:2], "supplier_cnt"))
        sums = [sum(col(r[0], r[1], "supplier_cnt")) for r in runs]
        report(16, all(near(s, o * ratio) for s in sums), f"sum(supplier_cnt) {[f'{s:,.0f}' for s in sums]}, SF30000 x 10/3 = {o * ratio:,.0f}")
    elif q == 22:
        o = dict(zip(*[[r[0] for r in old(22)[0]], col(*old(22)[:2], "numcust")]))
        worst = 0.0
        for r in runs:
            n = dict(zip([x[0] for x in r[0]], col(r[0], r[1], "numcust")))
            worst = max([worst] + [abs(n.get(k, 0) / (v * ratio) - 1) for k, v in o.items()])
        report(22, worst <= 0.02, f"numcust per country code within {worst:.2%} of SF30000 x 10/3")
