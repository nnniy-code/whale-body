"""core/bones.py — 骨骼元数据层：把 BONES.md 的五条约束变成代码。

约束来源：docs/BONES.md §2 / §6

  1. 不许硬编码骨名          → BoneMap.resolve / by_role
  2. 不许硬编码 unconnected  → BoneMap.is_connected（读 bones_map.json）
  3. 语义角度先转局部        → basis_from_world_rot
  4. 无权重骨的处理          → BoneMap.has_weights
  5. 前缀混用要归一化        → normalize / resolve
"""

from __future__ import annotations

import json
import os

import numpy as np

_PREFIXES = ("mixamorig:",)


def normalize(name: str) -> str:
    """去掉 `mixamorig:` 前缀（大小写不敏感）。"""
    low = name.lower()
    for p in _PREFIXES:
        if low.startswith(p):
            return name[len(p):]
    return name


def rotation_matrix(axis: str, deg: float) -> np.ndarray:
    """绕世界坐标轴的 3x3 旋转。"""
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if axis == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    if axis == "z":
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    raise ValueError(f"axis 必须是 x/y/z，收到 {axis!r}")


class BoneMap:
    """`config/bones_map.json` 的只读访问层。"""

    def __init__(self, data: dict):
        self.data = data
        self.bones: dict = data["bones"]
        self._norm: dict[str, str] = {}
        for n in self.bones:
            self._norm.setdefault(normalize(n), n)
        self._dups = [n for n in self._norm if sum(1 for x in self.bones if normalize(x) == n) > 1]

    @classmethod
    def load(cls, path: str | None = None) -> "BoneMap":
        if path is None:
            path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config", "bones_map.json",
            )
        with open(path, "r", encoding="utf-8") as f:
            return cls(json.load(f))

    # ---- 名字 ----
    def resolve(self, name: str) -> str:
        """把任意写法解析成 json 里的真实骨名。解析不到抛 KeyError。"""
        if name in self.bones:
            return name
        n = self._norm.get(normalize(name))
        if n is None:
            raise KeyError(f"未知骨骼：{name!r}（归一化后 {normalize(name)!r}）")
        return n

    def try_resolve(self, name: str) -> str | None:
        try:
            return self.resolve(name)
        except KeyError:
            return None

    def duplicates(self) -> list[str]:
        """归一化后重名的骨（当前应为空）。"""
        return list(self._dups)

    # ---- 属性 ----
    def chain(self, name: str) -> str:
        return self.bones[self.resolve(name)]["chain"]

    def parent(self, name: str) -> str | None:
        return self.bones[self.resolve(name)]["parent"]

    def is_connected(self, name: str) -> bool:
        return bool(self.bones[self.resolve(name)]["connected"])

    def has_weights(self, name: str) -> bool:
        return self.bones[self.resolve(name)]["vg_verts"] > 0

    def axes(self, name: str) -> dict:
        return self.bones[self.resolve(name)]["axes"]

    def names(self, chain: str | None = None) -> list[str]:
        if chain is None:
            return list(self.bones)
        return [n for n, v in self.bones.items() if v["chain"] == chain]

    def roots(self) -> list[str]:
        return [n for n, v in self.bones.items() if v["parent"] is None]

    # ---- 几何（需要 Rig 提供 rest 矩阵）----
    def basis_from_world_rot(self, rig, name: str, axis: str, deg: float) -> np.ndarray:
        """把「绕世界 axis 转 deg 度」写成该骨的 basis（4x4）。

        这是约束 3 的唯一入口。四肢骨和躯干骨局部轴不同，所以不能套公式。
        """
        b = self.resolve(name)
        r3 = np.asarray(rig.rest[b], dtype=np.float64)[0:3, 0:3]
        r3_inv = np.linalg.inv(r3)
        r_local = r3_inv @ rotation_matrix(axis, deg) @ r3
        m = np.eye(4)
        m[0:3, 0:3] = r_local
        return m

    def basis_from_world_loc(self, rig, name: str, delta_m) -> np.ndarray:
        """把「世界空间位移 delta_m」写成该骨的 basis。

        只对 unconnected 骨有效——connected 骨的 location 会被 Blender 忽略
        （本实现里由 Rig.compute 清掉）。
        """
        b = self.resolve(name)
        r3 = np.asarray(rig.rest[b], dtype=np.float64)[0:3, 0:3]
        r3_inv = np.linalg.inv(r3)
        m = np.eye(4)
        m[0:3, 3] = r3_inv @ np.asarray(delta_m, dtype=np.float64)
        return m
