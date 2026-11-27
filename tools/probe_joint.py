"""tools/probe_joint.py — 扫关节角，看手/肘落在哪。用于挑 gestures 的关键帧。

怎么测：python tools/probe_joint.py
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.bones import BoneMap  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.gestures import _joint_basis  # noqa: E402


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))

    print(f"{'ua_y':>6} {'ua_z':>5} {'la_y':>5} {'hand_z':>8} {'hand_x':>8} {'elbow_z':>8} {'elbow_x':>8}")
    for ua_y in (-60, -90, -105, -120, -140):
        for ua_z in (0, 25, 45):
            for la_y in (-40, -80, -110):
                basis = {n: __import__("numpy").eye(4) for n in rig.names}
                basis["rightUpperArm"] = _joint_basis(rig, bm, "rightUpperArm",
                                                      {"x": 0, "y": ua_y, "z": ua_z})
                basis["rightLowerArm"] = _joint_basis(rig, bm, "rightLowerArm",
                                                      {"x": 0, "y": la_y, "z": 0})
                M = rig.compute(basis)
                h = M["rightHand"][0:3, 3]
                e = M["rightLowerArm"][0:3, 3]
                print(f"{ua_y:6d} {ua_z:5d} {la_y:5d} {h[2]:8.3f} {h[0]:8.3f} {e[2]:8.3f} {e[0]:8.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
