"""tools/probe_leg_ik2.py — 验证 leg_basis 的数学：目标=rest 位置时应得到恒等 basis。"""
import json
import sys

import numpy as np

sys.path.insert(0, r"D:\DSproject\body-v2")

from core import ik
from core.bones import BoneMap
from core.fk import Rig


def main():
    R = r"D:\DSproject\body-v2"
    d = json.load(open(R + r"\tests\fixtures\rig_data.json", encoding="utf-8"))
    rig = Rig(d["rest"], d["parent"], d["connected"], d["order"])
    bm = BoneMap.load(R + r"\config\bones_map.json")
    M0 = rig.compute({n: np.eye(4) for n in d["order"]})

    ankle_rest = M0["leftFoot"][0:3, 3].copy()
    print("rest 踝:", [round(float(x), 4) for x in ankle_rest])

    # 目标 = rest 踝（应该得到「几乎恒等」的 basis）
    b, why = ik.leg_basis(rig, bm, "left", ankle_rest)
    if b is None:
        print("IK 失败:", why)
        return 1

    print("\n目标=rest 踝时的 basis（应接近单位矩阵）:")
    for bone in ("leftUpperLeg", "leftLowerLeg", "leftFoot"):
        m = b[bone]
        off = float(np.max(np.abs(m - np.eye(4))))
        print(f"  {bone:14s} max|basis-I| = {off:.4f}")

    # 实际 FK 后脚到哪
    cur = {n: np.eye(4) for n in d["order"]}
    cur.update(b)
    M = rig.compute(cur)
    got = M["leftFoot"][0:3, 3]
    err = float(np.linalg.norm(got - ankle_rest) * 1000)
    print(f"\nFK 后踝: {[round(float(x),4) for x in got]}")
    print(f"目标   : {[round(float(x),4) for x in ankle_rest]}")
    print(f"误差   : {err:.1f} mm")

    # 再试一个明确的目标（前移 10cm）
    tgt = ankle_rest + np.array([0.0, -0.10, 0.0])
    b2, why2 = ik.leg_basis(rig, bm, "left", tgt, hip_offset=(0, 0, -0.03))
    if b2 is None:
        print("\n前移目标 IK 失败:", why2)
        return 1
    cur2 = {n: np.eye(4) for n in d["order"]}
    cur2.update(b2)
    M2 = rig.compute(cur2)
    got2 = M2["leftFoot"][0:3, 3]
    err2 = float(np.linalg.norm(got2 - tgt) * 1000)
    print(f"\n前移目标: {[round(float(x),4) for x in tgt]}")
    print(f"FK 到达 : {[round(float(x),4) for x in got2]}")
    print(f"误差    : {err2:.1f} mm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
