"""Best set of pinned columns per memory budget for the 22 SF10000 queries in one long-running process.

Pinned size per rank (GB, v2 plans) is calibrated on measured pins:
  lineitem 4 cols 105.7 (E22), 6 cols 134.1 (P2), 14 cols 281 (SF3000 v2 x 10/3); orders 4 cols 10.5 (P2),
  7 cols 37.3 (E22); customer 24.3, part 10.3 (SF3000 v2 x 10/3); partsupp 8.1, part 5 cols 1.4 (P2).
A table's pin serves a query's scan only if it holds every column that scan reads.
Query time: U (unpinned, one process: cap-sweep warm x 0.89) when nothing it reads is pinned,
P (SF3000 v2 pinned warm x 10/3) when everything is, and in between in proportion to Parquet bytes served."""
import itertools, json, os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.environ.get("SIRIUS", "/tmp/aocsa/wt/dpo-rank-pin"), "bench/dist-tpch"))
from columns import cols, SIZES

L = dict(l_orderkey=10.2, l_partkey=55, l_suppkey=47, l_quantity=26, l_extendedprice=47, l_discount=8,
         l_tax=8, l_returnflag=4, l_linestatus=2, l_shipdate=24, l_commitdate=24, l_receiptdate=24,
         l_shipinstruct=4, l_shipmode=6)
O = dict(o_orderkey=0.2, o_orderstatus=1.2, o_orderdate=7.3, o_orderpriority=1.8, o_custkey=15.5,
         o_totalprice=11.3, o_shippriority=0.1)
C = dict(c_custkey=0.1, c_name=1.7, c_address=8.7, c_nationkey=0.2, c_phone=3.9, c_acctbal=0.9,
         c_mktsegment=0.1, c_comment=8.7)
PA = dict(p_partkey=0.2, p_name=8.4, p_mfgr=0.2, p_brand=0.3, p_type=0.4, p_size=0.3, p_container=0.5)
PS = dict(ps_partkey=2.5, ps_suppkey=2.5, ps_availqty=0.4, ps_supplycost=2.7)
FIXED = 2.3  # supplier (rank share), nation, region: always pinned

def load(p, tag=None):
    d = {}
    for l in open(p):
        r = json.loads(l)
        if tag is None or r["tag"] == tag:
            d[r["query"]] = r
    return d
import glob
cap = {}
for f in glob.glob("/tmp/aocsa/tpch22/dpo-best/sf10k-22-cap4m/r*/results.jsonl"):
    cap.update(load(f))
v2 = load("/tmp/aocsa/tpch22/pin10k/sf3k-v2/results.jsonl", "_v2")
Q = list(range(1, 23))
U = np.array([cap[q]["warm_s"] * 0.89 for q in Q])
P = np.array([v2[q]["warm_s"] * 10 / 3 for q in Q])
need = {q: {t: frozenset(c) for t, c in cols(q, 10000).items()} for q in Q}
total = np.array([sum(SIZES[t][c] for t, cs in need[q].items() for c in cs) for q in Q])

def options(table, sizes, allsets):
    """Every union of the column sets queries read from this table (plus none)."""
    reqs = sorted({need[q][table] for q in Q if table in need[q] and need[q][table] <= sizes.keys()}, key=len)  # o_comment is never pinned
    unions = {frozenset()}
    for r in reqs:
        unions |= {u | r for u in unions}
    out = []
    for u in unions:
        size = sum(sizes[c] for c in u)
        served = np.array([sum(SIZES[table][c] for c in need[q][table]) if table in need[q] and need[q][table] <= u else 0 for q in Q])
        out.append((size, served, u))
    return out

tabs = [options("lineitem", L, None), options("orders", O, None), options("customer", C, None),
        options("part", PA, None), options("partsupp", PS, None)]
# small tables always pinned: supplier, nation, region
fixed_served = np.array([sum(SIZES[t][c] for t in ("supplier", "nation", "region") if t in need[q] for c in need[q][t]) for q in Q])
print("options per table:", [len(t) for t in tabs], file=sys.stderr)

