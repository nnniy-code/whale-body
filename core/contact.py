"""core/contact.py — 接触检测。

设计依据：docs/design/02-predicates.md（C 类谓词）、WORKFLOW §2

职责边界：
  只做**几何**判定（离地多少、穿地多少）。
  「能不能离地」由谓词根据步态模式决定（walk 必须落地，run 腾空段允许）。
  这样 contact 不需要知道族，谓词不需要看矩阵。

地面：默认 z=0（这具身体 rest 姿态脚底在 z≈0）。
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_TOL_MM = 35.0          # 坑 #5：走路摆腿时支撑脚抬升 21mm 属正常，所以 35
PENETRATION_TOL_MM = 3.0       # 低于地面 3mm 以上算穿地


@dataclass
class FootContact:
    side: str
    sole_z_m: float
    height_mm: float          # 离地高度（>0）
    penetration_mm: float     # 穿地深度（>0 = 陷进地面）
    grounded: bool            # 高度 <= tol 且没穿地
    below: bool               # 是否低于地面


def phase_region(phase: float, duty: float) -> str:
    """单只脚的相位区间：'stance'（支撑相）或 'swing'（摆动相）。"""
    if not (0.0 <= duty <= 1.0):
        raise ValueError(f"duty 必须在 [0,1]，收到 {duty}")
    p = phase % 1.0
    return "stance" if p < duty else "swing"


def detect(
    sole_z: dict[str, float],
    tol_mm: float = DEFAULT_TOL_MM,
    ground_z: float = 0.0,
) -> dict[str, FootContact]:
    """由每只脚的最低点高度算出接触状态。"""
    out: dict[str, FootContact] = {}
    for side, z in sole_z.items():
        h_mm = (z - ground_z) * 1000.0
        pen_mm = max(0.0, -h_mm)
        grounded = (h_mm <= tol_mm) and (pen_mm < PENETRATION_TOL_MM)
        out[side] = FootContact(
            side=side,
            sole_z_m=float(z),
            height_mm=float(h_mm),
            penetration_mm=float(pen_mm),
            grounded=bool(grounded),
            below=bool(h_mm < 0.0),
        )
    return out


def dual_support_ratio(contacts_per_frame: list[dict[str, FootContact]]) -> float:
    """整段动作里「双脚同时着地」的帧占比。

    注意换算：单脚支撑相占比 duty 与双支撑占比的关系是
        dual = max(0, 2*duty - 1)
    duty=0.62 → 0.24（walk 有双支撑相，但只占 24% 的帧）
    duty=0.35 → 0（run 没有双支撑，反而有腾空）
    gait-guide 说的「walk duty>0.5」指前者，不是后者。
    """
    if not contacts_per_frame:
        return 0.0
    n = 0
    for frame in contacts_per_frame:
        if len(frame) >= 2 and all(c.grounded for c in frame.values()):
            n += 1
    return n / len(contacts_per_frame)


def stance_frames(contacts_per_frame: list[dict[str, FootContact]]) -> dict[str, list[int]]:
    """每只脚处于着地状态的帧号列表（用于 foot_slip 检查）。"""
    out: dict[str, list[int]] = {}
    for i, frame in enumerate(contacts_per_frame):
        for side, c in frame.items():
            if c.grounded:
                out.setdefault(side, []).append(i)
    return out
