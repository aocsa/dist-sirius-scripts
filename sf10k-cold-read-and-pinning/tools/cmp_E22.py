import json, glob
def load(pat, tag=None):
    d = {}
    for f in glob.glob(pat):
        for l in open(f):
            r = json.loads(l)
            if tag and r.get("tag") != tag: continue
            d[r["query"]] = r
    return d
E = load("/tmp/aocsa/tpch22/pin10k/results.jsonl", "_E22")
C = load("/tmp/aocsa/tpch22/dpo-best/sf10k-22-cap4m/r*/results.jsonl")
B = load("/tmp/aocsa/tpch22/dpo-best/sf10k-22-b4/r*/results.jsonl")
print("| Q | cap cold | cap warm | E cold | E warm | warm speedup | pin hits | answers |")
print("|---|---|---|---|---|---|---|---|")
t = dict(cc=0, cw=0, ec=0, ew=0, bc=0, bw=0)
for q in range(1, 23):
    c, e, b = C.get(q), E.get(q), B.get(q)
    t["cc"] += c["cold_s"]; t["cw"] += c["warm_s"]; t["ec"] += e["cold_s"]; t["ew"] += e["warm_s"]
    if b: t["bc"] += b["cold_s"]; t["bw"] += b["warm_s"]
    ans = ",".join(sorted(set(e["vs_ref"] + e["same_as_first"])))
    print(f"| Q{q} | {c['cold_s']:.1f} | {c['warm_s']:.1f} | {e['cold_s']:.1f} | {e['warm_s']:.1f} | {c['warm_s']/e['warm_s']:.2f}x | {e['pin_hits']} | {ans} |")
print(f"| total | {t['cc']:.1f} | {t['cw']:.1f} | {t['ec']:.1f} | {t['ew']:.1f} | {t['cw']/t['ew']:.2f}x | | |")
print("b4 totals", round(t["bc"],1), round(t["bw"],1), len(B))
