#!/usr/bin/env python3
"""Per-rank pin-size model for TPC-H SF10000 on 4 ranks, and estimated warm times per pin set.

Sizes: rows from the dataset's Parquet footers (footers.json); raw = cuDF bytes after pin_table's
narrowing; comp = bits/row implied by the repo's tpch_sf1000 Simpatico plan ratios (bitpack widths
re-derived for SF10000 key ranges). 'imp' = with proposed plan changes. Estimates, not measurements.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get('SIRIUS', '/tmp/aocsa/wt/dpo-rank-pin') + "/bench/dist-tpch")
from columns import cols  # noqa: E402

R = 4
FOOT = json.load(open(os.path.join(HERE, "footers.json")))
# column: (table, raw bytes/row after narrowing, comp bits/row current plan, comp bits/row improved,
#          per-rank multiplier: 1/R for rank share, 1 for replicated)
C = {
    "l_orderkey": (8, 5.2, 5.2), "l_partkey": (4, 31, 31), "l_suppkey": (4, 27, 27),
    "l_quantity": (4, 13.1, 13.1), "l_extendedprice": (4, 24.1, 24.1), "l_discount": (4, 4.1, 4.1),
    "l_tax": (4, 4.1, 4.1), "l_returnflag": (5, 2.1, 2.1), "l_linestatus": (5, 1.1, 1.1),
    "l_shipdate": (2, 12.1, 12.1), "l_commitdate": (2, 12.1, 12.1), "l_receiptdate": (2, 12.1, 12.1),
    "l_shipinstruct": (16, 2.1, 2.1), "l_shipmode": (8.3, 39, 3.1),
    "o_orderkey": (8, 5.2, 5.2), "o_custkey": (4, 31, 31), "o_orderstatus": (5, 2.1, 2.1),
    "o_totalprice": (4, 26.1, 26.1), "o_orderdate": (2, 12.1, 12.1), "o_orderpriority": (12.4, 3.1, 3.1),
    "o_shippriority": (1, 0.1, 0.1), "o_comment": (52.9, 52.9 * 8, 52.9 * 8 / 2.6),
    "ps_partkey": (4, 0.3, 0.3), "ps_suppkey": (4, 0.9, 0.9), "ps_availqty": (2, 14, 14),
    "ps_supplycost": (4, 17.1, 17.1),
    # part/customer: current = raw (plans disabled); improved = the *_disabled plans enabled
    "p_partkey": (4, 32, 0.1), "p_name": (37.3, 37.3 * 8, 112), "p_mfgr": (18, 144, 3.1),
    "p_brand": (12, 96, 5.1), "p_type": (24.6, 24.6 * 8, 8.1), "p_size": (1, 8, 6.1),
    "p_container": (11.6, 11.6 * 8, 6),
    "c_custkey": (4, 32, 0.2), "c_name": (24.3, 24.3 * 8, 44.5), "c_address": (30.7, 30.7 * 8, 170),
    "c_nationkey": (1, 8, 5), "c_phone": (20.9, 20.9 * 8, 72), "c_acctbal": (4, 32, 21),
    "c_mktsegment": (12.6, 12.6 * 8, 3), "c_comment": (77.1, 77.1 * 8, 223),
    # supplier is replicated: whole table per rank
    "s_suppkey": (4, 0.1, 0.1), "s_name": (24, 107, 107), "s_address": (31, 173, 173),
    "s_nationkey": (1, 5, 5), "s_phone": (21, 67, 67), "s_acctbal": (4, 21, 21),
    "s_comment": (67, 335, 335),
}
TBL = {"l": "lineitem", "o": "orders", "ps": "partsupp", "p": "part", "c": "customer", "s": "supplier"}


def table(c):
    return TBL[c.split("_")[0]]


def rows_per_rank(c):
    t = table(c)
    n = FOOT[t]["rows"]
    return n if t == "supplier" else n / R


def gb(c, kind):
    raw, cur, imp = C[c]
    per_row = {"raw": raw, "cur": cur / 8, "imp": imp / 8}[kind]
    return rows_per_rank(c) * per_row / 1e9


def pq(c):
    t = table(c)
    b = FOOT[t]["cols"][c]["pq_bytes"]
    return b if t == "supplier" else b / R


# Warm seconds today (cap build, 806.7/507.3 run is not per-query; use 4 MiB-block sweep, same totals
# within noise) and SF3000 all-pinned warm (dpo) scaled x10/3.
UNPIN = dict(zip(range(1, 23), [24.7, 7.7, 26.6, 18.4, 28.7, 21.7, 29.1, 30.3, 36.6, 28.1, 9.3, 20.6,
                                14.4, 19.8, 19.1, 3.6, 29.9, 36.6, 26.1, 21.3, 53.2, 4.6]))
UNPIN_COLD = dict(zip(range(1, 23), [25.2, 11.8, 45.1, 34.0, 48.6, 30.1, 45.0, 49.3, 52.2, 46.9, 13.0,
                                     42.4, 18.4, 35.2, 36.3, 7.7, 39.4, 56.3, 46.4, 38.0, 69.9, 6.0]))
PIN3K = dict(zip(range(1, 23), [1.17, 0.39, 0.72, 0.54, 0.76, 0.43, 0.83, 0.94, 1.86, 1.32, 1.36, 0.96,
                                3.33, 0.65, 0.63, 0.46, 0.76, 2.16, 0.95, 0.93, 1.78, 0.27]))
PIN = {q: v * 10 / 3 for q, v in PIN3K.items()}
QCOLS = {q: [c for t, cs in cols(q).items() if t not in ("nation", "region") for c in cs]
         for q in range(1, 23)}


def evaluate(pinned, kind="imp"):
    """pinned: {table: set(columns)}. A table serves a query only if it holds all that query's
    columns of it. Warm estimate: unpinned - (unpinned - pinned) * share of Parquet bytes served."""
    mem = sum(gb(c, kind) for t, cs in pinned.items() for c in cs)
    tot = 0
    full = []
    for q, qc in QCOLS.items():
        by_t = {}
        for c in qc:
            by_t.setdefault(table(c), []).append(c)
        served = sum(pq(c) for t, cs in by_t.items() if set(cs) <= pinned.get(t, set()) for c in cs)
        total = sum(pq(c) for c in qc) or 1
        share = served / total
        if share > 0.999:
            full.append(q)
        tot += UNPIN[q] - (UNPIN[q] - PIN[q]) * share
    return mem, tot, full


if __name__ == "__main__":
    print("per-column GB per rank: raw / cur / imp / parquet")
    for t in TBL.values():
        cs = [c for c in C if table(c) == t]
        for c in cs:
            print(f"  {c:16s} {gb(c,'raw'):7.1f} {gb(c,'cur'):7.1f} {gb(c,'imp'):7.1f} {pq(c)/1e9:7.1f}")
        used = sorted({c for q in QCOLS.values() for c in q if table(c) == t})
        print(f"{t:10s} used-by-22 cols: raw {sum(gb(c,'raw') for c in used):7.1f} "
              f"cur {sum(gb(c,'cur') for c in used):7.1f} imp {sum(gb(c,'imp') for c in used):7.1f} "
              f"pq {sum(pq(c) for c in used)/1e9:7.1f}  ({len(used)} cols)")
    print("unpinned warm total", sum(UNPIN.values()), "all-pinned est", round(sum(PIN.values()), 1))
