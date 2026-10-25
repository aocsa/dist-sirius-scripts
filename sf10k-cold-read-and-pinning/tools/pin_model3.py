import os, sys, runpy
sys.argv = [sys.argv[0]]
m = runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pin_model_b.py"))
Q = m["Q"]
for host, gpu in [(155, 60), (155, 80), (155, 100), (175, 80), (175, 100)]:
    (h, g, ua, ub, rc, per), t = m["two_tier"](host, gpu, rest_on_gpu=True)
    print(f"== host {host}, GPU {gpu} (small tables + lineitem B on GPU): {t:.0f} s")
    print("  lineitem host:", sorted(c[2:] for c in ua), " GPU:", sorted(c[2:] for c in ub))
    print("  small tables:", [sorted(x.split('_', 1)[1] for x in c) for c in rc])
