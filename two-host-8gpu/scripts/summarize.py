#!/usr/bin/env python3
"""Summarize one run: rank 0's times and rows, each iteration against the first and the reference.

usage: summarize.py <out dir> <q> <sf> <rc> <wall s> <spill bytes> [reference log]
Appends one JSON line to <out dir>/../results.jsonl and prints it.

Results above size_only_rows rows are not parsed into memory (Q11 at SF100000 is ~100M rows):
each run is compared with the first by row count and an order-independent checksum of its lines,
and with the reference by row count only.
"""
import glob
import json
import os
import re
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from results import check, parse  # noqa: E402

out, q, sf, rc, wall, spill = sys.argv[1:7]
ref = sys.argv[7] if len(sys.argv) > 7 and sys.argv[7] else None
log = f"{out}/rank_0.log"
size_only_rows = 10**6
# glog lines from NIXL (e.g. "E1030 15:05:26.765149 ...") share stderr with the answers.
glog = re.compile(r"^[EWIF]\d{4} \d\d:\d\d:\d\d")
noise = ("WARNING", "[", "estimate:", "duckdb agent mode", '{"exception')
run_time = re.compile(r"Run Time \(s\): real ([0-9.]+)")


def lines(path):
    with open(path, errors="replace") as f:
        for l in f:
            l = l.rstrip("\r\n")
            if l and not glog.match(l) and not l.startswith(noise) and "no version information" not in l:
                yield l


def stream(path):
    """(rows, checksum, seconds) per statement, without keeping the rows."""
    res, n, s = [], 0, 0
    for l in lines(path):
        m = run_time.match(l)
        if m:
            res.append((max(n - 1, 0), s, float(m[1])))
            n, s = 0, 0
        else:
            if n > 0:
                s = (s + zlib.crc32(l.encode())) % (1 << 64)
            n += 1
    return res


def fast(rows, other):
    """check(), but exact matches in any row order first: its tolerant fallback takes minutes on
    Q11's 30M rows. Above size_only_rows, only the row counts are compared."""
    if len(other) > size_only_rows:
        return "same size" if len(rows) == len(other) else f"WRONG({len(rows)} rows vs {len(other)})"
    if len(rows) == len(other) and (
        rows == other or sorted(map(tuple, rows)) == sorted(map(tuple, other))
    ):
        return "ok"
    return check(rows, other)


streamed = stream(log) if os.path.exists(log) else []
if streamed and max(r[0] for r in streamed) > size_only_rows:
    first = streamed[0]
    times = [r[2] for r in streamed]
    rows = [r[0] for r in streamed]
    same = ["same rows+checksum" if r[:2] == first[:2] else f"WRONG({r[0]} rows vs {first[0]}, checksum)" for r in streamed]
    if ref:
        want = stream(ref)[-1][0]
        vs_ref = ["same size" if r[0] == want else f"WRONG({r[0]} rows vs {want})" for r in streamed]
    else:
        vs_ref = "unverified"
else:
    text = "\n".join(lines(log)) if os.path.exists(log) else ""
    runs = parse(text)
    want = parse("\n".join(lines(ref)))[-1][0] if ref else None
    times = [r[2] for r in runs]
    rows = [len(r[0]) for r in runs]
    same = [fast(r[0], runs[0][0]) for r in runs]
    vs_ref = [fast(r[0], want) for r in runs] if want is not None else "unverified"

errors = sum(
    open(f, errors="replace").read().count("Error")
    for f in glob.glob(f"{out}/rank_*.log")
)
res = dict(
    query=int(q),
    sf=int(sf),
    rc=int(rc),
    wall_s=round(float(wall), 1),
    times_s=times,
    rows=rows,
    same_as_first=same,
    vs_ref=vs_ref,
    error_lines=errors,
    spill_bytes=int(spill),
    out=out,
)
line = json.dumps(res)
open(os.path.join(os.path.dirname(out), "results.jsonl"), "a").write(line + "\n")
print(line)
