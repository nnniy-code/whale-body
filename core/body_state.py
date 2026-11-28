"""core/body_state.py — 角色在世界里的移动状态 + 姿态保持（v2.1）。

设计依据：DEVLOG 2026-11-25/27「D 的设计」「姿态保持」

**职责分离**：
    动作（walk/run）  → 只写 vel / turn_rate
    BodyState.step()  → 积分出 x/y/yaw，算 bob
    姿态层（pose_gen + ik）→ **只读**，不写

**姿态保持（v2.1）**：
    用户要求「默认保持当前姿势，说『回待机』才重置」。
    注意：腿的状态（hip_drop / foot_source）也在 State 里，
    因为 P 只描述上半身，蹲下的 hip_drop 不在 P 里。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class BodyState:
    """角色的世界状态（移动 + 姿态保持）。"""

    # ---- 移动 ----
    x: float = 0.0            # 世界 x（米）
    y: float = 0.0            # 世界 y（米）
    yaw: float = 0.0          # 朝向（度，0 = 朝 -Y）
    bob: float = 0.0          # 上下浮动（米，重心起伏）
    vel: float = 0.0          # 前进速度（米/秒）
    turn_rate: float = 0.0    # 转向速度（度/秒）

    # 内部：累计走过的距离（算 bob 相位用）
    _dist: float = field(default=0.0, repr=False)

    # ---- 姿态保持（v2.1）----
    P_hold: dict = field(default_factory=dict)   # 上半身 12 维
    hip_drop_hold: float = 0.0                    # 腿：髋降
    lift_hold: dict = field(default_factory=dict)  # 腿：单脚抬高（side → 米）
    foot_source_hold: str = "static"              # 腿：脚目标源
    side_hold: str | None = None                  # 上半身：只驱动哪一侧（v2.4）

    # 步态参数（由动作设置，默认走路）
    stride: float = 0.5
    bob_amp: float = 0.012

    def step(self, dt: float) -> None:
        """按当前 vel / turn_rate 前进一个时间步。"""
        if dt <= 0:
            return
        self.yaw = (self.yaw + self.turn_rate * dt) % 360.0
        v = float(self.vel)
        if abs(v) > 1e-9:
            rad = math.radians(self.yaw)
            fx = math.sin(rad)
            fy = -math.cos(rad)
            self.x += fx * v * dt
            self.y += fy * v * dt
            self._dist += abs(v) * dt
            phase = self._dist / max(1e-6, self.stride)
            self.bob = math.sin(2.0 * math.pi * phase) * self.bob_amp
        else:
            self.bob *= 0.85      # 停下时渐渐不浮

    # ---- 重置 ----
    def reset(self) -> None:
        """全重置（位置 + 姿态）。"""
        self.x = self.y = self.yaw = 0.0
        self.bob = self.vel = self.turn_rate = 0.0
        self._dist = 0.0
        self.reset_pose()

    def reset_pose(self) -> None:
        """只重置姿态（**保留位置**）。

        用户要求：回待机不瞬移回原点。
        """
        self.P_hold = {}
        self.hip_drop_hold = 0.0
        self.lift_hold = {}
        self.foot_source_hold = "static"
        self.side_hold = None

    # ---- 姿态保持 ----
    def hold_pose(self, P: dict, hip_drop: float = 0.0,
                  lift: dict | str | None = None,
                  foot_source: str = "static",
                  side: str | None = None) -> None:
        """记下要保持的姿态（含腿的 hip_drop / lift）。

        **兼容 v2.1 的旧调用**：第三个参数以前是 `foot_source`（字符串）。
        现在改成 `lift`（字典）。传字符串时按旧的 `foot_source` 处理。
        """
        if isinstance(lift, str):
            foot_source = lift
            lift = None
        self.P_hold = dict(P or {})
        self.hip_drop_hold = float(hip_drop)
        self.lift_hold = dict(lift or {})
        self.foot_source_hold = str(foot_source)
        self.side_hold = side

    def has_pose(self) -> bool:
        return bool(self.P_hold)

    def pose_dict(self) -> dict:
        return {"P_hold": len(self.P_hold),
                "hip_drop_hold": round(self.hip_drop_hold, 3),
                "foot_source_hold": self.foot_source_hold,
                "side_hold": self.side_hold}

    # ---- 工具 ----
    def offset(self) -> tuple[float, float, float]:
        """姿态层平移 hips 用的偏移（世界坐标）。"""
        return (self.x, self.y, self.bob)

    def forward(self) -> tuple[float, float]:
        rad = math.radians(self.yaw)
        return (math.sin(rad), -math.cos(rad))

    def as_dict(self) -> dict:
        return {"x": round(self.x, 4), "y": round(self.y, 4),
                "yaw": round(self.yaw, 2), "bob": round(self.bob, 4),
                "vel": round(self.vel, 3), "turn_rate": round(self.turn_rate, 2),
                **self.pose_dict()}

    def __repr__(self) -> str:
        return (f"BodyState(x={self.x:.3f}, y={self.y:.3f}, yaw={self.yaw:.1f}, "
                f"bob={self.bob:.4f}, vel={self.vel:.2f}, "
                f"pose={'有' if self.has_pose() else '无'})")

