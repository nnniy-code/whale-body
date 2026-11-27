"""tools/walk_state.py — 带 BodyState 的全身走路（D 的第①步）。

和 `walk_full.build_walk` 的区别：
  旧：站着踏步（state 固定为原点）
  新：身体在世界里移动，hips 平移 = BodyState.offset()

**关键约束**：`pose_gen` 的 hips 平移 和 `ik.leg_basis` 的 `hip_offset`
必须是**同一个值**，否则腿和身体脱节。

怎么测：python tools/walk_state.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import gait, ik, pose_gen  # noqa: E402
from core.body_state import BodyState  # noqa: E402

ANKLE_HEIGHT = 0.080      # rest 踝 z（实测）


def _translate_local(basis: np.ndarray, rest4: np.ndarray,
                      dx: float, dy: float, dz: float) -> np.ndarray:
    """给根骨的 basis 加**世界**平移。

    basis 的平移会先被 rest 的旋转部分作用（M = rest @ basis），
    所以要把世界偏移转成局部：local = R_rest⁻¹ @ world。
    """
    out = np.array(basis, dtype=float, copy=True)
    R = np.asarray(rest4, dtype=float)[0:3, 0:3]
    out[0:3, 3] += R.T @ np.array([dx, dy, dz], dtype=float)
    return out


def build_walk_state(G: dict, P: dict, t: float, state: BodyState,
                     rig, bm, start: dict, hip_drop: float = 0.08) -> tuple[dict, dict]:
    """带身体位置的全身走路。

    state 提供 x/y/yaw/bob；脚目标用世界坐标（含 state 位置）。
    """
    # 1) 上半身
    basis = pose_gen.build(P, rig, bm, active_side=None)

    # 2) 根骨平移（和腿 IK 用**同一个** offset，含 hip_drop）
    #    关键：脚目标 z **不跟 hips 降**（脚要留在地面），否则相对距离不变、
    #    腿还是够不到。
    off = state.offset()
    off_drop = (off[0], off[1], off[2] - hip_drop)
    if "hips" in basis:
        basis["hips"] = _translate_local(basis["hips"], rig.rest["hips"], *off_drop)

    # 3) 脚目标：**相对身体**的周期位置（travel=False，不前进）
    #    前进由 BodyState 唯一负责，避免双重计算。
    #    origin 固定为 rest 脚 y（foot_targets 的 y 从 body_origin 起算）
    origin = [0.0, float(start["left"][1]), 0.0]
    ft = gait.foot_targets(G, t, start=start, body_origin=origin, travel=False)
    # 加上身体位移 → 世界坐标
    for k in ft:
        ft[k] = [float(ft[k][0]) + off[0], float(ft[k][1]) + off[1], float(ft[k][2])]
    # 注意：z 不加 off[2]（bob 不传到脚）

    # 4) 腿 IK：hip_offset 必须等于上面的 off（加 hip_drop）
    hip_off = off_drop
    diag = {"foot_targets": {}, "foot_actual": {}, "err_mm": {}, "ik_fail": {}}
    for side in ("left", "right"):
        tgt = np.array(ft[side], dtype=float)
        tgt[2] += ANKLE_HEIGHT
        lb, why = ik.leg_basis(rig, bm, side, tgt, hip_offset=hip_off)
        if lb is None:
            diag["ik_fail"][side] = str(why)
        else:
            basis.update(lb)
        diag["foot_targets"][side] = [round(float(x), 4) for x in tgt]

    # 5) 验证
    M = rig.compute(basis)
    for side in ("left", "right"):
        actual = M[f"{side}Foot"][0:3, 3]
        tgt = np.array(ft[side], dtype=float)
        tgt[2] += ANKLE_HEIGHT
        diag["foot_actual"][side] = [round(float(x), 4) for x in actual]
        diag["err_mm"][side] = round(float(np.linalg.norm(actual - tgt)) * 1000, 1)
    return basis, diag


def main() -> int:
    from core.bones import BoneMap
    from core.fk import Rig

    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    M0 = rig.compute({n: np.eye(4) for n in rigd["order"]})
    start = {s: [float(M0[f"{s}Foot"][0, 3]), float(M0[f"{s}Foot"][1, 3]), 0.0]
             for s in ("left", "right")}

    G = dict(fams["walk"]["G"])
    G["period"] = 1.0
    G["stride"] = 0.5
    G["foot_lift"] = 0.06
    P = dict(fams["walk"]["P_default"])

    st = BodyState(vel=0.0)          # 先站着，验证 hips 平移
    print(f"{'x':>6} {'y':>6} {'bob':>7} {'左踝z':>7} {'右踝z':>7} {'误差mm':>7}")
    for x in (0.0, 0.5, 1.0):
        st2 = BodyState(x=x, y=0.0, bob=0.01)
        b, d = build_walk_state(G, P, 0.0, st2, rig, bm, start)
        M = rig.compute(b)
        print(f"{x:6.2f} {0.0:6.2f} {st2.bob:7.3f} "
              f"{float(M['leftFoot'][2,3]):7.3f} {float(M['rightFoot'][2,3]):7.3f} "
              f"{max(d['err_mm'].values()):7.1f}")
        print(f"      左脚 world={d['foot_actual']['left']}  目标={d['foot_targets']['left']}")

    print("\n=== 走路 3 秒（vel=0.5）===")
    st3 = BodyState(vel=0.5)
    t, dt = 0.0, 1.0 / 12
    print(f"{'t':>5} {'x':>6} {'y':>7} {'bob':>7} {'左踝z':>7} {'右踝z':>7} {'误差':>6}")
    for i in range(13):
        b, d = build_walk_state(G, P, t, st3, rig, bm, start)
        M = rig.compute(b)
        print(f"{t:5.2f} {st3.x:6.3f} {st3.y:7.3f} {st3.bob:7.4f} "
              f"{float(M['leftFoot'][2,3]):7.3f} {float(M['rightFoot'][2,3]):7.3f} "
              f"{max(d['err_mm'].values()):6.1f}")
        st3.step(dt)
        t += dt
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




