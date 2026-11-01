#!/usr/bin/env python3
"""Print the SQL a rank runs: views over DATA/<table>/*.parquet, then TPC-H Q at SF, ITERS times.

usage: gen_sql.py Q SF DATA ITERS
Query text comes from test/tpch_performance/queries.py of WT, the Sirius checkout.
Views are made only for the tables the query names. With NO_LINEITEM=1, a query that reads
lineitem is refused. With STATS=1, each view gets the column statistics that analyze_stats.sh
wrote to STATS_DIR/<basename DATA>.csv (default ../stats); a missing file is an error.
"""
import csv
import os
import re
import sys

q, sf, data, iters = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
here = os.path.dirname(os.path.abspath(__file__))
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
if os.environ.get("NO_LINEITEM") == "1" and "lineitem" in tables:
    sys.exit(f"Q{q} reads lineitem, which NO_LINEITEM=1 refuses")
stats = {}
if os.environ.get("STATS") == "1":
    stats_dir = os.environ.get("STATS_DIR", os.path.join(here, "..", "stats"))
    path = os.path.join(stats_dir, os.path.basename(data) + ".csv")
    with open(path) as f:
        for row in csv.DictReader(f):
            stats.setdefault(row["table"], []).append(
                f"'{row['column']}': MAP {{'min': '{row['min']}', 'max': '{row['max']}', "
                f"'null_count': '{row['null_count']}'}}"
            )


def view(t):
    opts = f", column_statistics := MAP {{{', '.join(stats[t])}}}" if t in stats else ""
    return f"create view {t} as select * from read_parquet('{data}/{t}/*.parquet'{opts});"


lines = [view(t) for t in tables]
lines += [".mode csv", ".timer on"]
lines += [text + ";"] * iters
print("\n".join(lines))
