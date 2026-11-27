"""core/foot_target.py — 脚目标源：把「脚在哪」和「腿怎么驱动」解耦。

设计依据：DEVLOG 2026-11-25「腿的三种驱动」

**三种驱动**（由族指定）：
  ① GaitTarget  周期步态（走/跑）    —— 公式生成
  ② StaticTarget 脚不动（蹲/站）      —— 固定点
  ③ PointTarget  指定点（踩台阶）    —— 直接给坐标

**为什么抽象**：
  之前 `build_walk_state` 只调 `gait.foot_targets`，所以「腿只能走」。
  抽象后，同一套装配流程（`build_full_body`）能跑任意脚目标源。

**坐标约定**（v1.9 统一）：
  `positions()` 返回**身体局部系**的脚底坐标（身体朝 -Y、身体原点在前一帧的 (0,0)）。
  **不含**世界平移/旋转——那些由 `assembly.build_full_body` 在装配最后一次性完成。
  上层负责加 `ANKLE_HEIGHT`（脚底 → 踝）。

  为什么改成局部系：身体转身时，若脚钉在世界里，髋到脚的距离会超过腿长
  （实测转 90° 变 441mm > 427mm，IK 直接失败）。脚随身体转是滑，但腿是健康的；
  而「支撑脚当轴、转身不滑」是 HANDOFF §22.6 第 3 项，还没做。
"""

from __future__ import annotations

import numpy as np

from core import gait

ANKLE_HEIGHT = 0.080      # rest 踝 z（实测）


class FootTarget:
    """脚目标源基类。"""

    name = "base"

    def positions(self, state, t: float) -> dict[str, list[float]]:
        """{side: [x, y, z]}，**身体局部系**的脚底坐标。"""
        raise NotImplementedError

    def hip_drop(self) -> float:
        """髋要降多少（米）。stride 大时必须降，否则腿够不到。"""
        return 0.0

    def speed(self) -> float:
        """这个目标源隐含的身体速度（m/s）。0 = 不定义（站桩/指定点）。"""
        return 0.0

    def pole(self, side: str):
        """膝盖朝向提示（世界方向）。None = 默认前方。"""
        return None

    def describe(self) -> str:
        return f"{self.name}(hip_drop={self.hip_drop():.3f})"


class GaitTarget(FootTarget):
    """周期步态：走 / 跑 / 后退。

    内部还是 `gait.foot_targets`，只是把 `travel` 关掉
    （前进由 BodyState 负责）。
    """

    name = "gait"

    def __init__(self, G: dict, start: dict[str, list[float]],
                 forward=(0.0, -1.0, 0.0), hip_drop: float | None = None):
        self.G = dict(G)
        self.start = {k: list(v) for k, v in start.items()}
        self.forward = forward
        self._origin = [0.0, float(start["left"][1]), 0.0]
        if hip_drop is not None:
            self._hip_drop = float(hip_drop)
        else:
            # stride 越大，髋要降得越多（否则腿够不到）。
            # 抬脚高度也算进去：抬得高时腿要伸得更远。
            # 实测 stride=0.5 + lift=0.06 时，只按 stride 估算会差 7.8mm（434.8 > 427）。
            stride = float(self.G.get("stride", 0.0))
            lift = float(self.G.get("foot_lift", 0.0))
            self._hip_drop = max(0.0, stride * 0.16 + lift * 0.5)

    def positions(self, state, t: float) -> dict[str, list[float]]:
        # 朝向统一由 BodyState.yaw 负责，这里**必须**把 G.turn_rate 清零，
        # 否则脚会自己转一遍、身体再转一遍（双重旋转）。
        G = self.G
        if abs(float(G.get("turn_rate", 0.0))) > 1e-9:
            G = {**G, "turn_rate": 0.0}
        ft = gait.foot_targets(G, t, start=self.start,
                               body_origin=self._origin, forward=self.forward,
                               travel=False)
        # 局部系：不加身体位移（world 由 assembly 统一搬）
        return {k: [float(v[0]), float(v[1]), float(v[2])] for k, v in ft.items()}

    def hip_drop(self) -> float:
        return self._hip_drop

    def speed(self) -> float:
        return gait.body_speed(self.G)

    def stance_side(self, t: float, prefer: str | None = None):
        """t 时刻哪只脚在支撑相。双脚都在（duty>0.5）时保持 prefer。"""
        period = max(float(self.G.get("period", 1.0)), 1e-3)
        duty = min(max(float(self.G.get("duty", 0.5)), 0.05), 1.0)
        off = float(self.G.get("phase_offset", 0.5))
        ph = (t / period) % 1.0
        cand = [s for s, o in (("left", 0.0), ("right", off))
                if ((ph + o) % 1.0) < duty]
        if not cand:
            return None
        if prefer in cand:
            return prefer
        return cand[0]


