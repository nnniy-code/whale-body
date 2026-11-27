"""core/idle.py — L0 底噪：呼吸 + 头部视线跟随（v1.9）。

**为什么要有这个**：
  `runtime.py` 里写着「视线跟随本来就该由 L0 底噪自动处理」，
  但 L0 **根本不存在**。于是 `gaze_toward` 被从 LLM 能力表里藏起来，
  理由就是「L0 会管」——结果两头都不管，视线跟随 = 完全没有。

  同时她静止时是**完全静止**的：不呼吸、不微动，像个被钉住的手办。

**边界**：
  - 只加微小、连续的量（呼吸 ≤1°，视线 ≤25°），不改变求解出来的姿态语义
  - 不进 `Runtime`，只在 `BodyLoop` 里按帧叠加（有状态：呼吸相位）
  - 纯 numpy。角度换算复用 `bones.basis_from_world_rot`
"""

from __future__ import annotations

import math

import numpy as np

from core.ik import rot_between  # noqa: F401  (保留给外部复用)
from core.secondary import rodrigues as _rodrigues


class IdleMotion:
    """一直存在的细微动作：呼吸 +（可选）看某个点。"""

    def __init__(self, rig, bm, breath_deg: float = 0.9, breath_period: float = 4.2,
                 max_yaw_deg: float = 25.0, max_pitch_deg: float = 18.0,
                 breath: bool = True):
        self.rig = rig
        self.bm = bm
        self.breath_deg = float(breath_deg)
        self.period = max(0.5, float(breath_period))
        self.max_ang = math.radians(min(float(max_yaw_deg), float(max_pitch_deg)))
        self.breath_on = bool(breath)
        self.t = 0.0
        self.target = None            # 世界坐标 (x,y,z) 或 None
        # 头骨的「脸朝前」在 head 局部系里的方向（rest 时世界 -Y）
        r3 = np.asarray(rig.rest["head"])[0:3, 0:3]
        self.local_fwd = r3.T @ np.array([0.0, -1.0, 0.0])

    # ---- 设置 ----
    def set_look(self, target) -> None:
        """让她看向某个世界坐标；None = 取消。"""
        self.target = None if target is None else np.asarray(target, dtype=float)

    def reset(self) -> None:
        self.t = 0.0

    # ---- 一帧 ----
    def step(self, basis: dict, dt: float) -> dict:
        if dt is None or dt <= 0:
            return basis
        self.t += dt
        out = dict(basis)

        if self.breath_on and self.breath_deg > 0:
            s = math.sin(2.0 * math.pi * self.t / self.period)
            # 注意：basis_from_world_rot 收的是**角度**，不是弧度
            a = self.breath_deg * s
            out["chest"] = self._mul(out.get("chest"),
                                     self.bm.basis_from_world_rot(self.rig, "chest", "x", a))
            out["spine"] = self._mul(out.get("spine"),
                                     self.bm.basis_from_world_rot(self.rig, "spine", "x", a * 0.45))

        if self.target is not None and "head" in out:
            out["head"] = self._look(out)
        return out

    # ---- 视线 ----
    def _look(self, basis: dict) -> np.ndarray:
        M = self.rig.compute(basis)
        head = np.asarray(M["head"])
        pos = head[0:3, 3]
        fwd = head[0:3, 0:3] @ self.local_fwd
        want = self.target - pos
        n = float(np.linalg.norm(want))
        if n < 1e-6:
            return np.asarray(basis["head"], dtype=float)
        want = want / n

        cross = np.cross(fwd, want)
        s, c = float(np.linalg.norm(cross)), float(np.dot(fwd, want))
        ang = math.atan2(s, c)
        if ang < 1e-6:
            return np.asarray(basis["head"], dtype=float)
        ang = min(ang, self.max_ang)              # 超出颈部范围只转这么多

        if s > 1e-9:
            axis = cross / s
        else:                                     # 正好背对：随便挑个垂直轴
            axis = np.cross(fwd, np.array([0.0, 0.0, 1.0]))
            axis = axis / (np.linalg.norm(axis) + 1e-12)

        R = _rodrigues(axis, ang)
        r3 = np.asarray(self.rig.rest["head"])[0:3, 0:3]
        m = np.array(basis["head"], dtype=float, copy=True)
        m[0:3, 0:3] = m[0:3, 0:3] @ (r3.T @ R @ r3)
        return m

    @staticmethod
    def _mul(a, b) -> np.ndarray:
        base = np.eye(4) if a is None else np.asarray(a, dtype=float)
        return base @ np.asarray(b, dtype=float)

    def snapshot(self) -> dict:
        return {"t": round(self.t, 3),
                "breath_deg": round(self.breath_deg, 3),
                "breath_on": self.breath_on,
                "look": None if self.target is None else [round(float(x), 3) for x in self.target]}
