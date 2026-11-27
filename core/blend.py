"""core/blend.py — 动作混合：两个姿态之间的 200ms 惯性化过渡。

设计依据：HANDOFF §16 待定项 #3、docs/design/08-state-machine.md

**为什么必须用四元数**：
  4x4 矩阵逐元素线性插值会让旋转部分失去正交性 → 骨骼视觉上「缩短」。
  正确做法：提取旋转四元数做 slerp，平移单独 lerp。

**为什么不是线性 alpha**：
  线性会让动作「一启动就猛冲」。用 smoothstep（3t²-2t³），起止都缓。

不做的事：
  - 不插值缩放（骨骼没有缩放）
  - 不插值尾巴/手指等二次骨（它们跟随主骨，单独插值会抖）
"""

from __future__ import annotations

import numpy as np

# 默认过渡时长（毫秒）
DEFAULT_BLEND_MS = 200.0

# 参与混合的骨（主骨）。二次骨（头发/裙子/手指）跟随，不单独插值。
BLEND_BONES = [
    "hips", "spine", "chest", "upperChest", "neck", "head",
    "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand",
    "rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand",
    "leftUpperLeg", "leftLowerLeg", "leftFoot",
    "rightUpperLeg", "rightLowerLeg", "rightFoot",
    "WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04",
]


def ease(t: float) -> float:
    """smoothstep：起止都缓，中间快。t 会被 clamp 到 [0,1]。"""
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def mat_to_quat(R: np.ndarray) -> np.ndarray:
    """3x3 旋转矩阵 → 四元数 (w, x, y, z)。Shepperd 方法，数值稳定。"""
    m = R
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0.0:
        s = np.sqrt(tr + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2, 1] - m[1, 2]) / s
        y = (m[0, 2] - m[2, 0]) / s
        z = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s
    q = np.array([w, x, y, z], dtype=float)
    return q / (np.linalg.norm(q) + 1e-12)


def quat_to_mat(q: np.ndarray) -> np.ndarray:
    w, x, y, z = q / (np.linalg.norm(q) + 1e-12)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ], dtype=float)


def slerp(qa: np.ndarray, qb: np.ndarray, t: float) -> np.ndarray:
    """球面线性插值。t=0 → qa，t=1 → qb。"""
    qa = qa / (np.linalg.norm(qa) + 1e-12)
    qb = qb / (np.linalg.norm(qb) + 1e-12)
    d = float(np.dot(qa, qb))
    if d < 0.0:                      # 取最短路径
        qb, d = -qb, -d
    d = max(-1.0, min(1.0, d))
    if d > 0.9995:                   # 太接近，退化成 lerp（避免除零）
        q = qa + t * (qb - qa)
        return q / (np.linalg.norm(q) + 1e-12)
    th = np.arccos(d)
    s = np.sin(th)
    return (np.sin((1 - t) * th) / s) * qa + (np.sin(t * th) / s) * qb


def blend_mat(a: np.ndarray, b: np.ndarray, t: float) -> np.ndarray:
    """两个 4x4 basis 之间插值：旋转 slerp，平移 lerp。"""
    out = np.eye(4)
    out[0:3, 0:3] = quat_to_mat(slerp(mat_to_quat(a[0:3, 0:3]),
                                       mat_to_quat(b[0:3, 0:3]), t))
    out[0:3, 3] = (1.0 - t) * a[0:3, 3] + t * b[0:3, 3]
    return out


def blend_basis(a: dict, b: dict, alpha: float,
                bones: list[str] | None = None) -> dict:
    """两套 basis 之间按 alpha 混合（alpha 已经过 ease）。"""
    names = bones or BLEND_BONES
    out = {k: np.array(v, dtype=float, copy=True) for k, v in a.items()}
    for bn in names:
        if bn in a and bn in b:
            out[bn] = blend_mat(a[bn], b[bn], alpha)
    return out


def progress(elapsed_ms: float, duration_ms: float = DEFAULT_BLEND_MS) -> float:
    """已过时间 → ease 后的 alpha。"""
    if duration_ms <= 0:
        return 1.0
    return ease(elapsed_ms / duration_ms)
