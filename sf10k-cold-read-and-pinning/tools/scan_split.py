# Cold vs warm fetch/decode/filter task-seconds per query from [scan_timing] cumulative lines.
import re, sys, glob
from datetime import datetime
ts = lambda s: datetime.strptime(s, '%Y-%m-%d %H:%M:%S.%f').timestamp()
for d in sys.argv[1:]:
    starts, lines = [], []
    for f in glob.glob(f'{d}/logs/*.log'):
        for l in open(f, errors='replace'):
            m = re.match(r'\[([^\]]+)\]', l)
            if not m: continue
            if 'QueryBegin' in l and 'SQL: select' in l: starts.append(ts(m.group(1)))
            if '[scan_timing]' in l:
                g = re.search(r'gpu (\S+) splits (\d+) reads (\d+) read_MB (\d+) rows (\d+) \| fetch_ms (\d+) decode_ms (\d+) static_filter_ms (\d+) dynamic_filter_ms (\d+)', l)
                lines.append((ts(m.group(1)), g.group(1), [int(x) for x in g.groups()[1:]]))
    starts = sorted(set(round(s, 1) for s in starts))
    # second iteration begins at the 2nd distinct start time cluster (ranks start within ~1 s)
    clusters = []
    for s in starts:
        if not clusters or s - clusters[-1] > 5: clusters.append(s)
    boundary = clusters[1] if len(clusters) > 1 else 1e18
    tot = {'cold': [0]*8, 'all': [0]*8}
    for gpu in sorted({x[1] for x in lines}):
        mine = sorted(x for x in lines if x[1] == gpu)
        cold = [x for x in mine if x[0] < boundary]
        for k, sel in (('cold', cold), ('all', mine)):
            if sel: tot[k] = [a + b for a, b in zip(tot[k], sel[-1][2])]
    warm = [a - b for a, b in zip(tot['all'], tot['cold'])]
    for name, v in (('cold', tot['cold']), ('warm', warm)):
        f, dcd, sf, df = v[4] / 1e3, v[5] / 1e3, v[6] / 1e3, v[7] / 1e3
        s = f + dcd + sf + df or 1
        print(f"{d.split('/')[-2]:10s} {name}: splits {v[0]:5d} read {v[2]/1e3:7.0f} GB | fetch {f:7.0f}s ({100*f/s:3.0f}%) decode {dcd:6.0f}s ({100*dcd/s:3.0f}%) static_filter {sf:5.0f}s dyn_filter {df:5.0f}s  (task-seconds, 4 ranks)")
