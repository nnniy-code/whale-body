"""tools/audit_rig.py — 完整骨架体检：骨骼完整性、局部轴、镜像性、pose_gen 参数有效性。

怎么测：python tools/audit_rig.py
产物：docs/RIG_AUDIT.md
判据：所有检查项必须 PASS，否则写关节角/映射前先修。
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import pose_gen  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.fk import Rig  # noqa: E402

FAILS: list[str] = []
LINES: list[str] = []


def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}{('  ' + detail) if detail else ''}")
    LINES.append(f"- **{tag}** {name} {detail}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    bmap = json.load(open(os.path.join(ROOT, "config", "bones_map.json"), encoding="utf-8"))["bones"]
    zero = {n: np.eye(4) for n in rig.names}
    rest = rig.compute(zero)

    LINES.append("# 骨架体检报告\n")

    # ---------- 1. 完整性 ----------
    print("[1] 骨骼完整性")
    LINES.append("\n## 1. 骨骼完整性\n")
    check("骨骼数 = 209", len(rig.names) == 209, str(len(rig.names)))
    check("bones_map 覆盖全部", set(rig.names) == set(bmap), 
          f"缺 {len(set(rig.names)-set(bmap))}")
    check("parent 全可解析", all(p is None or p in rig.names for p in rig.parent.values()))
    check("names 与 parent 键一致", set(rig.names) == set(rig.parent))

    # ---------- 2. rest 矩阵健全 ----------
    print("[2] rest 矩阵健全")
    LINES.append("\n## 2. rest 矩阵健全\n")
    bad_rot = []
    for n, M in rest.items():
        R = M[0:3, 0:3]
        if not np.allclose(R @ R.T, np.eye(3), atol=1e-4):
            bad_rot.append(n)
    check("所有旋转正交", not bad_rot, str(bad_rot[:3]))
    check("无 NaN", all(np.isfinite(M).all() for M in rest.values()))
    dets = [float(np.linalg.det(M[0:3, 0:3])) for M in rest.values()]
    check("行列式 ≈ 1（无镜像）", all(abs(d - 1) < 1e-4 for d in dets),
          f"min={min(dets):.6f} max={max(dets):.6f}")

    # ---------- 3. 局部轴 ----------
    print("[3] 主骨局部轴（世界坐标）")
    LINES.append("\n## 3. 主骨局部轴（世界坐标）\n")
    LINES.append("| 骨 | 局部X→世界 | 局部Y→世界 | 局部Z→世界 |")
    LINES.append("|---|---|---|---|")
    for bone in ("spine", "chest", "head", "leftUpperArm", "rightUpperArm",
                 "leftLowerArm", "rightLowerArm", "leftUpperLeg", "rightUpperLeg"):
        if bone not in rest:
            continue
        R = rest[bone][0:3, 0:3]
        ax = [f"({R[0,i]:+.2f},{R[1,i]:+.2f},{R[2,i]:+.2f})" for i in range(3)]
        LINES.append(f"| {bone} | {ax[0]} | {ax[1]} | {ax[2]} |")

    # ---------- 4. 左右镜像性 ----------
    print("[4] 左右镜像性（关键：这个模型不是标准镜像）")
    LINES.append("\n## 4. 左右镜像性\n")
    # X 轴：右侧镜像后 x 取反
    for a, b in (("leftUpperArm", "rightUpperArm"),
                 ("leftLowerArm", "rightLowerArm"),
                 ("leftUpperLeg", "rightUpperLeg"),
                 ("leftFoot", "rightFoot")):
        if a not in rest or b not in rest:
            continue
        ya = rest[a][0:3, 1]
        yb = rest[b][0:3, 1]
        mirror = np.array([-ya[0], ya[1], ya[2]])
        d = float(np.linalg.norm(mirror - yb))
        check(f"{a} ↔ {b} 关于 X 镜像", d < 0.05, f"diff={d:.3f}")

    # ---------- 5. 绕世界轴运动 ----------
    print("[5] 绕世界轴运动（±30° 末端位移方向）")
    LINES.append("\n## 5. 绕世界轴运动\n")
    LINES.append("| 骨 | 轴 | +30° 主方向 | 位移 mm |")
    LINES.append("|---|---|---|---|")
    WATCH = {
        "rightUpperArm": "rightHand", "leftUpperArm": "leftHand",
        "rightLowerArm": "rightHand", "leftLowerArm": "leftHand",
        "rightUpperLeg": "rightFoot", "leftUpperLeg": "leftFoot",
        "spine": "head", "head": "mixamorig:HeadTop_End",
    }
    for bone, watch in WATCH.items():
        if bone not in rest:
            continue
        base = rest[watch][0:3, 3].copy()
        for axis in ("x", "y", "z"):
            basis = {n: np.eye(4) for n in rig.names}
            basis[bone] = bm.basis_from_world_rot(rig, bone, axis, 30.0)
            M = rig.compute(basis)
            d = (M[watch][0:3, 3] - base) * 1000.0
            idx = int(np.argmax(np.abs(d)))
            LINES.append(f"| {bone} | {axis} | {'+' if d[idx]>0 else '-'}{'XYZ'[idx]} "
                         f"| {abs(d[idx]):.0f} |")

    # ---------- 6. pose_gen 参数有效性 ----------
    print("[6] pose_gen 参数有效性（每个参数改动能动到哪）")
    LINES.append("\n## 6. pose_gen 参数有效性\n")
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    P0 = dict(fams["wave"]["P_default"])
    def with_tip(M):
        """加一个合成的「手尖」点（手骨沿局部 Y 再延伸 40mm），用来观察腕/手参数。"""
        M = dict(M)
        for s in ("left", "right"):
            b = f"{s}Hand"
            if b in M:
                M[f"_HANDTIP_{s[0].upper()}"] = M[b].copy()
                M[f"_HANDTIP_{s[0].upper()}"][0:3, 3] = M[b][0:3, 3] + M[b][0:3, 1] * 0.04
        return M

    base_M = with_tip(rig.compute(pose_gen.build(P0, rig, bm, active_side="right")))

    CASES = [
        ("lean", 15, "head"), ("side_bend", 15, "head"),
        ("head_nod", 15, "mixamorig:HeadTop_End"), ("head_tilt", 15, "mixamorig:HeadTop_End"), ("head_yaw", 15, "mixamorig:HeadTop_End"),
        ("arm_lift", 40, "rightHand"), ("arm_swing", 30, "rightHand"),
        ("arm_roll", 40, "rightHand"), ("elbow_bend", 40, "rightHand"),
        ("wrist_bend", 30, "_HANDTIP_R"), ("hand_bias", 25, "_HANDTIP_R"),
        ("tail_curl", 20, "WhaleTail_04"),
    ]
    LINES.append("| 参数 | 改动 | 影响骨 | 位移 mm | 是否有效 |")
    LINES.append("|---|---|---|---|---|")
    for key, delta, watch in CASES:
        P = dict(P0)
        P[key] = float(P[key]) + delta
        M = with_tip(rig.compute(pose_gen.build(P, rig, bm, active_side="right")))
        if watch not in M or watch not in base_M:
            LINES.append(f"| {key} | +{delta} | {watch} | — | 骨不在 M 里 |")
            check(f"{key} 有影响", False, f"{watch} 不存在")
            continue
        d = float(np.linalg.norm(M[watch][0:3, 3] - base_M[watch][0:3, 3]) * 1000.0)
        ok = d > 1.0
        LINES.append(f"| {key} | +{delta} | {watch} | {d:.1f} | {'✅' if ok else '❌ 无效'} |")
        check(f"{key} 有影响（>1mm）", ok, f"{d:.1f}mm")

    # ---------- 7. 手臂生理合理性 ----------
    print("[7] 手臂生理合理性（肘不能反关节）")
    LINES.append("\n## 7. 手臂生理合理性\n")
    for eb in (0, 45, 90, 130):
        P = dict(P0)
        P["arm_lift"] = 90
        P["elbow_bend"] = eb
        M = with_tip(rig.compute(pose_gen.build(P, rig, bm, active_side="right")))
        sh = M["rightShoulder"][0:3, 3]
        el = M["rightLowerArm"][0:3, 3]
        hd = M["rightHand"][0:3, 3]
        v1, v2 = sh - el, hd - el
        ang = float(np.degrees(np.arccos(np.clip(
            np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)), -1, 1))))
        # 肘角应当随 elbow_bend 单调下降，且始终在合理范围
        ok = 20 < ang < 180
        LINES.append(f"- elbow_bend={eb} → 实际肘角 {ang:.1f}° {'✅' if ok else '❌'}")
        if eb == 0:
            check("elbow=0 时接近伸直（>150°）", ang > 150, f"{ang:.1f}°")
        if eb == 130:
            check("elbow=130 时明显弯曲（<110°）", ang < 110, f"{ang:.1f}°")

    # ---------- 8. 左右手对称 ----------
    print("[8] 左右手对称（同参数下镜像）")
    LINES.append("\n## 8. 左右手对称\n")
    P = dict(P0)
    P["arm_lift"] = 90
    P["elbow_bend"] = 80
    MR = rig.compute(pose_gen.build(P, rig, bm, active_side="right"))
    ML = rig.compute(pose_gen.build(P, rig, bm, active_side="left"))
    hr, hl = MR["rightHand"][0:3, 3], ML["leftHand"][0:3, 3]
    check("右手在右侧", float(hr[0]) < 0, f"x={hr[0]:.3f}")
    check("左手在左侧", float(hl[0]) > 0, f"x={hl[0]:.3f}")
    sym = float(np.linalg.norm(np.array([-hr[0], hr[1], hr[2]]) - hl))
    check("左右关于 X 对称（<20mm）", sym < 0.02, f"{sym*1000:.1f}mm")
    check("左右高度接近", abs(float(hr[2] - hl[2])) < 0.02,
          f"{abs(float(hr[2]-hl[2]))*1000:.1f}mm")

    # ---------- 9. 写报告 ----------
    out = os.path.join(ROOT, "docs", "RIG_AUDIT.md")
    head = (f"# 骨架体检报告\n\n结论：{'全部通过' if not FAILS else '有 ' + str(len(FAILS)) + ' 项失败'}\n\n")
    open(out, "w", encoding="utf-8").write(head + "\n".join(LINES) + "\n")

    print("\n" + "-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
    else:
        print("RESULT: PASS")
    print("WROTE", out)
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())



