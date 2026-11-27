"""tests/test_bones.py — 骨骼元数据层验收（BONES.md 五条约束）。

怎么测：python tests/test_bones.py
预期现象：[bones] ALL PASS
调参位置：core/bones.py、config/bones_map.json
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bones import BoneMap, normalize  # noqa: E402
from core.fk import Rig  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RIG = os.path.join(HERE, "fixtures", "rig_data.json")
MAP = os.path.join(os.path.dirname(HERE), "config", "bones_map.json")

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    with open(RIG, "r", encoding="utf-8") as f:
        rig_d = json.load(f)
    rig = Rig(rig_d["rest"], rig_d["parent"], rig_d["connected"], rig_d["order"])
    bm = BoneMap.load(MAP)

    print("[1] 约束 5：前缀归一化（166 无前缀 + 43 mixamorig:）")
    check("normalize 去前缀", normalize("mixamorig:HeadTop_End") == "HeadTop_End")
    check("normalize 无前缀原样", normalize("hips") == "hips")
    check("解析短名", bm.resolve("HeadTop_End") == "mixamorig:HeadTop_End",
          bm.resolve("HeadTop_End"))
    check("解析全名", bm.resolve("mixamorig:HeadTop_End") == "mixamorig:HeadTop_End")
    check("无重名", bm.duplicates() == [], str(bm.duplicates()))
    check("未知骨抛 KeyError", bm.try_resolve("nonexistent") is None)

    print("[2] 约束 1：不硬编码骨名（链分类）")
    for n, expect in (("hips", "main"), ("Hair_00_01", "hair"), ("Skirt_03_02", "skirt"),
                      ("WhaleTail_02", "tail"), ("Bangs_01_01", "bangs"),
                      ("WaistRibbon_0_02", "ribbon")):
        got = bm.try_resolve(n)
        check(f"{n} -> {expect}", got is not None and bm.chain(got) == expect,
              f"got {None if got is None else bm.chain(got)}")
    check("main 21 根", len(bm.names("main")) == 21, str(len(bm.names("main"))))
    check("根骨唯一 hips", bm.roots() == ["hips"], str(bm.roots()))

    print("[3] 约束 2：connected 来自数据（实测 57 unconnected）")
    unconn = [n for n in bm.names() if not bm.is_connected(n)]
    check("unconnected = 57", len(unconn) == 57, str(len(unconn)))
    check("hips 是 unconnected", not bm.is_connected("hips"))
    check("leftLowerLeg 是 connected", bm.is_connected("leftLowerLeg"))

    print("[4] 约束 4：无权重骨（43 根）")
    check("hips 无权重", not bm.has_weights("hips"))
    check("spine 有权重", bm.has_weights("spine"))
    check("HeadTop_End 无权重", not bm.has_weights("HeadTop_End"))
    n_nw = sum(1 for n in bm.names() if not bm.has_weights(n))
    check("无权重总数 = 43", n_nw == 43, str(n_nw))

    print("[5] 约束 3：语义角度 → 局部 basis（躯干 vs 四肢不同轴）")
    zero = {n: np.eye(4) for n in rig_d["order"]}
    M0 = rig.compute(zero)
    head0 = M0["head"][0:3, 3].copy()

    lean = dict(zero)
    lean["spine"] = bm.basis_from_world_rot(rig, "spine", "x", 20)
    M1 = rig.compute(lean)
    check("躯干绕世界 X 20° → 头前移(y 减小)",
          M1["head"][0:3, 3][1] < head0[1] - 0.01,
          f"{head0[1]:.4f} -> {M1['head'][0:3, 3][1]:.4f}")

    raise_arm = dict(zero)
    raise_arm["leftUpperArm"] = bm.basis_from_world_rot(rig, "leftUpperArm", "z", 60)
    M2 = rig.compute(raise_arm)
    hand0 = M0["leftHand"][0:3, 3].copy()
    hand2 = M2["leftHand"][0:3, 3]
    check("手臂绕世界 Z 60° → 手位移明显", np.linalg.norm(hand2 - hand0) > 0.05,
          f"move={np.linalg.norm(hand2-hand0):.3f}m")

    # 同一个「绕世界 Z」作用到躯干和手臂，结果方向应不同（局部轴不同）
    a = bm.basis_from_world_rot(rig, "spine", "z", 30)
    b = bm.basis_from_world_rot(rig, "leftUpperArm", "z", 30)
    check("躯干/手臂的局部 basis 不同（证明轴没套错）",
          not np.allclose(a, b, atol=1e-6))

    print("[6] world_loc 只对 unconnected 有效")
    loc = bm.basis_from_world_loc(rig, "hips", np.array([0.05, 0.0, 0.0]))
    M3 = rig.compute({**zero, "hips": loc})
    dx = M3["hips"][0:3, 3][0] - M0["hips"][0:3, 3][0]
    check("hips 位移生效（≈50mm）", abs(abs(dx) - 0.05) < 0.005, f"dx={dx*1000:.2f}mm")

    loc2 = bm.basis_from_world_loc(rig, "leftFoot", np.array([0.05, 0.0, 0.0]))
    M4 = rig.compute({**zero, "leftFoot": loc2})
    check("leftFoot 位移被忽略（connected）",
          np.allclose(M4["leftFoot"][0:3, 3], M0["leftFoot"][0:3, 3]),
          f"{M4['leftFoot'][0:3,3] - M0['leftFoot'][0:3,3]}")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
