"""core/emotion.py — 情绪 → 参数偏置。

设计依据：docs/design/10-emotion_bias.md、HANDOFF D6 / P1.5

两条硬规则：
  1. 偏置绝不作用于 K.targets（否则 D1 的旗舰证据失效）
  2. 幅度封顶 ±15%（超了就不是「同一个动作的不同情绪」）

低通用一阶指数（dt 无关），不用弹簧（弹簧要固定子步）。
"""

from __future__ import annotations

import json
import math
import os

AXES = ("val", "aro", "close", "guard")


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


class EmotionState:
    """四轴状态 + 一阶低通。"""

    def __init__(self, tau_s: float = 1.0, init: dict | None = None):
        self.tau_s = float(tau_s)
        self.raw = {a: 50.0 for a in AXES}
        self.smooth = {a: 50.0 for a in AXES}
        if init:
            for a in AXES:
                if a in init:
                    self.raw[a] = self.smooth[a] = float(init[a])

    def set_raw(self, axes: dict) -> None:
        for a in AXES:
            if a in axes:
                self.raw[a] = clamp(float(axes[a]), 0.0, 100.0)

    def update(self, dt_s: float) -> dict:
        """推进低通。返回当前平滑值。dt 用真实帧间隔。"""
        if dt_s <= 0:
            return dict(self.smooth)
        alpha = 1.0 - math.exp(-dt_s / self.tau_s)
        for a in AXES:
            self.smooth[a] += alpha * (self.raw[a] - self.smooth[a])
        return dict(self.smooth)

    def snapshot(self) -> dict:
        return dict(self.smooth)


def load_table(path: str | None = None) -> dict:
    if path is None:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "emotion_bias.json",
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalized(axis_value: float) -> float:
    """把 0..100 映射到 [-1, 1]，50 为 0。"""
    return clamp((float(axis_value) - 50.0) / 50.0, -1.0, 1.0)


def apply_bias(
    params: dict,
    axes: dict,
    table: dict,
    ranges: dict,
) -> dict:
    """对参数副本叠加情绪偏置。

    params: {name: value}（P 与 G 的连续通道混在一起）
    ranges: {name: (lo, hi)}，来自 bounds 的全局范围
    返回新的 dict，不修改入参。

    **总偏移封顶 ±15%**：多个轴同时作用于同一参数时（例如 val 和 aro 都调
    arm_swing），先把各轴贡献相加，再对「总和」截断。只对单个 k 截断是不够的
    ——那样两轴叠加能到 30%，动作就变成另一个动作了。
    """
    out = dict(params)
    k_max = float(table.get("global_k_max", 0.15))
    forbidden = set(table.get("forbidden", []))

    offsets: dict[str, float] = {}
    for axis, k_map in table.get("axes", {}).items():
        if axis not in axes:
            continue
        n = normalized(axes[axis])
        for pname, k in k_map.items():
            if pname in forbidden:
                raise ValueError(f"emotion_bias 试图修改被禁止的参数 {pname!r}")
            if pname not in out or pname not in ranges:
                continue
            lo, hi = ranges[pname]
            span = (hi - lo) / 2.0
            offsets[pname] = offsets.get(pname, 0.0) + clamp(float(k), -k_max, k_max) * n * span

    for pname, off in offsets.items():
        lo, hi = ranges[pname]
        span = (hi - lo) / 2.0
        limit = k_max * span
        out[pname] = clamp(out[pname] + clamp(off, -limit, limit), lo, hi)
    return out


def validate_table(table: dict) -> list[str]:
    """启动断言：forbidden 里的键不许出现在任何 axes 映射里。"""
    bad = []
    forbidden = set(table.get("forbidden", []))
    for axis, k_map in table.get("axes", {}).items():
        for pname in k_map:
            if pname in forbidden:
                bad.append(f"{axis}.{pname}")
    return bad

# ---------------------------------------------------------------------------
# 情绪改「目标」而不是「幅度」
# ---------------------------------------------------------------------------
# 用户 2026-11-24 指出：直接把 P 缩放是假的 ——「累了」不是「挥手幅度小」，
# 是「手抬到肩就不想再抬了」。所以要改的是**目标**，让求解器重新解。
#
# 这里只改谓词参数，不碰 P。改完必须重新 solve，不能复用旧解。

# energy 低时把手部目标从「过头顶」降到「过肩」
_LOW_ENERGY_SWAP = {
    "hand_above_head": "hand_above_shoulder",
    "hand_above_shoulder": "hand_above_shoulder",
}

# energy 低时锚点距离加多少 mm（越远 = 手越低）
_LOW_ENERGY_DIST_ADD = 90.0
_HIGH_ENERGY_DIST_SUB = 40.0


def bias_predicates(predicates: list[dict], mood: dict | None) -> tuple[list[dict], dict]:
    """按情绪调整谓词目标。返回 (新谓词, 报告)。

    只做**保守**的调整（位置类），不改角度类（那会让姿势拧）。
    没有 mood 或数值异常时原样返回。
    """
    if not mood:
        return [dict(p) for p in predicates or []], {"changed": False}

    energy = mood.get("energy")
    if not isinstance(energy, (int, float)):
        return [dict(p) for p in predicates or []], {"changed": False}
    energy = float(energy)
    mood_v = mood.get("mood")
    mood_num = float(mood_v) if isinstance(mood_v, (int, float)) else 0.5

    out = []
    changes = []
    for p in predicates or []:
        q = {"name": p.get("name"), "args": dict(p.get("args") or {})}

        # 1) 目标降级：energy < 30 时「过头顶」改「过肩」
        if energy < 30 and q["name"] in _LOW_ENERGY_SWAP:
            new_name = _LOW_ENERGY_SWAP[q["name"]]
            if new_name != q["name"]:
                changes.append(f"{q['name']} → {new_name}（精力低）")
                q["name"] = new_name

        # 2) 锚点距离：精力低 → 手更远离头顶（更低）；精力高 → 更近（更高）
        if q["name"] == "hand_near_anchor" and "dist_mm" in q["args"]:
            d = float(q["args"]["dist_mm"])
            if energy < 40:
                d += _LOW_ENERGY_DIST_ADD * (1.0 - energy / 40.0)
                changes.append(f"手目标放低（dist {q['args']['dist_mm']:.0f}→{d:.0f}mm）")
            elif energy > 70:
                d -= _HIGH_ENERGY_DIST_SUB * ((energy - 70.0) / 30.0)
                changes.append(f"手目标抬高（dist {q['args']['dist_mm']:.0f}→{d:.0f}mm）")
            q["args"]["dist_mm"] = round(d, 1)

        # 3) 心情差 → 头微低（只在没指定 head_nod 时加）
        if mood_num < 0.35 and q["name"] == "head_nod" and "deg" in q["args"]:
            q["args"]["deg"] = float(q["args"]["deg"]) + 4.0
            changes.append("头微低（心情差）")

        out.append(q)

    # 心情差且没有 head_nod → 补一条轻微的
    if mood_num < 0.35 and not any(p["name"] == "head_nod" for p in out):
        out.append({"name": "head_nod",
                    "args": {"deg": 5.0, "tol_deg": 8.0}})
        changes.append("补 head_nod=5°（心情差）")

    return out, {"changed": bool(changes), "changes": changes,
                 "energy": energy, "mood": mood_num}
