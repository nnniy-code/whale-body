"""tools/walk_full.py — 全身走路姿态：上半身 P + 步态脚目标 + 腿 IK。

之前的 `pose_gen.build` 只处理上半身和尾巴，**腿一直是 rest 姿态**。
这个模块补上：脚目标 → IK → 腿的 basis。

怎么测：python tools/walk_full.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import gait, ik, pose_gen  # noqa: E402

# 走路时身体下沉多少（腿要弯才够得着地面）
HIP_DROP = 0.0

# **关键**：gait.foot_targets 返回的是「脚底」位置（z=0），
# 但 ik.leg_basis 的目标是「踝」（rest 时踝在 z=0.0775）。
# 不加这个偏移，IK 会报 unreachable（差 8cm）。
ANKLE_HEIGHT = 0.080   # rest 踝 z（实测）


def build_walk(G: dict, P: dict, t: float, rig, bm,
               start: dict, origin: list | None = None,
               hip_drop: float = HIP_DROP) -> tuple[dict, dict]:
    """全身走路姿态。

    返回 (basis, 诊断 {foot_targets, foot_actual, err_mm})
    """
    origin = origin or [0.0, 0.0, 0.0]
    # 1) 上半身
    basis = pose_gen.build(P, rig, bm, active_side=None)

    # 2) 脚目标
    ft = gait.foot_targets(G, t, start=start, body_origin=origin)

    # 3) 腿 IK（身体下沉，否则腿够不到目标）
    off = (0.0, 0.0, -hip_drop)
    diag = {"foot_targets": {}, "foot_actual": {}, "err_mm": {}}
    for side in ("left", "right"):
        tgt = np.array(ft[side], dtype=float)
        tgt[2] += ANKLE_HEIGHT          # 脚底 → 踝
        lb, why = ik.leg_basis(rig, bm, side, tgt, hip_offset=off)
        if lb is None:
            diag.setdefault("ik_fail", {})[side] = str(why)
        else:
            basis.update(lb)
        diag["foot_targets"][side] = [round(float(x), 4) for x in tgt]

    # 4) 验证：算 FK 看脚到了没
    M = rig.compute(basis)
    for side in ("left", "right"):
        actual = M[f"{side}Foot"][0:3, 3]
        tgt = np.array(ft[side], dtype=float)
        tgt[2] += ANKLE_HEIGHT          # 目标也要抬到踝高度才对得上
        diag["foot_actual"][side] = [round(float(x), 4) for x in actual]
        diag["err_mm"][side] = round(float(np.linalg.norm(actual - tgt)) * 1000, 1)
    return basis, diag


def main() -> int:
    import core.fk as fkmod
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = fkmod.Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    from core.bones import BoneMap
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]

    G = dict(fams["walk"]["G"])
    G["period"] = 1.0
    # 原地踏步：stride=0，只抬脚不前进。验证腿 IK 会不会动。
    G["stride"] = 0.0
    G["foot_lift"] = 0.06
    P = dict(fams["walk"]["P_default"])
    # start 必须是 **rest 姿态的脚底位置**（从 rig 读，不猜）
    M0 = rig.compute({n: np.eye(4) for n in rigd["order"]})
    start = {}
    for s in ("left", "right"):
        p_ankle = M0[f"{s}Foot"][0:3, 3]
        start[s] = [float(p_ankle[0]), float(p_ankle[1]), 0.0]   # 脚底 z=0
    # foot_targets 的 y 从 body_origin 起算（不像 x 有 lateral 补正），
    # 所以 origin 的 y 必须等于 rest 脚的 y
    origin = [0.0, float(M0["leftFoot"][1, 3]), 0.0]
    print("start", start, "origin", origin)

    print(f"{'phase':>6} {'左脚误差':>9} {'右脚误差':>9}  {'左脚z':>7} {'右脚z':>7}")
    for i in range(9):
        t = 1.0 * i / 8
        basis, diag = build_walk(G, P, t, rig, bm, start, origin=origin)
        ph = gait.phase_of(t, G["period"])
        lz = diag["foot_actual"]["left"][2]
        rz = diag["foot_actual"]["right"][2]
        fail = diag.get("ik_fail") or {}
        note = f"   IK失败:{list(fail)}" if fail else ""
        print(f"{ph:6.2f} {diag['err_mm']['left']:9.1f} {diag['err_mm']['right']:9.1f}  "
              f"{lz:7.3f} {rz:7.3f}{note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())






