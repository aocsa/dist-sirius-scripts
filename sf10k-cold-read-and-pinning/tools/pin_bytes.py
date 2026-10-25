"""Host-pool bytes held per rank after each pin_table, from the [host_pool] lines that follow each statement.
usage: pin_bytes.py SIRIUS_LOG"""
import re, sys
name, last, rows = {}, {}, []
for line in open(sys.argv[1], errors="replace"):
    m = re.search(r"QueryBegin: instance=(\S+) connection=\d+ query=(\d+) SQL: CALL pin_table\(.*name = '(\w+)'", line)
    if m:
        name[(m[1], m[2])] = m[3]
        continue
    m = re.search(r"\[host_pool\] HOST:\d+ Query\S* instance=(\S+) connection=\d+ window=\d+ query=(\d+) allocated=(\d+) bytes peak=(\d+)", line)
    if m and (m[1], m[2]) in name:
        last[(m[1], m[2])] = (int(m[3]), int(m[4]))
inst = sorted({k[0] for k in name})
for i in inst:
    prev = 0
    out = []
    for k in sorted((k for k in name if k[0] == i), key=lambda k: int(k[1])):
        a, p = last.get(k, (0, 0))
        out.append(f"{name[k]} +{(a - prev) / 1e9:.1f}")
        prev = a
    print(i[-6:], f"total {prev / 1e9:.1f} GB:", ", ".join(out))
