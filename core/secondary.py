"""core/secondary.py — 二次运动：头发 / 裙摆 / 腰饰的惯性滞后（v1.9）。

**为什么要有这个**：
  209 骨里 21 根主骨，其余 188 根是二次骨
  （hair 70 含 bangs 10 / skirt 64 / ribbon 6 / tail 4 / 手指 40 / toe 4）。
  v1.8 只驱动 21 主骨 + 4 节尾巴，**头发和裙子是恒等矩阵**——
  她走路时它们跟着身体刚性平移，看起来像塑料模型。
  这里接管 hair + skirt + ribbon 共 **140 根**（尾巴归 `pose_gen.tail_curl`，
  手指/toe 暂不动）。

**模型**（不是物理仿真，是「够像」的弹簧）：

  每一帧取「父骨在世界里的旋转增量」，换算成角速度 ω，
  目标滞后角 θ* = -gain · ω（父骨转得越快，子骨落后越多），
  再用二阶弹簧-阻尼把 θ 拉向 θ*：

        a = k(θ* - θ) - c·v
        v += a·dt
        θ += v·dt
        θ  ← clamp(θ, ±max_deg)

  链条耦合：子骨的最终滞后还要叠上父骨滞后的一部分（`chain_coupling`），
  否则整根头发会像一块板一起歪，而不是从发根往发梢甩。

**边界**：
  - 只在 `BodyLoop` 里按帧调用（需要 dt）。`Runtime` 不碰它——
    否则测试里的离线姿态也会带惯性残留。
  - 纯 numpy，不 import bpy。
  - 可以整个关掉：`BodyLoop(..., secondary=None)`。
"""

from __future__ import annotations

import math

import numpy as np

from core.collision import region_of

#: 默认接管的分区（尾巴归 pose_gen 的 tail_curl，不在这里抢）
DEFAULT_GROUPS = ("hair", "skirt", "ribbon")


def rotvec(R: np.ndarray) -> np.ndarray:
    """3x3 旋转 → 旋转向量（axis·angle）。小角度稳定。"""
    tr = float(R[0, 0] + R[1, 1] + R[2, 2])
    c = max(-1.0, min(1.0, (tr - 1.0) * 0.5))
    ang = float(np.arccos(c))
    if ang < 1e-9:
        return np.zeros(3)
    if ang > np.pi - 1e-6:            # 接近 180°，用对称部分取轴
        ax = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
        ax = ax / (np.linalg.norm(ax) + 1e-12)
        return ax * ang
    ax = np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    return ax * (ang / (2.0 * np.sin(ang)))


