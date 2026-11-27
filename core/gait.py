"""core/gait.py — G（步态参数）→ 双脚的世界目标位置。

设计依据：HANDOFF §4.5、docs/design/01-bounds.md

核心规则（gait-guide）：
  - 支撑相：脚锁在固定世界坐标（不滑）
  - 摆动相：五次曲线从上一落脚点走到下一落脚点，峰值抬脚 = foot_lift
  - duty < 0.5 时自动出现腾空相（双脚离地）
  - stride 为负 = 倒退

**stride 语义（2026-11-24 定）**：单脚迈一步的距离。
  脚相对身体的摆动范围 = ±stride/2。
  这具身体腿长 427mm，rest 时几乎伸直（余量 29mm），
  stride 超过 ~0.24m 必须靠「降髋」才够得着。

不累加（D10）：所有位置由 (t, phase, G) 直接算出，不依赖上一帧。
"""

from __future__ import annotations

import math

FORWARD = (0.0, -1.0, 0.0)      # 脸朝 -Y


def quintic(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return u * u * u * (10.0 - 15.0 * u + 6.0 * u * u)


def phase_of(t: float, period: float) -> float:
    if period <= 0:
        return 0.0
    return (t / period) % 1.0


def stance_or_swing(p: float, duty: float) -> str:
    return "stance" if p < duty else "swing"


def body_speed(G: dict) -> float:
    """步态隐含的身体速度（m/s），**唯一正确值**。

    支撑相脚要在世界里纹丝不动，身体就必须以这个速度前进：
        v_body = stride / (duty × period)
    走快了脚往前滑，走慢了往后滑（实测 vel=0.5 时一个支撑期滑 118mm）。

    stride=0（站桩族）返回 0.0 —— 表示「这个族不定义速度」，调用方自由。
    """
    stride = float(G.get("stride", 0.0))
    if abs(stride) < 1e-9:
        return 0.0
    duty = min(max(float(G.get("duty", 0.5)), 0.05), 1.0)
    period = max(float(G.get("period", 1.0)), 1e-3)
    return stride / (duty * period)


def foot_targets(G: dict, t: float, start: dict[str, list],
                 body_origin: list | None = None, forward=FORWARD,
                 travel: bool = True):
    """返回 {side: [x,y,z]}（脚踝目标**世界**位置）。

    `travel=False` 时**不前进**，只算脚相对身体的周期位置。
    前进交给 `BodyState`（唯一位移来源），避免双重计算。

    start: 起始脚踝位置（rest 的踝）；躯干中线取两脚中点。
    stride 语义 = 单脚一步的距离：脚相对身体的摆幅是 ±stride/2。
    身体速度让支撑相脚在世界坐标里**纹丝不动**：
        v_body = stride / (duty × period)
    """
    period = float(G.get("period", 1.0))
    stride = float(G.get("stride", 0.0))
    duty = float(G.get("duty", 0.5))
    lift = float(G.get("foot_lift", 0.0))
    landing = float(G.get("landing_height", 0.0))
    turn = float(G.get("turn_rate", 0.0))
    phase_off = float(G.get("phase_offset", 0.5))

    duty = min(max(duty, 0.05), 1.0)
    period = max(period, 1e-3)
    base_phase = phase_of(t, period)

    if body_origin is None:
        body_origin = [
            (float(start["left"][0]) + float(start["right"][0])) * 0.5,
            (float(start["left"][1]) + float(start["right"][1])) * 0.5,
            (float(start["left"][2]) + float(start["right"][2])) * 0.5,
        ]

    # 转向：前进方向绕 Z 轴转 turn*t
    ang = math.radians(turn) * t
    c, s = math.cos(ang), math.sin(ang)
    fx, fy = float(forward[0]), float(forward[1])
    fwd = (fx * c - fy * s, fx * s + fy * c, 0.0)

    # 身体前进（支撑相脚不动 → v = stride / (duty*period)）
    # travel=False 时**不前进**，只算脚相对身体的周期位置。
    # 前进交给 BodyState（唯一位移来源），避免双重计算。
    travel_m = stride * (t / (duty * period)) if travel else 0.0
    body = [float(body_origin[0]) + fwd[0] * travel_m,
            float(body_origin[1]) + fwd[1] * travel_m,
            float(body_origin[2])]

    # 每只脚相对身体左右偏移（用 start 与身体中线的差）
    lateral = {k: float(start[k][0]) - float(body_origin[0]) for k in ("left", "right")}
    # 左右偏移必须沿「前进方向的法向」——转身后要跟着转，
    # 否则两脚永远沿世界 X 分开，转身时脚会横着挪（实测朝向只转 41° 而不是 90°）
    perp = (-fwd[1], fwd[0], 0.0)

    out: dict[str, list] = {}
    for side, p_off in (("left", 0.0), ("right", phase_off)):
        p = (base_phase + p_off) % 1.0
        if p < duty:
            u = p / duty
            rel = stride * 0.5 * (1.0 - 2.0 * u)      # +s/2 → −s/2
            dz = 0.0
        else:
            u = (p - duty) / max(1e-6, 1.0 - duty)
            rel = stride * 0.5 * (-1.0 + 2.0 * quintic(u))   # −s/2 → +s/2
            dz = lift * math.sin(math.pi * u)

        out[side] = [
            round(body[0] + fwd[0] * rel + perp[0] * lateral[side], 6),
            round(body[1] + fwd[1] * rel + perp[1] * lateral[side], 6),
            round(body[2] + dz + landing, 6),
        ]
    return out


def foot_heights(targets: dict) -> dict:
    return {k: v[2] for k, v in targets.items()}


def is_airborne(targets: dict, ground_z: float, tol_mm: float = 35.0) -> bool:
    return all((v[2] - ground_z) * 1000.0 > tol_mm for v in targets.values())

