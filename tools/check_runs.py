#!/usr/bin/env python3
"""Checks one process's runs of a benchmark query and prints a CSV row per run.

    check_runs.py <out.log> <expected.csv> <query> <sf> <gpus> <runs> <runner exit code>

out.log is the DuckDB shell's output in -csv mode with `.timer on`; bench.sh prints the line
BENCH_START after the setup statements (LOAD, SET, views) and before the first query. Each query's
rows end with a "Run Time (s): real <seconds> ..." line.

answer per run:
  ok / WRONG  the rows equal / differ from <expected.csv> (header included)
  ERROR       the run printed an error, or the setup did: the shell carries on after a failed
              LOAD, and DuckDB would then answer the query on the CPU
  MISSING     no timing line for this run (the process died or stopped early)
  n/a         no <expected.csv> to compare with; EMPTY when the run printed no rows at all
  +RANK_FAILED  appended when the runner exited non-zero (another rank failed)
"""
import os
import re
import sys

out, expected_file, query, sf, gpus, runs, rc = sys.argv[1:]
expected = None
if os.path.exists(expected_file):
    expected = [line.strip() for line in open(expected_file) if line.strip()]

error = re.compile(r"Error\b|\"exception_type\"")
lines = open(out, errors="replace").read().splitlines() if os.path.exists(out) else []
start = lines.index("BENCH_START") if "BENCH_START" in lines else len(lines)
setup_failed = any(error.search(line) for line in lines[:start])

results, rows = [], []
for line in (line.strip() for line in lines[start + 1:]):
    if not line or "no version information" in line:
        continue
    if line.startswith("Run Time (s): real"):
        if setup_failed or any(error.search(row) for row in rows):
            verdict = "ERROR"
        elif expected is None:
            verdict = "n/a" if len(rows) > 1 else "EMPTY"
        else:
            verdict = "ok" if rows == expected else "WRONG"
        results.append((line.split()[4], verdict))
        rows = []
    else:
        rows.append(line)

for i in range(int(runs)):
    seconds, verdict = results[i] if i < len(results) else ("", "MISSING")
    if rc != "0" and verdict in ("ok", "n/a"):
        verdict += "+RANK_FAILED"
    print(f"{query},{sf},{gpus},{i + 1},{seconds},{verdict}")
