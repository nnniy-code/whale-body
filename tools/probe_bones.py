"""tools/probe_bones.py — 骨骼全向移动测试：每根主骨绕世界 X/Y/Z 转 ±30°，看末端往哪动。

怎么测：python tools/probe_bones.py
产物：docs/BONE_AXES.md（映射表）+ 控制台输出
用途：**这个模型不是镜像的**，左右符号可能不同。写关节角之前先查这张表，
      别再凭"一般规律"猜符号。
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

# 主骨 → 用来观察的末端骨
WATCH = {
    "leftShoulder": "leftHand", "leftUpperArm": "leftHand", "leftLowerArm": "leftHand",
    "rightShoulder": "rightHand", "rightUpperArm": "rightHand", "rightLowerArm": "rightHand",
    "leftUpperLeg": "leftFoot", "leftLowerLeg": "leftFoot",
    "rightUpperLeg": "rightFoot", "rightLowerLeg": "rightFoot",
    "spine": "head", "chest": "head", "upperChest": "head", "neck": "head",
    "head": "mixamorig:HeadTop_End",
    "hips": "head",
    "WhaleTail_01": "WhaleTail_04",
}

AXES = ("x", "y", "z")
DEG = 30.0


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    zero = {n: np.eye(4) for n in rig.names}
    rest = rig.compute(zero)

    lines = ["# 骨骼轴向映射表（全向实测）", "",
             f"每根骨绕**世界** {AXES} 轴转 ±{DEG:.0f}°，看末端骨往哪动。",
             "**这个模型不是镜像的，左右符号可能不同——写关节角前先查这张表。**", "",
             "| 骨 | 轴 | +deg 末端位移 (dx, dy, dz) mm | -deg 末端位移 mm | 结论 |",
             "|---|---|---|---|---|"]

    rows = {}
    for bone, watch in WATCH.items():
        if bone not in rest:
            continue
        base = rest[watch][0:3, 3].copy()
        for axis in AXES:
            res = {}
            for sign, tag in ((1, "+"), (-1, "-")):
                basis = {n: np.eye(4) for n in rig.names}
                basis[bone] = bm.basis_from_world_rot(rig, bone, axis, sign * DEG)
                M = rig.compute(basis)
                res[tag] = (M[watch][0:3, 3] - base) * 1000.0
            p, m = res["+"], res["-"]
            # 结论：主分量方向
            idx = int(np.argmax(np.abs(p)))
            name = "XYZ"[idx]
            direction = "+" if p[idx] > 0 else "-"
            concl = f"{watch} 向 {direction}{name}"
            lines.append(
                f"| {bone} | {axis} | ({p[0]:.0f}, {p[1]:.0f}, {p[2]:.0f}) "
                f"| ({m[0]:.0f}, {m[1]:.0f}, {m[2]:.0f}) | {concl} |")
            rows[(bone, axis)] = (p, m, concl)

    out = os.path.join(ROOT, "docs", "BONE_AXES.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # 关键结论：右手绕 Y 正转往哪
    print("=== 关键符号（写关节角前必查）===")
    for bone in ("rightUpperArm", "leftUpperArm", "rightUpperLeg", "leftUpperLeg"):
        for axis in AXES:
            if (bone, axis) in rows:
                p, m, c = rows[(bone, axis)]
                print(f"  {bone:16s} {axis} +{DEG:.0f}° → {c:28s} dx={p[0]:6.0f}mm")
    print()
    print("WROTE", out, os.path.getsize(out), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
