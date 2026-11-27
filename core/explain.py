"""core/explain.py — 把求解失败的技术原因转成用户看得懂的话。

设计依据：用户要求「报错原因不要只进日志，使用者也看得到」

**分两层**：
    explain_gap(gap)      → 一句话（给用户）
    explain_detail(detail) → 一句话（给调试）

**原则**：
  - 用户不需要知道 `elbow_angle` 是什么，只需要知道「手肘弯不到那么低」
  - 但**具体数字要保留**（差多少、要求多少），否则没法判断是系统坏了还是描述错了
"""

from __future__ import annotations

# 谓词 → 用户能懂的名字
PREDICATE_ZH = {
    "torso_lean": "身体前倾",
    "torso_upright": "身体直立",
    "head_nod": "点头",
    "head_tilt": "歪头",
    "head_yaw": "转头",
    "arm_lift": "手臂抬起",
    "arm_swing": "手臂前后摆",
    "arm_not_crossed": "手臂不交叉",
    "elbow_angle": "手肘弯曲",
    "elbow_below_shoulder": "手肘低于肩",
    "elbow_below_hand": "手肘低于手",
    "elbow_behind_hand": "手肘在手后面",
    "elbow_outside": "手肘朝外",
    "elbow_forward": "手肘朝前",
    "elbow_near_anchor": "手肘靠近",
    "forearm_upright": "前臂竖直",
    "wrist_neutral": "手腕不折",
    "hand_above_head": "手举过头顶",
    "hand_above_shoulder": "手举过肩",
    "hand_below_waist": "手低于腰",
    "hand_outside_body": "手在身体外侧",
    "hand_near_anchor": "手靠近",
    "hand_at_point": "手到指定点",
    "hand_elbow_gap": "手和手肘的间距",
    "knee_angle": "膝盖弯曲",
    "knee_forward": "膝盖朝前",
    "shoulder_range": "肩的活动范围",
    "foot_grounded": "脚踩地",
    "foot_lifted": "脚抬起",
    "foot_at_point": "脚到指定点",
    "foot_near_anchor": "脚靠近",
    "foot_slip": "脚不打滑",
    "tail_tip_at_anchor": "尾巴尖的位置",
    "gaze_toward": "视线朝向",
    "balance": "身体平衡",
    "no_self_collision": "不穿模",
    "no_ground_penetration": "不穿地",
    "no_hyperextension": "关节不反折",
    "no_big_step": "步幅不过大",
    "no_micro_jitter": "不抖",
    "jerk_rms": "动作平滑",
    "max_deg_per_frame": "每帧转动不过大",
    "inside_workspace": "在可达范围内",
    "dual_support": "双脚支撑",
    "duty_match": "步态占空比",
}


def _friendly(pred: str) -> str:
    return PREDICATE_ZH.get(pred, pred)


def _fmt_detail(pred: str, d: dict) -> str:
    """把一个 detail 转成「要求 vs 实际」。"""
    if pred in ("torso_lean", "head_nod", "head_tilt", "head_yaw"):
        target = d.get("target", d.get("deg"))
        actual = d.get("abs_deg", d.get("deg"))
        if target is not None and actual is not None:
            return f"要求 {target:+.0f}°，实际 {actual:+.1f}°"
        return f"实际 {actual}°"
    if pred == "elbow_angle":
        rng = d.get("range")
        deg = d.get("deg")
        if rng and deg is not None:
            return f"要求 {rng[0]}–{rng[1]}°，实际 {deg:.1f}°"
        return f"实际 {deg}°"
    if pred == "knee_angle":
        rng = d.get("range")
        deg = d.get("deg")
        if rng and deg is not None:
            return f"要求 {rng[0]}–{rng[1]}°，实际 {deg:.1f}°"
        return f"实际 {deg}°"
    if "dist_mm" in d or "d_mm" in d:
        return f"要求 {d.get('dist_mm', d.get('min_mm', '?'))}mm，实际 {d.get('d_mm', '?')}mm"
    if "deg" in d:
        return f"实际 {d['deg']}°"
    # 兜底：列出前两个数字字段
    keys = [k for k in d if not k.startswith("_")]
    if keys:
        return ", ".join(f"{k}={d[k]}" for k in keys[:2])
    return ""


def explain_detail(detail: list[dict]) -> str:
    """把一个 detail 列表转成一句话。"""
    if not detail:
        return ""
    # 按误差从大到小排，取最严重的两条
    items = sorted(detail, key=lambda x: -float(x.get("err", 0)))[:2]
    parts = []
    for it in items:
        pred = it.get("predicate", "?")
        d = it.get("detail") or {}
        parts.append(f"{_friendly(pred)}：{_fmt_detail(pred, d)}")
    return "；".join(parts)


def explain_gap(gap: dict | None) -> str:
    """把 `capability_gap` 转成一句给用户看的话。"""
    if not gap:
        return ""
    kind = gap.get("kind")
    wanted = gap.get("wanted") or gap.get("family") or "这个动作"

    if kind == "no_family":
        return f"「{wanted}」我还没有对应的动作，做不出来。"

    if kind == "solve_failed":
        why = explain_detail(gap.get("detail") or [])
        return f"「{wanted}」做不到——{why}。" if why else f"「{wanted}」我算不出来。"

    if kind == "solve_error":
        return f"「{wanted}」算的时候出了点错（参数写错了？），我没法做。"

    if kind == "verify_failed":
        why = explain_detail(gap.get("detail") or [])
        return f"「{wanted}」算出来会撞到自己——{why}。" if why else f"「{wanted}」算出来会撞到自己。"

    if kind == "out_of_range":
        return f"「{wanted}」超出了我身体能动的范围。"

    return f"「{wanted}」做不出来。"


def explain_result(r: dict) -> str:
    """从 `handle_intent` / `plan_trajectory` 的返回里抽一句话。"""
    if not r:
        return ""
    return explain_gap(r.get("capability_gap") or r.get("gap"))


def explain_trajectory(tr: dict) -> str:
    """轨迹规划失败时的说明。"""
    if not tr:
        return ""
    mode = tr.get("mode")
    if mode == "no_entry":
        gaps = tr.get("gaps") or []
        return explain_gap(gaps[0]) if gaps else "这个动作我做不出来。"
    if mode == "failed":
        bad = tr.get("still_bad") or []
        return f"这条动作路线会撞到自己（{len(bad)} 帧过不去），我没法做。"
    if mode == "repaired":
        n = len(tr.get("repaired") or [])
        return f"这条动作路线绕了一下（{n} 帧调过），做出来了。"
    return ""
