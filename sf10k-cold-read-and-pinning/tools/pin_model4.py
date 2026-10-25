"""If a scan could read its pinned columns from the pin and the rest from disk (no full-coverage rule),
which columns to pin per total budget (host + GPU, each column once). Greedy by seconds saved per GB."""
import os, sys, runpy
sys.argv = [sys.argv[0]]
m = runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pin_model.py"))
np, Q, need, SIZES, U, P, total, fixed_served, FIXED = (m[k] for k in "np Q need SIZES U P total fixed_served FIXED".split())
size = {**{("lineitem", c): v for c, v in m["L"].items()}, **{("orders", c): v for c, v in m["O"].items()},
        **{("customer", c): v for c, v in m["C"].items()}, **{("part", c): v for c, v in m["PA"].items()},
        **{("partsupp", c): v for c, v in m["PS"].items()}}
def served(pinned):
    return np.array([sum(SIZES[t][c] for t, cs in need[q].items() for c in cs if (t, c) in pinned) for q in Q]) + fixed_served
def cost(pinned):
    return (P + (U - P) * (1 - served(pinned) / total)).sum()
pinned, used = set(), FIXED
curve = []
while True:
    base = cost(pinned)
    cands = [((base - cost(pinned | {k})) / v, k) for k, v in size.items() if k not in pinned]
    if not cands:
        break
    gain, k = max(cands)
    pinned.add(k); used += size[k]
    curve.append((used, cost(pinned), k[1]))
for b in (155, 200, 255, 300):
    u, t, _ = max((x for x in curve if x[0] <= b), key=lambda x: x[0])
    print(f"budget {b} GB/rank: {t:.0f} s (uses {u:.0f} GB)")
print("order:", [k for _, _, k in curve][:20])

budget = 155
chosen = [k for u, t, k in curve if u <= budget]
print("\nset at", budget, "GB:", sorted(chosen))
by_table = {}
for (t, c) in size:
    if c in chosen: by_table.setdefault(t, []).append(c)
print({t: sorted(c) for t, c in by_table.items()})
print("excluded (needed but not pinned):", sorted(c for (t, c) in size if c not in chosen))
