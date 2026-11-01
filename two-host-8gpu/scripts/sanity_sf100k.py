#!/usr/bin/env python3
"""Checks SF100000 answers that have no reference, from facts of the data and the SF30000 answers.

usage: sanity_sf100k.py <run dir>
- Q13: the custdist column sums to the customer count, SF x 150,000 = 15,000,000,000.
- Q2: 100 rows. Q11: about 10/3 x the SF30000 row count.
- Q16: the supplier_cnt column sums to about 10/3 x SF30000's. Q22: numcust per country code is
  about 10/3 x SF30000's.
- Queries with fixed groups (Q1, Q4-Q9, Q12, Q14, Q17, Q19): rows matched to SF30000's by group
  key; sums and counts about 10/3 x SF30000's, averages and shares about equal.
- Top-N queries (Q3, Q10, Q18, Q21): the full N rows, sorted. Q15: at least one row.
  Q20: both runs agree (its row count follows no scale rule).
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


# query: (group key columns, columns about 10/3 x SF30000, columns about equal)
grouped = {
    1: (["l_returnflag", "l_linestatus"],
        ["sum_qty", "sum_base_price", "sum_disc_price", "sum_charge", "count_order"],
        ["avg_qty", "avg_price", "avg_disc"]),
    4: (["o_orderpriority"], ["order_count"], []),
    5: (["n_name"], ["revenue"], []),
    6: ([], ["revenue"], []),
    7: (["supp_nation", "cust_nation", "l_year"], ["revenue"], []),
    8: (["o_year"], [], ["mkt_share"]),
    9: (["nation", "o_year"], ["sum_profit"], []),
    12: (["l_shipmode"], ["high_line_count", "low_line_count"], []),
    14: ([], [], ["promo_revenue"]),
    17: ([], ["avg_yearly"], []),
    19: ([], ["revenue"], []),
}
# query: (rows, column sorted descending)
top_n = {3: (10, "revenue"), 10: (20, "revenue"), 18: (100, "o_totalprice"), 21: (100, "numwait")}


def check_grouped(q, runs):
    keys, scaled, same = grouped[q]
    rows, header = old(q)[:2]
    ref = {tuple(r[header.index(k)] for k in keys): r for r in rows}
    worst, missing = 0.0, 0
    for got, h in (r[:2] for r in runs):
        missing += abs(len(got) - len(ref))
        for r in got:
            o = ref.get(tuple(r[h.index(k)] for k in keys))
            if o is None:
                missing += 1
                continue
            for c, want in [(c, ratio) for c in scaled] + [(c, 1.0) for c in same]:
                worst = max(worst, abs(float(r[h.index(c)]) / (float(o[header.index(c)]) * want) - 1))
    report(q, missing == 0 and worst <= 0.02,
           f"{len(rows)} groups matched{'' if missing == 0 else f', {missing} missing or extra'}; "
           f"values within {worst:.2%} of SF30000 (sums x 10/3)")


def check_top_n(q, runs):
    n, c = top_n[q]
    sizes = [len(r[0]) for r in runs]
    ordered = all(
        all(a >= b for a, b in zip(v, v[1:]))
        for v in (col(r[0], r[1], c) for r in runs)
    )
    report(q, all(s == n for s in sizes) and ordered, f"rows {sizes} (want {n}), {c} descending: {ordered}")


for q in sorted(res):
    log = f"{run}/q{q:02d}/rank_0.log"
    if res[q]["rc"] != 0:
        report(q, False, f"run failed (rc {res[q]['rc']}, times {res[q]['times_s']}, rows {res[q]['rows']})")
        continue
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
    if q == 20:
        # Its row count follows no scale rule: DuckDB's CPU answers are 177 rows per SF unit at
        # SF100 and 111 at SF1000, and a checked SF10000 run gave 38. Only the runs' agreement is checked.
        r = res[20]
        report(20, len(set(r["rows"])) == 1 and r["same_as_first"][-1] == "same rows+checksum",
               f"rows {r['rows']}, runs agree: {r['same_as_first']} (no scale rule to check against)")
        continue
    if not os.path.exists(log):
        continue
    runs = answers(log)
    if q in grouped:
        check_grouped(q, runs)
    elif q in top_n:
        check_top_n(q, runs)
    elif q == 15:
        report(15, all(len(r[0]) >= 1 for r in runs), f"rows {[len(r[0]) for r in runs]}")
    elif q == 2:
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
