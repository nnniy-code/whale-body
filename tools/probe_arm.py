"""tools/probe_arm.py — 探测 arm_lift / elbow_bend 对右手位置的影响。

怎么测：python tools/probe_arm.py
用途：给 core/gestures.py 的关键帧挑参数
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import pose_gen  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.fk import Rig  # noqa: E402


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    fam = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    base = dict(fam["wave"]["P_default"])

    print(f"{'lift':>5} {'elbow':>6} {'hand_z':>8} {'hand_x':>8} {'hand_y':>8}")
    for lift in (40, 70, 100, 130, 160):
        for eb in (20, 60, 100, 140):
            P = dict(base)
            P["arm_lift"] = lift
            P["elbow_bend"] = eb
            M = rig.compute(pose_gen.build(P, rig, bm, active_side="right"))
            h = M["rightHand"][0:3, 3]
            print(f"{lift:5d} {eb:6d} {h[2]:8.3f} {h[0]:8.3f} {h[1]:8.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
