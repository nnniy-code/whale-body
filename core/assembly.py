"""core/assembly.py — 全身装配：上半身（求解）+ 根骨平移 + 腿 IK（脚目标源）。

设计依据：DEVLOG 2026-11-25「腿的三种驱动」

**为什么单独一个模块**：
  之前装配逻辑散在 `tools/walk_state.py` 和 `tools/test_foot_target.py` 里，
  两份几乎一样的代码，容易改一边漏一边。

**唯一的装配入口**：

    basis, diag = build_full_body(P, foot_target, state, t, rig, bm)

**三条硬约束**（踩过坑，别再犯）：
  1. `hips` 的 basis 平移会被 rest 旋转作用 → 必须转成局部
     `local = R_rest⁻¹ @ world`
  2. 脚目标的 **z 不跟 hips 降**（脚要留在地面），
     否则相对距离不变、腿还是够不到
  3. **装配在「身体局部系」里做，最后一次性搬到世界**（v1.9）：
     腿 IK 假设 hips 不转（`ik.leg_basis`），如果直接在旋转过的 hips 上解，
     脚会绕身体画弧、而且是「够不着」（实测转 90° 髋到脚要 441mm > 427mm）。
     所以脚目标由 `FootTarget.positions()` 直接给**局部系**坐标，装完再把
     hips 整体转为 `D = (Rz(yaw), (x,y))`：

         M[hips]_world = D @ M[hips]_local
         M[child]_world = D @ M[child]_local      （basis 不变，整棵子树刚体变换）

     代价：转身时脚跟着身体转（世界坐标里会画弧），这是 §22.6 第 3 项
     「支撑脚当轴」还没做的遗留问题，不是这里的 bug。
"""

from __future__ import annotations

import math

import numpy as np

from core import pose_gen


def translate_local(basis: np.ndarray, rest4: np.ndarray,
                    dx: float, dy: float, dz: float) -> np.ndarray:
    """给根骨的 basis 加**世界**平移。

    `M = rest @ basis`，所以 basis 的平移会先被 rest 的旋转作用。
    要把世界偏移转成局部：`local = R_rest⁻¹ @ world`。
    """
    out = np.array(basis, dtype=float, copy=True)
    R = np.asarray(rest4, dtype=float)[0:3, 0:3]
    out[0:3, 3] += R.T @ np.array([dx, dy, dz], dtype=float)
    return out


def rot_z(deg: float) -> np.ndarray:
    """绕世界 Z 的 3x3 旋转。"""
    a = math.radians(float(deg))
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]], dtype=float)


def place_hips(basis_hips: np.ndarray, rest4: np.ndarray,
               yaw: float, tx: float, ty: float) -> np.ndarray:
    """把局部系的 hips 整体搬到世界：`M' = D @ M`，D = 转 yaw + 平移 (tx,ty)。"""
    D = np.eye(4)
    D[0:3, 0:3] = rot_z(yaw)
    D[0:3, 3] = [float(tx), float(ty), 0.0]
    rest = np.asarray(rest4, dtype=float)
    return np.linalg.inv(rest) @ D @ rest @ np.asarray(basis_hips, dtype=float)


def _gait_phase(foot_target, t: float):
    """步态相位 0..1；非周期动作返回 None（摆臂保持静态角度）。"""
    G = getattr(foot_target, "G", None)
    if not G:
        return None
    try:
        if abs(float(G.get("stride", 0.0))) <= 1e-9:
            return None
        period = max(float(G.get("period", 1.0)), 1e-3)
        return (float(t) / period) % 1.0
    except (TypeError, ValueError):
        return None


def build_full_body(P: dict, foot_target, state, t: float, rig, bm,
                    active_side: str | None = None) -> tuple[dict, dict]:
    """装配完整身体姿态。

    P          : 上半身参数（solver 解的，或族默认）
    foot_target: `core.foot_target.FootTarget` 实例（Gait/Static/Point）
    state      : `core.body_state.BodyState`（x/y/yaw/bob）
    t          : 时间（秒），步态相位用

    返回 (basis, diag)。diag 含每只脚的误差和 IK 失败原因。
    """
    basis = pose_gen.build(P, rig, bm, active_side=active_side,
                           phase=_gait_phase(foot_target, t))

    drop = float(foot_target.hip_drop()) if foot_target is not None else 0.0
    dz = float(getattr(state, "bob", 0.0)) - drop
    yaw = float(getattr(state, "yaw", 0.0))
    sx = float(getattr(state, "x", 0.0))
    sy = float(getattr(state, "y", 0.0))

    # 1) 局部系：不平移、不转向，髋只做上下浮动
    if "hips" in basis:
        basis["hips"] = translate_local(basis["hips"], rig.rest["hips"], 0.0, 0.0, dz)

    if foot_target is None:
        return basis, {"foot_targets": {}, "foot_actual": {}, "err_mm": {},
                       "ik_fail": {}, "yaw": round(yaw % 360.0, 2)}

    # 2) 脚目标已经是**身体局部系**（foot_target 契约），直接用
    targets = foot_target.positions(state, t)

    # 3) 腿 IK（局部系里 hips 只有平移、没有旋转，ik 的假设成立）
    from core.foot_target import attach_legs
    basis, diag = attach_legs(basis, targets, (0.0, 0.0, dz),
                              rig, bm, foot_target=foot_target)

    # 4) 整体搬到世界
    if "hips" in basis:
        basis["hips"] = place_hips(basis["hips"], rig.rest["hips"], yaw, sx, sy)

    diag["hip_offset"] = [round(x, 4) for x in (sx, sy, dz)]
    diag["hip_drop"] = round(drop, 3)
    diag["yaw"] = round(yaw % 360.0, 2)
    return basis, diag
