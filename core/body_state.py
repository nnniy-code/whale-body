"""core/body_state.py — 角色在世界里的移动状态。

设计依据：DEVLOG 2026-11-25「D 的设计」

**职责分离**（这是 D 的核心）：
    动作（walk/run）  → 只写 vel / turn_rate
    BodyState.step()  → 积分出 x/y/yaw，算 bob
    姿态层（pose_gen + ik）→ **只读**，不写

为什么不让动作直接摆 hips：
    走路/跑步/转身/被推，改的都是「身体在世界里的位置」，
    而所有动作（挥/点头/歪头）都挂在这个位置上面。
    位置不属于任何一个动作，属于角色本身。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class BodyState:
    """角色的世界状态（只有移动相关的量）。"""

    x: float = 0.0            # 世界 x（米）
    y: float = 0.0            # 世界 y（米）
    yaw: float = 0.0          # 朝向（度，0 = 朝 -Y）
    bob: float = 0.0          # 上下浮动（米，重心起伏）
    vel: float = 0.0          # 前进速度（米/秒）
    turn_rate: float = 0.0    # 转向速度（度/秒）

    # 内部：累计走过的距离（算 bob 相位用）
    _dist: float = field(default=0.0, repr=False)

    # 步态参数（由动作设置，默认走路）
    stride: float = 0.5
    bob_amp: float = 0.012

    def step(self, dt: float) -> None:
        """按当前 vel / turn_rate 前进一个时间步。"""
        if dt <= 0:
            return
        # 朝向先转（这样这一帧的前进方向用的是新朝向）
        self.yaw = (self.yaw + self.turn_rate * dt) % 360.0
        # 前进
        v = float(self.vel)
        if abs(v) > 1e-9:
            rad = math.radians(self.yaw)
            # 朝向 0 = -Y，顺时针为正
            fx = math.sin(rad)
            fy = -math.cos(rad)
            self.x += fx * v * dt
            self.y += fy * v * dt
            self._dist += abs(v) * dt
            # 重心起伏：一个步态周期两次（左右脚各一次）
            phase = self._dist / max(1e-6, self.stride)
            self.bob = math.sin(2.0 * math.pi * phase) * self.bob_amp
        else:
            self.bob *= 0.85      # 停下时渐渐不浮

    def reset(self) -> None:
        self.x = self.y = self.yaw = 0.0
        self.bob = self.vel = self.turn_rate = 0.0
        self._dist = 0.0

    def offset(self) -> tuple[float, float, float]:
        """姿态层平移 hips 用的偏移（世界坐标）。"""
        return (self.x, self.y, self.bob)

    def forward(self) -> tuple[float, float]:
        rad = math.radians(self.yaw)
        return (math.sin(rad), -math.cos(rad))

    def as_dict(self) -> dict:
        return {"x": round(self.x, 4), "y": round(self.y, 4),
                "yaw": round(self.yaw, 2), "bob": round(self.bob, 4),
                "vel": round(self.vel, 3), "turn_rate": round(self.turn_rate, 2)}

    def __repr__(self) -> str:
        return (f"BodyState(x={self.x:.3f}, y={self.y:.3f}, yaw={self.yaw:.1f}, "
                f"bob={self.bob:.4f}, vel={self.vel:.2f})")
