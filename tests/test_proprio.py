"""tests/test_proprio.py — P1 验收。

怎么测：python tests/test_proprio.py
预期现象：[proprio] ALL PASS
调参位置：core/proprio.py、tests/fixtures/mesh_clusters.json
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bones import BoneMap  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.proprio import centroid, endpoints, snapshot, sole_z  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RIG = os.path.join(HERE, "fixtures", "rig_data.json")
MESH = os.path.join(HERE, "fixtures", "mesh_clusters.json")
MAP = os.path.join(os.path.dirname(HERE), "config", "bones_map.json")

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def world_rot(rest3: np.ndarray, axis: str, deg: float) -> np.ndarray:
    """已废弃：保留仅为说明。请用 core.bones.BoneMap.basis_from_world_rot()。"""
    raise NotImplementedError("use BoneMap.basis_from_world_rot")


def main() -> int:
    with open(RIG, "r", encoding="utf-8") as f:
        rig_d = json.load(f)
    with open(MESH, "r", encoding="utf-8") as f:
        mesh_d = json.load(f)

    rig = Rig(rig_d["rest"], rig_d["parent"], rig_d["connected"], rig_d["order"])
    bm = BoneMap.load(MAP)
    clusters = mesh_d["clusters"]
    sole_pts = mesh_d["sole"]

    zero = {n: np.eye(4) for n in rig_d["order"]}
    M0 = rig.compute(zero)
    snap0 = snapshot(M0, clusters, sole_pts)

    print("[1] rest 姿态快照")
    check("关节数 = 209", len(snap0["joints"]) == 209, f"got {len(snap0['joints'])}")
    check("末端齐全", all(k in snap0["endpoints"] for k in
                         ("leftHand", "rightHand", "leftFoot", "rightFoot", "head")))
    com = snap0["com"]
    check("质心 x≈0（左右居中）", abs(com[0]) < 0.02, f"x={com[0]:.4f}")
    check("质心 z 在人体高度内", 0.35 < com[2] < 0.95, f"z={com[2]:.4f}")
    check("双脚都在地面附近", all(abs(z) < 0.05 for z in snap0["sole_z"].values()),
          str({k: round(v, 4) for k, v in snap0["sole_z"].items()}))

    print("[2] 前倾时质心前移（y 变小，因为脸朝 -Y）")
    lean = {n: np.eye(4) for n in rig_d["order"]}
    lean["spine"] = bm.basis_from_world_rot(rig, "spine", "x", 20)
    Ml = rig.compute(lean)
    com_l = centroid(Ml, clusters)
    check("质心 y 前移", com_l[1] < com[1] - 0.005, f"{com[1]:.4f} -> {com_l[1]:.4f}")

    print("[3] 抬腿时支撑面上升（foot 是 connected，不能直接设 location）")
    lift = {n: np.eye(4) for n in rig_d["order"]}
    lift["leftUpperLeg"] = bm.basis_from_world_rot(rig, "leftUpperLeg", "x", -40)
    Mlf = rig.compute(lift)
    s = sole_z(Mlf, sole_pts)
    check("左脚最低点上升", s["leftFoot"] > snap0["sole_z"]["leftFoot"] + 0.05,
          f"{snap0['sole_z']['leftFoot']:.4f} -> {s['leftFoot']:.4f}")
    check("右脚仍在地面", abs(s["rightFoot"] - snap0["sole_z"]["rightFoot"]) < 1e-6,
          f"{s['rightFoot']:.4f}")

    print("[4] 快照字段自洽")
    check("support_z == min(sole_z)",
          abs(snap0["support_z"] - min(snap0["sole_z"].values())) < 1e-12)
    check("endpoints 与 joints 同源",
          np.allclose(snap0["endpoints"]["head"], snap0["joints"]["head"]))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