class StaticTarget(FootTarget):
    """脚不动：站立 / 蹲下 / 单脚站。

    `lift` 可以让某只脚抬高（单脚站/抬腿）。
    """

    name = "static"

    def __init__(self, feet: dict[str, list[float]], hip_drop: float = 0.0,
                 lift: dict[str, float] | None = None):
        self.feet = {k: list(v) for k, v in feet.items()}
        self._hip_drop = float(hip_drop)
        self.lift = dict(lift or {})

    def positions(self, state, t: float) -> dict[str, list[float]]:
        # 局部系：不加身体位移
        out = {}
        for side, p in self.feet.items():
            dz = float(self.lift.get(side, 0.0))
            out[side] = [float(p[0]), float(p[1]), float(p[2]) + dz]
        return out

    def hip_drop(self) -> float:
        return self._hip_drop


class PointTarget(FootTarget):
    """脚踩到指定点（台阶/指定位置）。

    `points` 是相对身体的偏移（米），不是绝对世界坐标。
    """

    name = "point"

    def __init__(self, points: dict[str, list[float]], hip_drop: float = 0.0,
                 pole_map: dict[str, tuple] | None = None):
        self.points = {k: list(v) for k, v in points.items()}
        self._hip_drop = float(hip_drop)
        self.pole_map = dict(pole_map or {})

    def positions(self, state, t: float) -> dict[str, list[float]]:
        # 本来就是「相对身体的偏移」（见类 docstring），这里不再加世界平移
        return {k: [float(v[0]), float(v[1]), float(v[2])]
                for k, v in self.points.items()}

    def hip_drop(self) -> float:
        return self._hip_drop

    def pole(self, side: str):
        return self.pole_map.get(side)


def attach_legs(basis: dict, targets: dict[str, list[float]],
                hip_off: tuple[float, float, float], rig, bm,
                foot_target: FootTarget | None = None) -> tuple[dict, dict]:
    """把腿 IK 接到 basis 上。

    `targets` 是**脚底**坐标；内部加 ANKLE_HEIGHT 转成踝。
    """
    from core import ik

    diag = {"foot_targets": {}, "foot_actual": {}, "err_mm": {}, "ik_fail": {}}
    for side in ("left", "right"):
        if side not in targets:
            continue
        tgt = np.array(targets[side], dtype=float)
        tgt[2] += ANKLE_HEIGHT
        pole = foot_target.pole(side) if foot_target else None
        lb, why = ik.leg_basis(rig, bm, side, tgt, pole=pole, hip_offset=hip_off)
        if lb is None:
            diag["ik_fail"][side] = str(why)
        else:
            basis.update(lb)
        diag["foot_targets"][side] = [round(float(x), 4) for x in tgt]

    M = rig.compute(basis)
    for side in ("left", "right"):
        if side not in targets:
            continue
        actual = M[f"{side}Foot"][0:3, 3]
        tgt = np.array(targets[side], dtype=float)
        tgt[2] += ANKLE_HEIGHT
        diag["foot_actual"][side] = [round(float(x), 4) for x in actual]
        diag["err_mm"][side] = round(float(np.linalg.norm(actual - tgt)) * 1000, 1)
    return basis, diag

