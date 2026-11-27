"""tests/test_fk_golden.py — P0.5 验收（FK golden test）。

怎么测：python tests/test_fk_golden.py
预期现象：[fk] ALL PASS (9 cases, worst 0.0003mm)
调参位置：core/fk.py（prelocal 预计算）、tests/fixtures/rig_data.json（重新导出）

对照数据由 Blender 导出：每个用例同时存 basis（输入）与 truth（bpy 的 pose matrix）。
"""

from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.fk import Rig  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "rig_data.json")

MAIN21 = [
    "hips", "leftUpperLeg", "leftLowerLeg", "leftFoot",
    "rightUpperLeg", "rightLowerLeg", "rightFoot",
    "spine", "chest", "upperChest",
    "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand",
    "neck", "head", "mixamorig:HeadTop_End",
    "rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand",
]

POS_TOL_MM = 0.01
ROT_TOL_DEG = 0.01

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def rot_err_deg(a: np.ndarray, b: np.ndarray) -> float:
    ra = a[0:3, 0:3]
    rb = b[0:3, 0:3]
    # 正交化（防缩放污染）
    ua, _, va = np.linalg.svd(ra)
    ub, _, vb = np.linalg.svd(rb)
    ra = ua @ va
    rb = ub @ vb
    cos = (np.trace(ra.T @ rb) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def mat_scale(m: np.ndarray) -> tuple[float, float, float]:
    return (float(np.linalg.norm(m[0:3, 0])),
            float(np.linalg.norm(m[0:3, 1])),
            float(np.linalg.norm(m[0:3, 2])))


def main() -> int:
    if not os.path.exists(FIXTURE):
        print(f"缺少 fixture：{FIXTURE}")
        print("用 Blender 重新导出：见 docs/design/12-fk-golden.md")
        return 2

    with open(FIXTURE, "r", encoding="utf-8") as f:
        d = json.load(f)

    rig = Rig(d["rest"], d["parent"], d["connected"], d["order"])
    names = d["order"]
    worst_pos = 0.0
    worst_rot = 0.0
    worst_case = ""

    print(f"[fk] {len(d['cases'])} 用例 × {len(names)} 骨")

    for case in d["cases"]:
        M = rig.compute(case["basis"])
        cpos = 0.0
        crot = 0.0
        for n in names:
            truth = np.asarray(case["truth"][n], dtype=np.float64)
            got = M[n]
            cpos = max(cpos, float(np.linalg.norm(got[0:3, 3] - truth[0:3, 3])) * 1000.0)
            crot = max(crot, rot_err_deg(got, truth))
        ok = cpos < POS_TOL_MM and crot < ROT_TOL_DEG
        print(f"  [{('PASS' if ok else 'FAIL')}] {case['label']:<14} "
              f"pos={cpos:.6f}mm rot={crot:.6f}deg")
        if not ok:
            FAILS.append(f"case:{case['label']}")
        if cpos > worst_pos or crot > worst_rot:
            worst_case = case["label"]
        worst_pos = max(worst_pos, cpos)
        worst_rot = max(worst_rot, crot)

    a = d["asserts"]

    print("[asserts]")
    check("IK influence 全 0", all(abs(x) < 1e-9 for x in a["ik_influence"]),
          str(a["ik_influence"]))
    mw = np.asarray(d["matrix_world"], dtype=np.float64)
    mw_dev = float(np.abs(mw - np.eye(4)).max())
    check("armature matrix_world ≈ 单位（实测偏差 2.4e-8）", mw_dev < 1e-6, f"maxdiff={mw_dev:.2e}")
    check("connected 骨数 152", a["n_connected"] == 152, f"got {a['n_connected']}")
    check("unconnected 骨数 57", a["n_unconnected"] == 57, f"got {a['n_unconnected']}")

    # 所有骨的 scale 都是 1（取最后一组真值）
    scales = [mat_scale(np.asarray(c["truth"][n], dtype=np.float64)) for c in d["cases"][:1] for n in names]
    check("所有骨 scale≈1", all(abs(s - 1.0) < 1e-4 for tri in scales for s in tri))

    # connected 骨 location 被忽略：单独那组的误差已经在上面的 case 循环里验过
    conn_case = next((c for c in d["cases"] if c["label"] == "connected_loc"), None)
    if conn_case is None:
        check("connected_loc 用例存在", False, "fixture 缺这个用例")
    else:
        M = rig.compute(conn_case["basis"])
        err = 0.0
        for n in names:
            truth = np.asarray(conn_case["truth"][n], dtype=np.float64)
            err = max(err, float(np.linalg.norm(M[n][0:3, 3] - truth[0:3, 3])) * 1000.0)
        check("connected 骨 location 被忽略", err < POS_TOL_MM, f"max_err={err:.6f}mm")

    # 子骨架（21 主骨）与全量一致
    print("[subset]")
    sub = rig.subset(MAIN21)
    case0 = d["cases"][1]      # rand30，非零 basis
    full = rig.compute(case0["basis"])
    part = sub.compute(case0["basis"])
    serr = max(float(np.linalg.norm(part[n][0:3, 3] - full[n][0:3, 3])) * 1000.0 for n in MAIN21)
    check("21 主骨子集与全量一致", serr < 1e-6, f"max={serr:.9f}mm")

    print("-" * 52)
    print(f"worst: {worst_case}  pos={worst_pos:.6f}mm  rot={worst_rot:.6f}deg")
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
