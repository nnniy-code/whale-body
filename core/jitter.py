"""core/jitter.py — 动作微抖动：在求解出的 P 附近加小扰动，让「同一个动作每次略不同」。

设计依据：用户 2026-11-24 提的「在原有动作上加一点根据当前情况的小范围抖动」

**和求解器的分工**：
  求解器  → 找到满足全部约束的 P*（精确、可复现）
  抖动层  → 在 P* 附近采样，**不破坏约束**（不精确、每次不同）

  所以这不是新求解器，是「后处理 + 守卫」：
      P' = perturb(P*, mood) → verify(P') → 不通过就回退 P*

**为什么要抖动**：
  同一个动作每次都解出完全一样的姿势（seed 固定），看起来像机器人。
  真实人每次都会略不同 —— 但差异有「倾向」（累了偏低、兴奋偏大），不是纯随机。

**不做的事**：
  - 不改「目标」（那是情绪规则/谓词的事）
  - 不抖动二次骨（头发/裙子跟随主骨）
  - 不为了抖动牺牲约束（守卫会回退）
"""

from __future__ import annotations

import numpy as np

# 抖动基准：**典型动作幅度**，不是参数全量程。
# 用全量程会让 arm_lift 抖出 ±16°（200×8%），看起来像抽筋。
# 这里按「一个正常动作会变动多少」估。
SPAN = {
    "lean": 20.0, "side_bend": 15.0, "head_nod": 20.0, "head_tilt": 20.0,
    "head_yaw": 25.0, "arm_swing": 40.0, "arm_lift": 60.0, "arm_roll": 40.0,
    "elbow_bend": 50.0, "wrist_bend": 30.0, "hand_bias": 25.0, "tail_curl": 30.0,
}

# 哪些参数抖得少（头部/尾巴比手臂敏感）
SENSITIVITY = {
    "head_nod": 0.4, "head_tilt": 0.4, "head_yaw": 0.5,
    "lean": 0.5, "side_bend": 0.5, "tail_curl": 0.7,
    "arm_lift": 1.0, "arm_swing": 1.0, "elbow_bend": 1.0,
    "arm_roll": 0.8, "wrist_bend": 0.6, "hand_bias": 0.6,
}


def mood_scale(mood: dict | None) -> tuple[float, float]:
    """由情绪算出 (随机幅度, 方向偏置)。

    精力高 → 抖得大；精力低 → 抖得小且整体偏低。
    """
    m = mood or {}
    energy = float(m.get("energy", 50) or 50)
    mood_v = m.get("mood")
    # mood 可能是文字（"发愣"），只认数值
    mood_num = float(mood_v) if isinstance(mood_v, (int, float)) else 0.5

    scale = 0.015 + 0.035 * (energy / 100.0)         # 1.5% ~ 5%
    drift = -0.025 * (1.0 - energy / 100.0)          # 最多 -2.5%（累了下沉）
    drift += 0.02 * (mood_num - 0.5)                 # 心情好 → 略上扬
    return scale, drift


def perturb(P: dict, mood: dict | None = None, rng=None,
            scale: float | None = None, drift: float | None = None) -> dict:
    """在 P 上叠加抖动，返回新的 dict（不改原对象）。"""
    rng = rng or np.random.default_rng()
    s_auto, d_auto = mood_scale(mood)
    s = s_auto if scale is None else float(scale)
    d = d_auto if drift is None else float(drift)

    out = {}
    for k, v in P.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            out[k] = v
            continue
        span = SPAN.get(k, 30.0) * SENSITIVITY.get(k, 1.0)
        noise = float(rng.normal(0.0, s * span))
        # 偏置只加在「会体现状态」的维度上（手臂/躯干/头），尾巴不偏
        bias = d * span if k in ("arm_lift", "elbow_bend", "lean", "head_nod") else 0.0
        out[k] = fv + noise + bias
    return out


def clamp_to_range(P: dict, family: dict) -> dict:
    """抖完夹回族的 P_range（防止抖出界被 verify 直接拒）。"""
    rng = family.get("P_range") or {}
    out = {}
    for k, v in P.items():
        if k in rng and isinstance(v, (int, float)):
            lo, hi = rng[k]
            out[k] = float(min(max(float(v), lo), hi))
        else:
            out[k] = v
    return out


def safe_perturb(P: dict, family: dict, mood: dict | None,
                 verify_fn, rng=None, max_tries: int = 2) -> tuple[dict, dict]:
    """带守卫的抖动：抖完验证，不通过就再试，最后回退原值。

    verify_fn(P) -> bool
    返回 (最终 P, 报告 dict)
    """
    rng = rng or np.random.default_rng()
    original = dict(P)
    for attempt in range(max_tries):
        cand = clamp_to_range(perturb(original, mood, rng), family)
        if verify_fn(cand):
            return cand, {"ok": True, "attempts": attempt + 1,
                          "fallback": False, "delta": _delta(original, cand)}
    return original, {"ok": True, "attempts": max_tries,
                      "fallback": True, "delta": {}}


def _delta(a: dict, b: dict) -> dict:
    out = {}
    for k in a:
        if isinstance(a[k], (int, float)) and isinstance(b.get(k), (int, float)):
            d = float(b[k]) - float(a[k])
            if abs(d) > 1e-9:
                out[k] = round(d, 2)
    return out
