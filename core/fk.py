"""core/fk.py — 纯 numpy 正向运动学。

设计依据：docs/design/12-fk-golden.md、HANDOFF D9

为什么要自己写：
  Blender 的 view_layer.update() 有 1.3–1.7 ms 固定地板（与骨数无关），
  而这里 21 主骨一次递推只要 0.018 ms。L2 求解 10000 次全靠它。

规则（必须逐字复刻 Blender）：
  M[b] = M[parent] @ (rest[parent]⁻¹ @ rest[b]) @ basis[b]
  若 b.use_connect：basis[b] 的 translation 必须清零（Blender 忽略它）

注意：本文件不 import bpy / mathutils。所有输入输出都是 numpy (4,4) float64。
"""

from __future__ import annotations

import numpy as np


class Rig:
    """一次构建，多次求值。prelocal 预计算后每帧只做 2 次矩阵乘。"""

    def __init__(self, rest: dict, parent: dict, connected: dict, order: list):
        self.names = list(order)
        self.parent = dict(parent)
        self.connected = {n: bool(connected.get(n, False)) for n in self.names}
        self.rest = {n: np.asarray(rest[n], dtype=np.float64) for n in self.names}
        self.prelocal: dict[str, np.ndarray] = {}
        for n in self.names:
            p = self.parent.get(n)
            if p is None:
                self.prelocal[n] = self.rest[n]
            else:
                self.prelocal[n] = np.linalg.inv(self.rest[p]) @ self.rest[n]

    def compute(self, basis: dict) -> dict[str, np.ndarray]:
        """返回 {bone: 4x4}（armature 空间）。basis 缺省视为单位矩阵。"""
        out: dict[str, np.ndarray] = {}
        for n in self.names:
            b = np.asarray(basis[n], dtype=np.float64) if n in basis else np.eye(4)
            if self.connected[n]:
                b = b.copy()
                b[0:3, 3] = 0.0          # Blender 对 connected 骨忽略 location
            p = self.parent.get(n)
            out[n] = (out[p] @ self.prelocal[n] @ b) if p is not None else (self.prelocal[n] @ b)
        return out

    def subset(self, names: list[str]) -> "Rig":
        """裁出子骨架（求解循环只要 21 主骨）。

        要求 names 对 parent 闭合（21 主骨满足：hips 是根，其余祖先都在集合内）。
        """
        missing = [n for n in names if self.parent.get(n) is not None and self.parent[n] not in names]
        if missing:
            raise ValueError(f"子集对 parent 不闭合：{missing[:5]}")
        rig = Rig.__new__(Rig)
        rig.names = list(names)
        rig.parent = {n: self.parent[n] for n in names}
        rig.connected = {n: self.connected[n] for n in names}
        rig.rest = {n: self.rest[n] for n in names}
        rig.prelocal = {n: self.prelocal[n] for n in names}
        return rig
