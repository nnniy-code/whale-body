"""core/ik.py — 两骨 IK 解析解（D8：禁止用 Blender 约束）。

设计依据：docs/design/03-gate.md、HANDOFF D8

为什么必须自己解：
  场景里的 4 条 IK 约束 influence 全 0，Blender 不会替你算。
  而且 L2 求解循环在纯 FK 里跑，用不了 bpy 的约束求值。

做法：
  矢状面（YZ）内的两骨 IK。给定髋位置与脚目标，用余弦定理求膝位置，
  再把两段骨骼的世界朝向转成局部 basis。
  不可达直接返回 None（D5：失败就是失败，不 clamp）。
"""

from __future__ import annotations

import numpy as np


def rot_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """把向量 a 转到 b 的最短旋转（Rodrigues）。返回 3x3。"""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    a = a / (np.linalg.norm(a) + 1e-12)
    b = b / (np.linalg.norm(b) + 1e-12)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    if c > 1.0 - 1e-12:
        return np.eye(3)
    if c < -1.0 + 1e-12:
        # 反向：找任意垂直轴转 180°
        axis = np.cross(a, np.array([1.0, 0.0, 0.0]))
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, np.array([0.0, 1.0, 0.0]))
        axis = axis / np.linalg.norm(axis)
        return 2.0 * np.outer(axis, axis) - np.eye(3)
    k = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + k + k @ k / (1.0 + c)


def bone_dir(rest4: np.ndarray) -> np.ndarray:
    """骨骼 rest 的世界朝向（局部 +Y = head→tail）。"""
    d = np.asarray(rest4, float)[0:3, 1]
    n = np.linalg.norm(d)
    return d / (n if n > 1e-12 else 1.0)


def solve_two_bone(hip: np.ndarray, target: np.ndarray, l1: float, l2: float,
                   pole: np.ndarray | None = None):
    """两骨 IK。

    hip: 髋关节世界位置
    target: 脚（踝）目标世界位置
    l1/l2: 大腿/小腿长度
    pole: 膝朝向提示（世界方向），默认前方 (-Y)

    返回 (knee_pos, ok, reason)。
    """
    hip = np.asarray(hip, float)
    target = np.asarray(target, float)
    d_vec = target - hip
    d = float(np.linalg.norm(d_vec))
    if d < 1e-9:
        return None, False, "zero_distance"

    reach = l1 + l2
    # 1mm 容差：rest 姿态腿本来就接近伸直（实测 426.7/427.0 = 99.93%），
    # 几毛毫米的差是数值误差，不是真不可达。
    if d > reach + 0.001:
        return None, False, f"unreachable d={d*1000:.1f}mm > {reach*1000:.1f}mm"
    if d < abs(l1 - l2) * 1.001:
        return None, False, f"too_close d={d*1000:.1f}mm"

    d = min(d, reach - 1e-9)          # 贴到满伸，避免 sqrt 负数
    if d < 1e-6:
        return None, False, "zero_distance"

    dn = d_vec / float(np.linalg.norm(d_vec))
    # 膝在 hip→target 线上的投影距离
    a = (l1 * l1 - l2 * l2 + d * d) / (2.0 * d)
    h2 = l1 * l1 - a * a
    h = float(np.sqrt(max(0.0, h2)))

    # 垂直方向：pole 投影到与 dn 垂直的平面
    if pole is None:
        pole = np.array([0.0, -1.0, 0.0])
    pole = np.asarray(pole, float)
    perp = pole - np.dot(pole, dn) * dn
    if np.linalg.norm(perp) < 1e-6:
        perp = np.cross(dn, np.array([1.0, 0.0, 0.0]))
        if np.linalg.norm(perp) < 1e-6:
            perp = np.cross(dn, np.array([0.0, 0.0, 1.0]))
    perp = perp / np.linalg.norm(perp)

    knee = hip + a * dn + h * perp
    return knee, True, "ok"


def leg_basis(rig, bm, side: str, target: np.ndarray, pole=None,
              hip_offset=(0.0, 0.0, 0.0)):
    """解一条腿，返回 {骨: basis}。不可达返回 (None, reason)。

    **坐标系**：本函数算的是**身体局部系**（hips 只平移、没有旋转）。
    v1.9 起朝向由 `BodyState.yaw` 负责，装配层（`assembly.build_full_body`）
    先把世界脚目标反旋回局部系，装完再把 hips 整体搬到世界。
    这样不用改这里的数学，也不会出现「脚绕身体画弧」。

    hip_offset：髋的**局部系位移**（例如走路时身体下沉 -0.04m）。
    rest 姿态腿几乎伸直（踝到髋 426.7mm / 腿长 427.0mm），
    前后余量只有 ~29mm；不降髋就走不了路。
    """
    up = f"{side}UpperLeg"
    lo = f"{side}LowerLeg"
    ft = f"{side}Foot"

    hip = np.asarray(rig.rest[up], float)[0:3, 3] + np.asarray(hip_offset, float)
    l1 = float(np.linalg.norm(np.asarray(rig.rest[up], float)[0:3, 3]
                              - np.asarray(rig.rest[lo], float)[0:3, 3]))

    # 小腿长度 = lowerLeg head → foot head
    l2 = float(np.linalg.norm(np.asarray(rig.rest[lo], float)[0:3, 3]
                              - np.asarray(rig.rest[ft], float)[0:3, 3]))
    knee, ok, reason = solve_two_bone(hip, target, l1, l2, pole)
    if not ok:
        return None, reason

    # rest 朝向
    d_up = bone_dir(rig.rest[up])
    d_lo = bone_dir(rig.rest[lo])

    # 目标朝向
    t_up = (knee - hip)
    t_lo = (np.asarray(target, float) - knee)

    # 世界旋转
    R_up_w = rot_between(d_up, t_up)
    R_lo_w = rot_between(d_lo, t_lo)

    # 小腿相对大腿
    R_lo_rel = R_up_w.T @ R_lo_w

    # 转到骨骼局部：R_local = R_rest^T @ R_world @ R_rest
    r_up = np.asarray(rig.rest[up], float)[0:3, 0:3]
    r_lo = np.asarray(rig.rest[lo], float)[0:3, 0:3]

    def to_basis(Rw, r3):
        R = r3.T @ Rw @ r3
        m = np.eye(4)
        m[0:3, 0:3] = R
        return m

    # 局部系里 hips 没有旋转，所以「大腿的世界旋转」可以直接用世界量表示。
    # （朝向由装配层最后整体旋转，见模块 docstring）
    b_up = to_basis(R_up_w, r_up)
    b_lo = to_basis(R_lo_rel, r_lo)

    # 脚保持水平（世界朝向不变）
    r_ft = np.asarray(rig.rest[ft], float)[0:3, 0:3]
    R_ft_w = R_up_w @ R_lo_rel          # 脚跟着小腿的累积旋转
    b_ft = to_basis(R_ft_w.T, r_ft)     # 抵消掉，让脚回水平

    return {up: b_up, lo: b_lo, ft: b_ft}, "ok"
