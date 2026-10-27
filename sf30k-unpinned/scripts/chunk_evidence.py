#!/usr/bin/env python3
"""Per run dir: how many CONCAT splits and multi-chunk join builds the Sirius logs record.
usage: chunk_evidence.py OUT_DIR"""
import collections
import glob
import re
import sys

split = re.compile(r"CONCAT id (\d+): split partition \d+ build side into (\d+) chunks")
built = re.compile(r"sirius_physical_hash_join id (\d+): partition \d+ built from (\d+) chunks")
for qd in sorted(glob.glob(f"{sys.argv[1]}/r*-*/q*")):
    s, b = collections.Counter(), collections.Counter()
    for f in glob.glob(f"{qd}/logs/*.log"):
        for line in open(f, errors="replace"):
            for m in split.finditer(line):
                s[(int(m[1]), int(m[2]))] += 1
            for m in built.finditer(line):
                b[(int(m[1]), int(m[2]))] += 1
    fmt = lambda c: ", ".join(f"op{k[0]}:{k[1]}chunks x{v}" for k, v in sorted(c.items())) or "none"
    print(f"{qd.split('/')[-2]}/{qd.split('/')[-1]}: concat splits [{fmt(s)}]; join builds [{fmt(b)}]")
