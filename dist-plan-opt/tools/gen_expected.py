#!/usr/bin/env python3
"""DuckDB CPU answers for the TPC-H queries, in the oracle format.

usage: gen_expected.py DATA OUT SF [queries=1,...,22] [--repo SIRIUS_CHECKOUT]
--repo (default $SIRIUS_REPO, else /tmp/aocsa/wt/dist-plan-opt): the Sirius checkout whose
test/tpch_performance/queries.py gives the SQL (the same text run22_dpo.py runs). Skips queries
whose OUT/qNN.json already exists, so it resumes after an interruption.
"""
import argparse, datetime, decimal, json, os, sys, time
import duckdb
ap = argparse.ArgumentParser()
ap.add_argument("data"); ap.add_argument("out"); ap.add_argument("sf")
ap.add_argument("queries", nargs="?", default=",".join(str(i) for i in range(1, 23)))
ap.add_argument("--repo", default=os.environ.get("SIRIUS_REPO", "/tmp/aocsa/wt/dist-plan-opt"))
a = ap.parse_args()
data, out, sf = a.data, a.out, a.sf
qs = [int(x) for x in a.queries.split(",")]
sys.path.insert(0, f"{a.repo}/test/tpch_performance")
from queries import queries_for_scale_factor
QS = queries_for_scale_factor(sf)
os.makedirs(out, exist_ok=True)
c = duckdb.connect()
for t in ["customer", "orders", "lineitem", "supplier", "nation", "region", "part", "partsupp"]:
    c.execute(f"create view {t} as select * from read_parquet('{data}/{t}/*.parquet')")
def conv(v):
    if isinstance(v, decimal.Decimal): return float(v)
    if isinstance(v, (datetime.date, datetime.datetime)): return v.isoformat()
    return v
for q in qs:
    f = f"{out}/q{q:02d}.json"
    if os.path.exists(f): continue
    text = QS[f"q{q}"]
    t0 = time.time()
    cur = c.execute(text)
    rows = [[conv(v) for v in r] for r in cur.fetchall()]
    json.dump({"names": [d[0] for d in cur.description], "rows": rows}, open(f, "w"))
    print(f"q{q:02d} {len(rows)} rows {time.time()-t0:.1f}s", flush=True)
