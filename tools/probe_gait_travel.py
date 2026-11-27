"""tools/probe_gait_travel.py — 验证 foot_targets 的 travel 开关。"""
import json
import sys

sys.path.insert(0, r"D:\DSproject\body-v2")

import core.gait as g


def main():
    fams = json.load(open(r"D:\DSproject\body-v2\config\seed_families.json", encoding="utf-8"))["families"]
    G = dict(fams["walk"]["G"])
    G["stride"] = 0.5
    G["period"] = 1.0
    s = {"left": [0.076, -0.068, 0.0], "right": [-0.076, -0.068, 0.0]}
    origin = [0.0, -0.068, 0.0]
    for tr in (True, False):
        print(f"--- travel={tr} ---")
        for t in (0.0, 0.25, 0.5, 1.0):
            ft = g.foot_targets(G, t, s, body_origin=origin, travel=tr)
            L = [round(float(x), 3) for x in ft["left"]]
            R = [round(float(x), 3) for x in ft["right"]]
            print(f"  t={t:4.2f}  left={L}  right={R}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
