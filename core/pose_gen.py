"""core/pose_gen.py — P（姿态参数）→ 骨骼 basis。

设计依据：HANDOFF §4.5

这是「generator(P)」的最小实现：把 10 个语义参数翻译成 21 主骨的旋转。
G（步态）还没实现，所以这里只处理上半身 + 尾巴。

所有角度都是**语义角度（度）**，经 BoneMap.basis_from_world_rot 转到局部。
"""

from __future__ import annotations

import math

import numpy as np

I4 = np.eye(4)


def combine(*mats: np.ndarray) -> np.ndarray:
    out = np.eye(4)
    for m in mats:
        out = out @ m
    return out


def build(P: dict, rig, bm, order: list[str] | None = None,
          active_side: str | None = None,
          phase: float | None = None) -> dict[str, np.ndarray]:
    """返回 {bone: 4x4 basis}，未涉及的骨是单位矩阵。

    P 的键：lean / side_bend / head_nod / head_tilt / head_yaw /
            arm_swing / arm_lift / elbow_bend / hand_bias / tail_curl
    active_side：只驱动这一侧手臂（None = 两侧都驱动）。挥手/指这类动作需要。
    phase：步态相位 0..1（v1.9）。给了就把 `arm_swing` 变成**摆动**：

        swing_side = ∓ arm_swing · cos(2π·phase)

        左腿在前（phase=0）时左臂在后、右臂在前 —— 人走路就是这个反相。
        不给 phase 就还是静态角度（站桩/挥手等非周期动作）。
    """
    names = order or rig.names
    basis = {n: np.eye(4) for n in names}

    def g(k, default=0.0):
        return float(P.get(k, default))

    lean = g("lean")
    side = g("side_bend")

    # 躯干：lean 分配 60/40 到 spine/chest，side_bend 只作用 spine
    if "spine" in basis:
        basis["spine"] = combine(
            bm.basis_from_world_rot(rig, "spine", "x", lean * 0.6),
            bm.basis_from_world_rot(rig, "spine", "y", side),
        )
    if "chest" in basis:
        basis["chest"] = bm.basis_from_world_rot(rig, "chest", "x", lean * 0.4)

    # 头：三轴组合
    if "head" in basis:
        basis["head"] = combine(
            bm.basis_from_world_rot(rig, "head", "x", g("head_nod")),
            bm.basis_from_world_rot(rig, "head", "y", g("head_tilt")),
            bm.basis_from_world_rot(rig, "head", "z", g("head_yaw")),
        )

    # 手臂：lift 绕世界 Y（侧平举/上举），swing 绕世界 X（前后摆），
    # roll 绕世界 Z（上臂扭转——控制「肘尖朝哪」，这是 v2 新加的维度）
    lift = g("arm_lift")
    swing = g("arm_swing")
    roll = g("arm_roll")
    elbow = g("elbow_bend")
    wrist = g("wrist_bend")
    hand = g("hand_bias")
    for side_name, s_lift in (("left", -1.0), ("right", 1.0)):
        if active_side is not None and side_name != active_side:
            continue
        ua = f"{side_name}UpperArm"
        la = f"{side_name}LowerArm"
        hd = f"{side_name}Hand"
        # 步态相位：左右反相摆动（左腿在前 → 左臂在后）
        swing_side = swing
        if phase is not None:
            sgn = -1.0 if side_name == "left" else 1.0
            swing_side = swing * sgn * math.cos(2.0 * math.pi * float(phase))
        if ua in basis:
            basis[ua] = combine(
                # lift：左臂绕世界 Y 负转 → 指向 +X（向左侧平举）
                bm.basis_from_world_rot(rig, ua, "y", lift * s_lift),
                # swing：前后摆，左右同号
                bm.basis_from_world_rot(rig, ua, "x", swing_side),
                # roll：上臂绕自身轴扭转。rest 时手臂沿 -Z，绕世界 Z 转不改变
                # 位置但会改肘的朝向 —— 这正是「肘朝天」缺的那一维
                bm.basis_from_world_rot(rig, ua, "z", roll * s_lift),
            )
        if la in basis:
            # 肘弯曲：绕**世界 X**（前后轴）转，不是 Y。
            # 绕 Y 转时 elbow 越大手越往身体中线收（实测 90° 时 x 从 -0.21 收到 -0.11），
            # 看起来就是「肘朝里」。绕 X 转前臂才会往上/往前折，手留在体侧。
            # **左右同号**：前后方向的弯曲对左右是镜像对称的，不需要 s_lift。
            # 加了 s_lift 会让左手往后折（实测 y 差 180mm）。
            basis[la] = bm.basis_from_world_rot(rig, la, "x", -elbow)
        if hd in basis:
            basis[hd] = combine(
                bm.basis_from_world_rot(rig, hd, "y", hand),
                bm.basis_from_world_rot(rig, hd, "x", wrist * s_lift),
            )

    # 尾巴：沿链均匀分配 tail_curl
    curl = g("tail_curl")
    tail_bones = [n for n in names if n.lower().startswith("whaletial") or "WhaleTail" in n]
    for i, tb in enumerate(sorted(tail_bones)):
        basis[tb] = bm.basis_from_world_rot(rig, tb, "z", curl / max(1, len(tail_bones)))

    return basis
