#!/usr/bin/env python3
"""Re-check a run22 output dir against expected answers using the saved rank-0 logs.
usage: recheck.py RUN_DIR EXPECTED_DIR  -> prints query,status,n_runs,times... per query"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from run22_dpo import parse, check
run, exp_dir = sys.argv[1], sys.argv[2]
for q in range(1, 23):
    log = f"{run}/q{q:02d}/exchange_rank_0.log"
    if not os.path.exists(log): continue
    runs = parse(open(log, errors="replace").read())
    ef = f"{exp_dir}/q{q:02d}.json"
    checks = [check(r[0], json.load(open(ef))) for r in runs] if os.path.exists(ef) else ["NOEXP"]
    bad = [c for c in checks if c != "ok"]
    status = (bad[0] if bad else "ok") if runs else "ERROR"
    print(f"{q},{status},{len(runs)}," + ",".join(f"{r[2]:.3f}" for r in runs))
