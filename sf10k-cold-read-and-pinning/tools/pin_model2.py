import os, sys, runpy
sys.argv = [sys.argv[0]]  # load definitions only
m = runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pin_model.py"))
Q = m["Q"]
for host, gpu in [(155, 0), (155, 40), (155, 60), (155, 80), (155, 100), (155, 120)]:
    (h, g, ua, ub, rc, per), t = m["two_tier"](host, gpu)
    print(f"\n== host {host} GB, GPU {gpu} GB per rank: {t:.0f} s (host uses {h:.0f}, GPU uses {g:.0f})")
    print("  lineitem on host:", sorted(c[2:] for c in ua))
    print("  lineitem on GPU: ", sorted(c[2:] for c in ub))
    for n, c in zip(["orders", "customer", "part", "partsupp"], rc):
        print(f"  {n}:", sorted(x.split('_', 1)[1] for x in c))
    print("  slowest:", sorted(((round(float(x), 1), q) for x, q in zip(per, Q)), reverse=True)[:6])
