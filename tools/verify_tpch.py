#!/usr/bin/env python3
"""Verify a TPC-H parquet dataset: table presence, file counts/sizes, exact row counts
(read from parquet footer metadata only -- no data pages are decoded)."""
import sys, os
from pathlib import Path
import pyarrow.parquet as pq

TABLES = ["nation","region","part","supplier","partsupp","customer","orders","lineitem"]
# exact TPC-H cardinalities per SF (lineitem is SF-dependent and not a clean multiple)
EXPECTED_LINEITEM = {1: 6001215, 100: 600037902, 300: 1799989091, 500: 3000028242,
                     1000: 5999989709, 3000: 18000048306}
EXPECTED_OTHER = {  # multiplier * SF
    "nation": None, "region": None,
    "part": 200000, "supplier": 10000, "partsupp": 800000,
    "customer": 150000, "orders": 1500000,
}

def main():
    root = Path(sys.argv[1]); sf = int(sys.argv[2])
    ok = True
    print(f"=== {root}  (SF{sf}) ===")
    total_bytes = 0
    for t in TABLES:
        d = root / t
        if not d.is_dir():
            print(f"  {t:<9} MISSING DIRECTORY"); ok = False; continue
        files = sorted(d.glob("*.parquet"))
        if not files:
            print(f"  {t:<9} NO PARQUET FILES"); ok = False; continue
        rows = 0
        sizes = []
        for f in files:
            rows += pq.ParquetFile(f).metadata.num_rows
            sizes.append(f.stat().st_size)
        total_bytes += sum(sizes)
        exp = EXPECTED_LINEITEM.get(sf) if t == "lineitem" else (
            None if EXPECTED_OTHER[t] is None else EXPECTED_OTHER[t] * sf)
        if t == "nation": exp = 25
        if t == "region": exp = 5
        verdict = ""
        if exp is not None:
            if rows == exp: verdict = "OK"
            else: verdict = f"*** MISMATCH expected {exp:,} (diff {rows-exp:+,}) ***"; ok = False
        print(f"  {t:<9} files={len(files):<3} rows={rows:>15,}  "
              f"size={sum(sizes)/2**30:8.2f} GiB  "
              f"file_range={min(sizes)/2**30:.2f}-{max(sizes)/2**30:.2f} GiB  {verdict}")
    print(f"  TOTAL parquet bytes: {total_bytes/2**30:.2f} GiB")
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

sys.exit(main())
