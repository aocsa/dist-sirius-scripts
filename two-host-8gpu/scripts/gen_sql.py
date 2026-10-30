#!/usr/bin/env python3
"""Print the SQL a rank runs: views over DATA/<table>/*.parquet, then TPC-H Q at SF, ITERS times.

usage: gen_sql.py Q SF DATA ITERS
Query text comes from test/tpch_performance/queries.py of WT, the Sirius checkout.
Views are made only for the tables the query names. At SF100000 (lineitem still being
generated) or with NO_LINEITEM=1, a query that reads lineitem is refused.
"""
import os
import re
import sys

q, sf, data, iters = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
wt = os.environ.get("WT", "/scratch/prestouser/aocsa/wt/xnode-staging")
sys.path.insert(0, os.path.join(wt, "test", "tpch_performance"))
from queries import queries_for_scale_factor  # noqa: E402

text = queries_for_scale_factor(sf)[f"q{q}"].strip().rstrip(";")
words = set(re.findall(r"\b[a-z_]+\b", text.lower()))
tables = [
    t
    for t in ["lineitem", "orders", "customer", "part", "partsupp", "supplier", "nation", "region"]
    if t in words
]
if (sf >= 100000 or os.environ.get("NO_LINEITEM") == "1") and "lineitem" in tables:
    sys.exit(f"Q{q} reads lineitem, which NO_LINEITEM=1 refuses")
lines = [f"create view {t} as select * from read_parquet('{data}/{t}/*.parquet');" for t in tables]
lines += [".mode csv", ".timer on"]
lines += [text + ";"] * iters
print("\n".join(lines))
