"""core/proprio.py — 本体感知快照。

设计依据：docs/design/03-gate.md（Trajectory 的来源）、HANDOFF D3/D9

原则：
  - 只读 fk.py 的结果（numpy 4x4），不碰 bpy
  - 顶点簇是预计算常量（tests/fixtures/mesh_clusters.json）
  - 输出在 armature 空间；调用方若要世界坐标，乘 armature.matrix_world（≈单位）
"""

from __future__ import annotations

import numpy as np

ENDPOINTS = ("leftHand", "rightHand", "leftFoot", "rightFoot", "head")


def _apply(M: np.ndarray, P: np.ndarray) -> np.ndarray:
    """把 (N,3) 局部点用 4x4 变换到 armature 空间。"""
    R = M[0:3, 0:3]
    t = M[0:3, 3]
    return P @ R.T + t


def joints(M: dict) -> dict[str, np.ndarray]:
    return {n: m[0:3, 3].copy() for n, m in M.items()}


def endpoints(M: dict) -> dict[str, np.ndarray]:
    return {n: M[n][0:3, 3].copy() for n in ENDPOINTS if n in M}


MAIN21 = [
    "hips", "leftUpperLeg", "leftLowerLeg", "leftFoot",
    "rightUpperLeg", "rightLowerLeg", "rightFoot",
    "spine", "chest", "upperChest",
    "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand",
    "neck", "head",
    "rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand",
]


def centroid(M: dict, clusters: dict[str, list], only: set | None = None) -> np.ndarray:
    """顶点簇等权质心（近似真实 COM）。

    only：只统计这些骨。**默认不含头发/裙子**——它们是二次运动，
    不跟脊柱转，算进去会把质心稀释到几乎不动。
    """
    if only is None:
        only = set(MAIN21)
    num = np.zeros(3)
    den = 0
    for b, pts in clusters.items():
        if not pts or b not in M or b not in only:
            continue
        P = np.asarray(pts, dtype=np.float64)
        num += _apply(M[b], P).sum(axis=0)
        den += len(P)
    return num / den if den else np.zeros(3)


def sole_z(M: dict, sole_pts: dict[str, list]) -> dict[str, float]:
    """每只脚的最低点 z（armature 空间）。"""
    out = {}
    for b, pts in sole_pts.items():
        if not pts or b not in M:
            continue
        P = np.asarray(pts, dtype=np.float64)
        out[b] = float(_apply(M[b], P)[:, 2].min())
    return out


def build_skeleton(rest: dict, parent: dict, connected: dict, order: list):
    """便捷入口：返回 fk.Rig。"""
    from core.fk import Rig
    return Rig(rest, parent, connected, order)


def snapshot(M: dict, clusters: dict, sole_pts: dict) -> dict:
    sz = sole_z(M, sole_pts)
    return {
        "joints": joints(M),
        "endpoints": endpoints(M),
        "com": centroid(M, clusters),
        "sole_z": sz,
        "support_z": (min(sz.values()) if sz else None),
    }