def rodrigues(axis: np.ndarray, ang: float) -> np.ndarray:
    """旋转向量（axis 已归一）转 3x3。"""
    x, y, z = axis
    c, s = np.cos(ang), np.sin(ang)
    C = 1.0 - c
    return np.array([
        [c + x * x * C, x * y * C - z * s, x * z * C + y * s],
        [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
        [z * x * C - y * s, z * y * C + x * s, c + z * z * C],
    ])


class SecondaryMotion:
    """惯性滞后。有状态，每帧 `step(basis, dt)`。"""

    def __init__(self, rig, groups=DEFAULT_GROUPS, stiffness: float = 34.0,
                 damping: float = 10.0, gain: float = 0.45, max_deg: float = 12.0,
                 chain_coupling: float = 0.55, scale=None,
                 wind_gain: float = 15.0):
        self.rig = rig
        self.groups = tuple(groups)
        self.k = float(stiffness)
        self.c = float(damping)
        self.gain = float(gain)
        self.max_rad = float(np.radians(max_deg))
        self.coupling = float(chain_coupling)
        #: 走路时的「风」：发梢朝运动反方向拖多少个角度（度 / (m/s)）。
        #: 只靠旋转驱动的话，走路时头几乎不转 → 头发纹丝不动（实测 0.71°）。
        self.wind_gain = float(wind_gain)
        #: {分区: 幅度系数}，给调参用（裙摆通常比头发重）
        self.scale = dict(scale or {"hair": 1.0, "skirt": 0.75, "ribbon": 0.8})

        self.bones = self._collect()
        self._depth = {n: self._depth_of(n) for n in self.bones}
        self.bones.sort(key=lambda n: self._depth[n])
        self.reset()

    # ---- 建表 ----
    def _collect(self) -> list[str]:
        return [n for n in self.rig.names if region_of(n) in self.groups]

    def _depth_of(self, name: str) -> int:
        d, p, guard = 0, self.rig.parent.get(name), 0
        while p is not None and guard < 64:
            d += 1
            p = self.rig.parent.get(p)
            guard += 1
        return d

    def reset(self) -> None:
        """清空惯性状态（瞬移/切场景后调用）。"""
        self.lag = {n: np.zeros(3) for n in self.bones}
        self.vel = {n: np.zeros(3) for n in self.bones}
        self.applied = {n: np.zeros(3) for n in self.bones}
        self._prev_R: dict[str, np.ndarray] = {}
        self.frames = 0

    # ---- 一帧 ----
    def step(self, basis: dict, dt: float, state=None) -> dict:
        """在 `basis` 的副本上加惯性偏转，返回新 dict（不改入参）。

        `state` 给了就用它的 `vel` / `yaw` 算「风」：人在往前走，
        头发会被拖向反方向。没有 state 就只按旋转惯性驱动。
        """
        if not self.bones or dt is None or dt <= 0:
            return basis

        wind = np.zeros(3)
        if state is not None and self.wind_gain:
            vel = float(getattr(state, "vel", 0.0) or 0.0)
            if abs(vel) > 1e-6:
                yaw = math.radians(float(getattr(state, "yaw", 0.0) or 0.0))
                f = np.array([math.sin(yaw), -math.cos(yaw), 0.0])
                axis = np.array([-f[1], f[0], 0.0])      # up × f
                wind = axis * (math.radians(self.wind_gain) * vel)

        out = dict(basis)
        M = self.rig.compute(basis)

        # 先把父骨的世界旋转记下来（用于下一帧求增量）
        cur_R = {n: np.asarray(M[n])[0:3, 0:3].copy() for n in self.bones}
        parent_R = {}
        for n in self.bones:
            p = self.rig.parent.get(n)
            parent_R[n] = np.asarray(M[p])[0:3, 0:3].copy() if p in M else None

        first = not self._prev_R
        for n in self.bones:
            pR = parent_R[n]
            if pR is None:
                continue
            prev = self._prev_R.get(n)
            if prev is not None and not first:
                # 父骨这一帧转了多少（世界系）
                w = rotvec(pR @ prev.T) / dt
                sc = self.scale.get(region_of(n), 1.0)
                target = -self.gain * w * sc + wind * sc
                nrm = float(np.linalg.norm(target))
                if nrm > self.max_rad:
                    target *= self.max_rad / nrm

                a = self.k * (target - self.lag[n]) - self.c * self.vel[n]
                self.vel[n] = self.vel[n] + a * dt
                self.lag[n] = self.lag[n] + self.vel[n] * dt
                nrm = float(np.linalg.norm(self.lag[n]))
                if nrm > self.max_rad:                 # 保险，别甩飞
                    self.lag[n] *= self.max_rad / nrm
                    self.vel[n] *= 0.5
            self._prev_R[n] = pR

            # 链条耦合：子骨跟着父骨再甩一点
            p = self.rig.parent.get(n)
            parent_applied = self.applied.get(p, np.zeros(3))
            vec = self.lag[n] + self.coupling * parent_applied
            nrm = float(np.linalg.norm(vec))
            if nrm > self.max_rad:
                vec *= self.max_rad / nrm
            self.applied[n] = vec
            if nrm > 1e-9:
                out[n] = self._apply(out.get(n, np.eye(4)), n, vec)

        # 用这一帧的「装饰后」朝向作为下一帧的基准？不——基准要连续，
        # 用父骨（未受装饰影响的部分）就够了，所以 cur_R 只记录未使用。
        _ = cur_R
        self.frames += 1
        return out

    def _apply(self, basis_b, name: str, vec: np.ndarray) -> np.ndarray:
        ang = float(np.linalg.norm(vec))
        if ang < 1e-9:
            return basis_b
        R = rodrigues(vec / ang, ang)
        r3 = np.asarray(self.rig.rest[name])[0:3, 0:3]
        local = r3.T @ R @ r3
        m = np.array(basis_b, dtype=float, copy=True)
        m[0:3, 0:3] = m[0:3, 0:3] @ local
        return m

    def snapshot(self) -> dict:
        """调试用：当前最大滞后角（度）和骨头。"""
        if not self.bones:
            return {"n": 0, "max_deg": 0.0, "bone": None}
        worst = max(self.bones, key=lambda n: float(np.linalg.norm(self.applied[n])))
        return {"n": len(self.bones),
                "max_deg": round(float(np.degrees(np.linalg.norm(self.applied[worst]))), 2),
                "bone": worst, "frames": self.frames}
