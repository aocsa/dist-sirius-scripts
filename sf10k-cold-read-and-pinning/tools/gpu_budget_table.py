import glob, json, re, sys
O = "/tmp/aocsa/tpch22/pin10k/gpu-budget"
R = {}
for l in open(f"{O}/results.jsonl"):
    r = json.loads(l); R.setdefault(r["tag"], {})[r["query"]] = r
arms = [t for t in ["_g0", "_g40e", "_g40", "_g80", "_g120"] if t in R]
gpu, down = {}, {}
for t in arms:
    L = glob.glob(f"{O}/suite{t}/logs/*.log")[0]
    txt = open(L, errors="replace").read()
    pin_q = re.findall(r"instance=(\S+) connection=1 query=(\d+) SQL: CALL pin_table\([^\n]*name = 'lineitem_gpu'", txt)
    g = [int(m) for i, q in pin_q for m in re.findall(rf"\[gpu_pool\] GPU:\d+ \S+ instance={re.escape(i)} connection=1 window=\d+ query={q} allocated=(\d+)", txt)]
    gpu[t] = max(g) / 1e9 if g else 0.0
    down[t] = len(re.findall(r"downgrad", txt, re.I))
Q = [9, 21, 18, 7, 14]
print("| GPU pin per rank | " + " | ".join(f"Q{q} warm" for q in Q) + " | total warm | total cold | answers | lineitem_gpu hits |")
for t in arms:
    w = [R[t][q]["warm_s"] for q in Q]; c = [R[t][q]["cold_s"] for q in Q]
    ans = sorted({x for q in Q for x in R[t][q]["vs_ref"] if x != "ok"} - set())
    print(f"| {gpu[t]:.0f} GB ({t[1:]}) | " + " | ".join(f"{x:.1f}" for x in w) + f" | {sum(w):.1f} | {sum(c):.1f} | {','.join(ans) or 'ok'} (Q18 tie) | — |")
print({t: down[t] for t in arms}, "downgrade log lines")
print({t: sum(R[t][q]["spill_bytes"] for q in Q) for t in arms}, "disk spill bytes")