def best(budget):
    rest = [(sum(x[0] for x in combo), sum(x[1] for x in combo), [x[2] for x in combo])
            for combo in itertools.product(*tabs[1:])]
    rest = [r for r in rest if r[0] + FIXED <= budget]
    rsize = np.array([r[0] for r in rest]); rserved = np.array([r[1] for r in rest])
    top = (None, 1e9)
    for lsize, lserved, lcols in tabs[0]:
        ok = rsize + lsize + FIXED <= budget
        if not ok.any():
            continue
        served = rserved[ok] + lserved + fixed_served
        t = (P + (U - P) * (1 - served / total)).sum(axis=1)
        i = t.argmin()
        if t[i] < top[1]:
            idx = np.flatnonzero(ok)[i]
            top = ((lsize + rsize[idx] + FIXED, lcols, rest[idx][2], P + (U - P) * (1 - served[i] / total)), t[i])
    return top

print(f"unpinned one process {U.sum():.0f} s; everything pinned {P.sum():.0f} s; all needed columns "
      f"{sum(L.values()) + sum(O.values()) + sum(C.values()) + sum(PA.values()) + sum(PS.values()) + FIXED:.0f} GB/rank")
for budget in [int(b) for b in sys.argv[1:]]:
    (size, lc, rc, per), t = best(budget)
    names = ["orders", "customer", "part", "partsupp"]
    print(f"\n== budget {budget} GB/rank: {t:.0f} s, uses {size:.0f} GB")
    print("  lineitem:", sorted(lc))
    for n, c in zip(names, rc):
        print(f"  {n}:", sorted(c))
    print("  slowest:", sorted(((round(x, 1), q) for x, q in zip(per, Q)), reverse=True)[:6])


def two_tier(host, gpu):
    """lineitem pinned twice: A on the host with the small tables, B on the GPU. A query's lineitem
    scan is served when A or B holds all its columns."""
    lopts = tabs[0]
    lserved_ok = [np.array([table_ok for table_ok in (need[q].get("lineitem", frozenset()) <= u and "lineitem" in need[q] for q in Q)]) for _, _, u in lopts]
    lbytes = np.array([sum(SIZES["lineitem"][c] for c in need[q].get("lineitem", ())) for q in Q])
    pick = lambda opts, keep: [o for o in opts if sorted(o[2]) in [sorted(k) for k in keep]]
    orders = pick(tabs[1], [[], ["o_orderdate", "o_orderkey", "o_orderpriority", "o_orderstatus"],
                            ["o_custkey", "o_orderdate", "o_orderkey", "o_orderpriority", "o_orderstatus", "o_shippriority"],
                            list(O)])
    cust = pick(tabs[2], [[], ["c_custkey", "c_nationkey"], ["c_custkey", "c_mktsegment", "c_name", "c_nationkey"], list(C)])
    part = pick(tabs[3], [[], ["p_brand", "p_mfgr", "p_partkey", "p_size", "p_type"], list(PA)])
    ps = pick(tabs[4], [list(PS)])
    rest = [(sum(x[0] for x in c), sum(x[1] for x in c), [x[2] for x in c]) for c in itertools.product(orders, cust, part, ps)]
    top = (None, 1e9)
    for ia, (sa, _, ua) in enumerate(lopts):
        for ib, (sb, _, ub) in enumerate(lopts):
            if sb > gpu or sa > host:
                continue
            lserved = np.where(lserved_ok[ia] | lserved_ok[ib], lbytes, 0)
            for rs, rserved, rc in rest:
                if sa + rs + FIXED > host:
                    continue
                served = lserved + rserved + fixed_served
                per = P + (U - P) * (1 - served / total)
                t = per.sum()
                if t < top[1]:
                    top = ((sa + rs + FIXED, sb, ua, ub, rc, per), t)
    return top


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "two":
    pass
