"""core/binder.py — 唯一碰骨骼的文件（D7）。

设计依据：docs/design/05-dispatcher.md、HANDOFF §8.5.1

职责边界：
  - 只做「4x4 姿态矩阵 → pose_bone.matrix_basis」的转换与写入
  - 不做插值、不做求解、不做时间推进（那是 trajectory/solver/player 的事）
  - 不保存 blend

为什么不用 pb.matrix = M：
  那个 setter 依赖「父级已先写入」的顺序，且行为随版本变化。
  这里用纯矩阵反算 basis，不依赖 bpy 的内部求值顺序：
      basis[n] = inv(M[parent] @ prelocal[n]) @ M[n]
  对根骨：  basis[n] = inv(prelocal[n]) @ M[n]
"""

from __future__ import annotations

import numpy as np


def to_basis(M: dict, rig, order: list[str] | None = None) -> dict[str, np.ndarray]:
    """把 armature 空间的 pose 矩阵反算成每根骨的 matrix_basis。"""
    names = order or rig.names
    out: dict[str, np.ndarray] = {}
    for n in names:
        if n not in M:
            continue
        p = rig.parent.get(n)
        if p is None:
            out[n] = np.linalg.inv(rig.prelocal[n]) @ M[n]
        else:
            out[n] = np.linalg.inv(M[p] @ rig.prelocal[n]) @ M[n]
    return out


def apply_basis(arm, basis: dict, only: set[str] | None = None) -> int:
    """把 basis 写进 arm.pose.bones。返回写入的骨数。"""
    n = 0
    for name, b in basis.items():
        if only is not None and name not in only:
            continue
        pb = arm.pose.bones.get(name)
        if pb is None:
            continue
        pb.matrix_basis = _to_matrix(b)
        n += 1
    return n


def read_basis(arm) -> dict[str, np.ndarray]:
    """读回当前所有 pose bone 的 matrix_basis（numpy）。"""
    out = {}
    for pb in arm.pose.bones:
        m = pb.matrix_basis
        out[pb.name] = np.array([[m[r][c] for c in range(4)] for r in range(4)])
    return out


def apply_pose(M: dict, rig, arm, force_update: bool = True) -> dict:
    """一步到位：pose 矩阵 → basis → 写骨 →（可选）update。

    返回统计，便于日志。
    """
    basis = to_basis(M, rig)
    n = apply_basis(arm, basis)
    if force_update:
        import bpy
        bpy.context.view_layer.update()
    return {"written": n, "needs_update": force_update}


def verify_roundtrip(arm, rig, M: dict) -> float:
    """闭环校验：写进去再读回来，FK 重算，返回最大位置误差（mm）。

    这是 P5 最重要的一条自检——写骨和 FK 必须是同一套数学。
    """
    apply_basis(arm, to_basis(M, rig))
    import bpy
    bpy.context.view_layer.update()
    basis = read_basis(arm)
    M2 = rig.compute(basis)
    worst = 0.0
    for n in rig.names:
        if n in M and n in M2:
            d = np.linalg.norm(M2[n][0:3, 3] - M[n][0:3, 3]) * 1000.0
            worst = max(worst, float(d))
    return worst


def _to_matrix(a) -> "object":
    """numpy 4x4 → mathutils.Matrix。延迟导入，保持模块可单测。"""
    from mathutils import Matrix
    a = np.asarray(a, dtype=np.float64)
    return Matrix([list(map(float, row)) for row in a])
