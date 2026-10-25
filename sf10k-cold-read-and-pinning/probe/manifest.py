# Byte ranges a Sirius scan reads: per row group, the chunks of the given columns, 4 KiB aligned,
# chunks closer than 4 KiB merged. Output: "F <path> <footer_off> <footer_len>" then "R <path> <off> <len>".
import sys, glob, pyarrow.parquet as pq
table, cols, first, n = sys.argv[1], sys.argv[2].split(','), int(sys.argv[3]), int(sys.argv[4])
import os
SF = os.environ.get('SF', '10000'); GAP = int(os.environ.get('GAP', '4096'))
A = 4096
files = sorted(glob.glob(f'/scratch/sirius/datasets/tpch_sf{SF}/{table}/*.parquet'))[first:first + n]
for f in files:
    pf = pq.ParquetFile(f); md = pf.metadata
    size = pf.metadata.serialized_size
    import os; fsz = os.path.getsize(f)
    print(f"F {f} {fsz - size - 8} {size + 8}")
    idx = [i for i in range(md.num_columns) if md.schema.column(i).name in cols]
    for rg in range(md.num_row_groups):
        r = md.row_group(rg); spans = []
        for i in idx:
            c = r.column(i)
            s = c.data_page_offset if c.dictionary_page_offset is None else min(c.dictionary_page_offset, c.data_page_offset)
            spans.append([s // A * A, -(-(s + c.total_compressed_size) // A) * A])
        spans.sort(); merged = [spans[0]]
        for s, e in spans[1:]:
            if s <= merged[-1][1] + GAP: merged[-1][1] = max(merged[-1][1], e)
            else: merged.append([s, e])
        for s, e in merged: print(f"R {f} {s} {e - s}")
