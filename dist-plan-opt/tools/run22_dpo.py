#!/usr/bin/env python3
"""Run TPC-H queries distributed on N ranks and check them against expected answers.

usage: run22_dpo.py --data DIR --sf SF --config YAML --out DIR [--repo SIRIUS_CHECKOUT] [--ranks 4]
                   [--iters 3] [--expected DIR] [--queries 1,2,...] [--port 30000] [--timeout 1800]
--repo (default $SIRIUS_REPO, else /tmp/aocsa/wt/dist-plan-opt): the built Sirius checkout; its
scripts/exchange_sql_ranks.sh launches the ranks and test/tpch_performance/queries.py gives the SQL.
Expected answers: DIR/qNN.json as {"names": [...], "rows": [[...], ...]} (gen_expected.py writes them).
"""
import argparse, csv, io, json, math, os, re, subprocess, sys, time
from decimal import Decimal

REPO = os.environ.get("SIRIUS_REPO", "/tmp/aocsa/wt/dist-plan-opt")
TABLES = ["customer", "orders", "lineitem", "supplier", "nation", "region", "part", "partsupp"]

def query_text(repo, q, sf):
    """The repo's benchmark query set, with Q11's fraction rendered for the scale factor."""
    sys.path.insert(0, f"{repo}/test/tpch_performance")
    from queries import queries_for_scale_factor
    return queries_for_scale_factor(sf)[f"q{q}"].strip().rstrip(";")

def sql_for(repo, data, q, iters, sf):
    text = query_text(repo, q, sf)
    lines = [f"create view {t} as select * from read_parquet('{data}/{t}/*.parquet');" for t in TABLES]
    lines += [".mode csv", ".timer on"]
    lines += [text + ";"] * iters
    return "\n".join(lines) + "\n"

def parse(out):
    """Split rank 0's output into (rows, seconds) per statement."""
    runs, block = [], []
    for line in out.splitlines():
        m = re.match(r"Run Time \(s\): real ([0-9.]+)", line)
        if m:
            runs.append((block, float(m.group(1))))
            block = []
        elif line and not line.startswith(("WARNING", "[", "estimate:", "duckdb agent mode", "{\"exception")) and "no version information" not in line:
            block.append(line)
    res = []
    for block, secs in runs:
        rows = list(csv.reader(io.StringIO("\n".join(block))))
        res.append((rows[1:] if rows else [], rows[0] if rows else [], secs))
    return res

def close(a, b):
    if a == b: return True
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()
    return math.isclose(x, y, rel_tol=1e-6, abs_tol=1e-2)

def check(rows, exp):
    want = exp["rows"]
    if len(rows) != len(want): return f"WRONG({len(rows)} rows vs {len(want)})"
    def same(rs, ws): return all(len(r) == len(w) and all(close(a, b) for a, b in zip(r, w)) for r, w in zip(rs, ws))
    if same(rows, want): return "ok"
    key = lambda r: [str(v) if not isinstance(v, (int, float)) else f"{float(v):.2f}" for v in r]
    norm = lambda r: [f"{float(v):.2f}" if re.fullmatch(r"-?[0-9.]+(e[-+]?[0-9]+)?", str(v)) else str(v) for v in r]
    if sorted(map(norm, rows)) == sorted(map(norm, want)): return "ok"  # ties in another order
    return "WRONG(values)"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True); ap.add_argument("--sf", required=True); ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--ranks", type=int, default=4)
    ap.add_argument("--iters", type=int, default=3); ap.add_argument("--expected")
    ap.add_argument("--queries", default=",".join(str(i) for i in range(1, 23)))
    ap.add_argument("--port", type=int, default=30000); ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--repo", default=REPO)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    env = dict(os.environ, SIRIUS_CONFIG_FILE=a.config, SIRIUS_LOG_DIR=f"{a.out}/logs",
               EXCHANGE_TIMEOUT=str(a.timeout))
    for k in ("AI_AGENT", "CLAUDECODE", "CLAUDE_CODE"): env.pop(k, None)
    summary = open(f"{a.out}/results.csv", "a", newline="")
    w = csv.writer(summary)
    for q in [int(x) for x in a.queries.split(",")]:
        sqlf = f"{a.out}/q{q:02d}.sql"
        open(sqlf, "w").write(sql_for(a.repo, a.data, q, a.iters, a.sf))
        qout = f"{a.out}/q{q:02d}"
        os.makedirs(qout, exist_ok=True)
        t0 = time.time()
        p = subprocess.run(["pixi", "run", "--manifest-path", a.repo, f"{a.repo}/scripts/exchange_sql_ranks.sh",
                            str(a.ranks), sqlf, str(a.port + q * 10), qout],
                           env=env, capture_output=True, text=True, timeout=a.timeout + 120)
        wall = time.time() - t0
        out = open(f"{qout}/exchange_rank_0.log", errors="replace").read() if os.path.exists(f"{qout}/exchange_rank_0.log") else p.stdout
        err = re.search(r'(Error|exception_message"?:)\s*"?([^"\n]{0,160})', out)
        runs = parse(out)
        exp = None
        if a.expected:
            f = f"{a.expected}/q{q:02d}.json"
            exp = json.load(open(f)) if os.path.exists(f) else None
        times = [r[2] for r in runs]
        status = "ERROR" if (err or p.returncode) and not runs else ("ok" if runs else "MISSING")
        checks = [check(r[0], exp) for r in runs] if exp else []
        if checks and any(c != "ok" for c in checks): status = next(c for c in checks if c != "ok")
        if err and status == "ok" and len(runs) < a.iters: status = "ERROR"
        note = err.group(2) if err else ""
        w.writerow([q, status, len(runs), *[f"{t:.3f}" for t in times], note]); summary.flush()
        best = min(times) if times else float("nan")
        print(f"q{q:02d} {status:14s} runs={len(runs)} times={' '.join(f'{t:.2f}' for t in times)} "
              f"best={best:.2f} wall={wall:.0f}s {note[:90]}", flush=True)

if __name__ == "__main__":
    main()
