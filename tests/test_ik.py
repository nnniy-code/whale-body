"""tests/test_ik.py — R6 验收（两骨 IK 闭环）。

怎么测：python tests/test_ik.py
预期现象：[ik] ALL PASS（脚到目标 <1mm）
调参位置：core/ik.py 的 solve_two_bone
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bones import BoneMap  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.ik import leg_basis, solve_two_bone  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RIG = os.path.join(HERE, "fixtures", "rig_data.json")
MAP = os.path.join(os.path.dirname(HERE), "config", "bones_map.json")

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    rig_d = json.load(open(RIG, encoding="utf-8"))
    rig = Rig(rig_d["rest"], rig_d["parent"], rig_d["connected"], rig_d["order"])
    bm = BoneMap.load(MAP)
    zero = {n: np.eye(4) for n in rig_d["order"]}

    up, lo, ft = "leftUpperLeg", "leftLowerLeg", "leftFoot"
    hip = np.asarray(rig.rest[up], float)[0:3, 3]
    ankle = np.asarray(rig.rest[ft], float)[0:3, 3]
    L1 = float(np.linalg.norm(hip - np.asarray(rig.rest[lo], float)[0:3, 3]))
    L2 = float(np.linalg.norm(np.asarray(rig.rest[lo], float)[0:3, 3] - ankle))
    print(f"[geometry] hip={[round(float(x),3) for x in hip]} ankle={[round(float(x),3) for x in ankle]}")
    print(f"[geometry] L1={L1*1000:.1f}mm L2={L2*1000:.1f}mm reach={(L1+L2)*1000:.1f}mm")

    def foot_after(basis_dict):
        M = rig.compute({**zero, **basis_dict})
        return M[ft][0:3, 3]

    print("[1] rest 目标（脚在原位）")
    basis, reason = leg_basis(rig, bm, "left", ankle)
    check("可解", basis is not None, str(reason))
    if basis:
        err = float(np.linalg.norm(foot_after(basis) - ankle)) * 1000
        check("脚到目标 <1mm", err < 1.0, f"{err:.3f}mm")

    print("[2] 抬脚 100mm")
    t2 = ankle + np.array([0, 0, 0.10])
    basis, reason = leg_basis(rig, bm, "left", t2)
    check("可解", basis is not None, str(reason))
    if basis:
        err = float(np.linalg.norm(foot_after(basis) - t2)) * 1000
        check("脚到目标 <1mm", err < 1.0, f"{err:.3f}mm")

    print("[3] 前移 120mm")
    t3 = ankle + np.array([0, -0.12, 0.02])
    basis, reason = leg_basis(rig, bm, "left", t3)
    check("可解", basis is not None, str(reason))
    if basis:
        err = float(np.linalg.norm(foot_after(basis) - t3)) * 1000
        check("脚到目标 <1mm", err < 1.0, f"{err:.3f}mm")

    print("[4] 多个目标批量")
    worst = 0.0
    okn = 0
    for dz in (0.0, 0.05, 0.10, 0.14):
        for dy in (-0.10, 0.0, 0.08):
            t = ankle + np.array([0, dy, dz])
            b, r = leg_basis(rig, bm, "left", t)
            if b is None:
                continue
            okn += 1
            worst = max(worst, float(np.linalg.norm(foot_after(b) - t)) * 1000)
    check("12 个目标解出 >=10（其余是真不可达）", okn >= 10, f"{okn}/12")
    check("最大误差 <2mm", worst < 2.0, f"{worst:.3f}mm")

    print("[5] 不可达 → 失败（D5）")
    far = ankle + np.array([0, 0, 1.5])
    b, r = leg_basis(rig, bm, "left", far)
    check("返回 None", b is None)
    check("reason 说明不可达", "unreachable" in str(r), str(r))

    print("[6] 太近 → 失败")
    near = hip + np.array([0, 0, -0.01])
    b, r = leg_basis(rig, bm, "left", near)
    check("返回 None", b is None, str(r))

    print("[7] 纯数学：solve_two_bone 边界")
    knee, ok, reason = solve_two_bone(np.array([0, 0, 0]), np.array([0, 0, -0.4]), 0.24, 0.24)
    check("伸展可达", ok, reason)
    check("膝盖在中间", knee is not None and abs(knee[2] + 0.2) < 0.05,
          str(None if knee is None else [round(float(x),3) for x in knee]))
    knee, ok, _ = solve_two_bone(np.array([0, 0, 0]), np.array([0, 0, -0.5]), 0.24, 0.24)
    check("超出总长失败", not ok)

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())


