# Cold/warm scan + shuffle table for the shuffle profile.
#   shuffle_table.py <profile dir>   (expects q12/ q19/ q21/ with logs/rank*/ and parsed/rank*/)
import csv, glob, re, sys
from datetime import datetime

ts = lambda s: datetime.strptime(s, '%Y-%m-%d %H:%M:%S.%f').timestamp()
KEYS = ['splits', 'reads', 'read_MB', 'rows', 'fetch_ms', 'decode_ms', 'static_filter_ms',
        'dynamic_filter_ms', 'shuffle_batches', 'shuffle_MB', 'route_ms', 'send_blocked_ms',
        'transfer_ms']
PAT = re.compile(r'gpu \S+ ' + ' '.join(fr'{k} (\d+)' if k not in ('fetch_ms', 'shuffle_batches') else
                                        fr'\| {k} (\d+)' for k in KEYS))


def iteration_starts(log):
    starts = []
    for l in open(log, errors='replace'):
        if 'QueryBegin' in l and 'SQL: select' in l:
            starts.append(ts(re.match(r'\[([^\]]+)\]', l).group(1)))
    return starts


root = sys.argv[1]
print('| Query | Iteration | Fetch | Decode | Filters | Shuffled | Route (partition) | Send blocked | '
      'Transfer in flight (wall) | Post-shuffle ops |')
print('|---|---|---|---|---|---|---|---|---|---|')
for q in ('q12', 'q19', 'q21'):
    sums = {'cold': [0] * len(KEYS), 'warm': [0] * len(KEYS)}
    post = {'cold': 0.0, 'warm': 0.0}
    for r in range(4):
        log = glob.glob(f'{root}/{q}/logs/rank{r}/*.log')[0]
        starts = iteration_starts(log)
        boundary = starts[1] if len(starts) > 1 else 1e18
        lines = []
        for l in open(log, errors='replace'):
            if '[scan_timing]' not in l: continue
            m = PAT.search(l)
            if m: lines.append((ts(re.match(r'\[([^\]]+)\]', l).group(1)), [int(x) for x in m.groups()]))
        cold = [x for x in lines if x[0] < boundary]
        c = cold[-1][1] if cold else [0] * len(KEYS)
        a = lines[-1][1] if lines else [0] * len(KEYS)
        sums['cold'] = [s + v for s, v in zip(sums['cold'], c)]
        sums['warm'] = [s + (av - cv) for s, av, cv in zip(sums['warm'], a, c)]
        # Post-shuffle operators from the trace logs: EXCHANGE_SOURCE, PARTITION, CONCAT.
        idx = list(csv.DictReader(open(f'{root}/{q}/parsed/rank{r}/_index.csv')))
        for it, name in zip(idx[:2], ('cold', 'warm')):
            for o in csv.DictReader(open(f'{root}/{q}/parsed/rank{r}/_operator_aggregates.csv')):
                if o['query_begin_ts'] == it['query_begin_ts'] and o['operator_type'] in (
                        'EXCHANGE_SOURCE', 'PARTITION', 'CONCAT'):
                    post[name] += float(o['sum_execution_time_ms'] or 0) / 1e3
    for name in ('cold', 'warm'):
        v = dict(zip(KEYS, sums[name]))
        f, d = v['fetch_ms'] / 1e3, v['decode_ms'] / 1e3
        filt = (v['static_filter_ms'] + v['dynamic_filter_ms']) / 1e3
        print(f"| {q.upper()} | {name} | {f:.0f} s | {d:.0f} s | {filt:.0f} s | {v['shuffle_MB']/1e3:.0f} GB "
              f"({v['shuffle_batches']} batches) | {v['route_ms']/1e3:.1f} s | {v['send_blocked_ms']/1e3:.1f} s | "
              f"{v['transfer_ms']/1e3:.1f} s | {post[name]:.1f} s |")
