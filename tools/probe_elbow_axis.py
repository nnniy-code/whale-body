"""tools/probe_elbow_axis.py — 前臂绕不同轴转，手落在哪。

怎么测：python tools/probe_elbow_axis.py
用途：诊断「前臂朝内」——肘的弯曲轴选错了，手就会往身体中线跑。
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.bones import BoneMap  # noqa: E402
from core.fk import Rig  # noqa: E402


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))

    # 上臂固定在"侧举 90°"（手大致水平向外）
    def build(ua_lift, la_axis, la_deg, ua_roll=0.0):
        basis = {n: np.eye(4) for n in rig.names}
        b = bm.basis_from_world_rot(rig, "rightUpperArm", "y", ua_lift)
        if ua_roll:
            b = b @ bm.basis_from_world_rot(rig, "rightUpperArm", "z", ua_roll)
        basis["rightUpperArm"] = b
        if la_deg:
            basis["rightLowerArm"] = bm.basis_from_world_rot(
                rig, "rightLowerArm", la_axis, la_deg)
        return rig.compute(basis)

    print("=== 上臂侧举 90°，前臂绕各轴 ±90° ===")
    print(f"{'axis':>5} {'deg':>5} {'hand_x':>8} {'hand_y':>8} {'hand_z':>8} {'elbow_x':>8}")
    for axis in ("x", "y", "z"):
        for deg in (-90, 90):
            M = build(90, axis, deg)
            h = M["rightHand"][0:3, 3]
            e = M["rightLowerArm"][0:3, 3]
            print(f"{axis:>5} {deg:5d} {h[0]:8.3f} {h[1]:8.3f} {h[2]:8.3f} {e[0]:8.3f}")

    print("\n=== 加上臂 roll=±40°，前臂绕 Y 90° ===")
    for roll in (-40, 0, 40):
        M = build(90, "y", 90, ua_roll=roll)
        h = M["rightHand"][0:3, 3]
        e = M["rightLowerArm"][0:3, 3]
        print(f"  roll={roll:+4d} hand=({h[0]:6.3f},{h[2]:6.3f}) elbow=({e[0]:6.3f},{e[2]:6.3f})")

    print("\n=== 目标：手在体侧(x<-0.15)且高(z>0.90)、肘在外侧 ===")
    best = []
    for lift in (70, 90, 110, 130):
        for roll in (-60, -30, 0, 30, 60):
            for la in (-100, -70, -40, 40, 70, 100):
                M = build(lift, "y", la, ua_roll=roll)
                h = M["rightHand"][0:3, 3]
                e = M["rightLowerArm"][0:3, 3]
                if h[0] < -0.15 and h[2] > 0.90 and e[0] < -0.10:
                    best.append((lift, roll, la, round(float(h[0]), 3),
                                 round(float(h[2]), 3), round(float(e[0]), 3)))
    print(f"  满足的组合 {len(best)} 个")
    for b in best[:10]:
        print(f"    lift={b[0]:3d} roll={b[1]:+3d} la={b[2]:+4d} -> hand_x={b[3]:6.3f} "
              f"hand_z={b[4]:5.3f} elbow_x={b[5]:6.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
