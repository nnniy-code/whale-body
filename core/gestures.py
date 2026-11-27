"""core/gestures.py — 预设手势：关键帧 + 插值，不经过求解器。

设计依据：HANDOFF §4.5、docs/design/07-trajectory.md

为什么不需要求解：
  挥手、点头这种动作的形状是**已知的**，就像 gait.py 的走路轨迹是公式算的。
  只有「库外的新动作」才需要 CMA-ES 去搜。

关键帧用**语义参数**（P 的 10 个键）表示，不是四元数——所以仍然受 D1 约束
（不写关节角），而且能直接喂给 pose_gen。

插值复用 core/trajectory.py 的 Hermite：段间 C1 连续、端点速度可指定。
"""

from __future__ import annotations

import math

import numpy as np

from core import pose_gen, trajectory

# 每个手势：关键帧列表（t 秒 + P 参数的偏置），以及驱动哪一侧
# P 的键会被覆盖到族默认值上（不是从零开始）
GESTURES: dict[str, dict] = {
    "wave": {
        "side": "right",
        "duration": 1.4,
        "loop": True,
        # 肘保持弯曲（95°），只让 arm_lift 上下摆 —— 这才是「挥」，
        # 不是「把手举起来放下」。实测 lift 160/110 对应手高 0.92/0.80。
        "keys": [
            (0.00, {"arm_lift": 135, "elbow_bend": 85, "head_tilt": 5}),
            (0.45, {"arm_lift": 75, "elbow_bend": 70, "head_tilt": 2}),
            (0.90, {"arm_lift": 135, "elbow_bend": 85, "head_tilt": 5}),
        ],
    },
    "wave_small": {
        "side": "right",
        "duration": 0.9,
        "loop": True,
        "keys": [
            (0.00, {"arm_lift": 80, "elbow_bend": 95, "arm_swing": 5}),
            (0.30, {"arm_lift": 72, "elbow_bend": 65, "arm_swing": 2}),
            (0.60, {"arm_lift": 80, "elbow_bend": 95, "arm_swing": 5}),
        ],
    },
    "nod": {
        "side": None,
        "duration": 0.7,
        "loop": True,
        "keys": [
            (0.00, {"head_nod": 0}),
            (0.22, {"head_nod": 14}),
            (0.44, {"head_nod": 0}),
        ],
    },
    "shake": {
        "side": None,
        "duration": 0.8,
        "loop": True,
        "keys": [
            (0.00, {"head_yaw": 0}),
            (0.20, {"head_yaw": 16}),
            (0.40, {"head_yaw": -16}),
            (0.60, {"head_yaw": 0}),
        ],
    },
    "stretch": {
        "side": "right",
        "duration": 1.6,
        "loop": False,
        "keys": [
            (0.00, {"arm_lift": 20, "elbow_bend": 30, "lean": 0}),
            (0.90, {"arm_lift": 150, "elbow_bend": 15, "lean": -8, "head_nod": -6}),
            (1.60, {"arm_lift": 30, "elbow_bend": 35, "lean": 0}),
        ],
    },
    "point": {
        "side": "right",
        "duration": 1.2,
        "loop": False,
        "keys": [
            (0.00, {"arm_lift": 20, "elbow_bend": 40}),
            (0.45, {"arm_lift": 95, "elbow_bend": 8, "arm_swing": 0}),
            (1.20, {"arm_lift": 95, "elbow_bend": 8, "arm_swing": 0}),
        ],
    },

    # ---- 关节角手势（绕世界轴，x→y→z 顺序）----
    # 上臂：y 负 = 向左（外侧）抬起；z 正 = 肘尖朝下/外
    # 前臂：y 负 = 向上折（相对上臂）
    "wave_joint": {
        "side": "right",
        "duration": 1.4,
        "loop": True,
        # 右手：y 正 = 向外侧抬起（负会穿到身体另一侧）
        # 上臂固定在 100°（侧上方），只让前臂在 40↔120 之间摆 —— 这才是挥
        "joint_keys": [
            (0.00, {"rightUpperArm": {"x": 0, "y": 90, "z": 20},
                    "rightLowerArm": {"x": 0, "y": 30, "z": 0}}),
            (0.45, {"rightUpperArm": {"x": 0, "y": 60, "z": 20},
                    "rightLowerArm": {"x": 0, "y": 30, "z": 0}}),
            (0.90, {"rightUpperArm": {"x": 0, "y": 90, "z": 20},
                    "rightLowerArm": {"x": 0, "y": 30, "z": 0}}),
        ],
    },
}


def _build_tracks(keys, base_P: dict) -> dict[str, trajectory.Track]:
    """每个 P 键一条轨道（用族默认值补全程）。"""
    total = keys[-1][0]
    tracks = {}
    all_keys = set()
    for _t, P in keys:
        all_keys |= set(P)
    for k in all_keys:
        times = [t for t, _P in keys]
        vals = [float(P.get(k, base_P.get(k, 0.0))) for _t, P in keys]
        if total > 0 and times[-1] < total:
            times.append(total)
            vals.append(vals[-1])
        # 去掉重复时间点
        uniq = []
        for t, v in zip(times, vals):
            if not uniq or t > uniq[-1][0] + 1e-9:
                uniq.append((t, v))
        if len(uniq) < 2:
            tracks[k] = None
            continue
        tracks[k] = trajectory.build_track([u[0] for u in uniq],
                                           [u[1] for u in uniq],
                                           start_v=0.0, end_v=0.0)
    return tracks


def _joint_basis(rig, bm, bone: str, axes: dict) -> np.ndarray:
    """把 {x:度, y:度, z:度} 转成该骨的 basis（绕世界轴，顺序 x→y→z）。"""
    out = np.eye(4)
    for axis in ("x", "y", "z"):
        d = float(axes.get(axis, 0.0))
        if abs(d) > 1e-9:
            out = out @ bm.basis_from_world_rot(rig, bone, axis, d)
    return out


def _build_joint_tracks(joint_keys) -> dict:
    """{bone: {axis: Track}}"""
    total = joint_keys[-1][0]
    tracks: dict = {}
    for _t, bones in joint_keys:
        for bone, axes in bones.items():
            for axis in axes:
                tracks.setdefault(bone, {}).setdefault(axis, None)
    for bone in tracks:
        for axis in tracks[bone]:
            pts = []
            for t, bones in joint_keys:
                v = float((bones.get(bone) or {}).get(axis, 0.0))
                if not pts or t > pts[-1][0] + 1e-9:
                    pts.append((t, v))
            if total > pts[-1][0] + 1e-9:
                pts.append((total, pts[-1][1]))
            if len(pts) < 2:
                continue
            tracks[bone][axis] = trajectory.build_track(
                [p[0] for p in pts], [p[1] for p in pts])
    return tracks


def sample_joint(gesture_name: str, t: float, rig, bm) -> dict:
    """关节角关键帧采样。返回 {M, bones(angle dict), t}。"""
    g = GESTURES.get(gesture_name)
    if g is None or "joint_keys" not in g:
        raise KeyError(f"{gesture_name!r} 不是关节角手势")
    dur = g["duration"]
    tt = (t % dur) if g.get("loop") else min(t, dur)
    tracks = _build_joint_tracks(g["joint_keys"])

    basis = {n: np.eye(4) for n in rig.names}
    angles = {}
    for bone, axes in tracks.items():
        a = {}
        for axis, tr in axes.items():
            a[axis] = float(tr.value_at(tt)) if tr is not None else 0.0
        angles[bone] = a
        basis[bone] = _joint_basis(rig, bm, bone, a)
    return {"M": rig.compute(basis), "bones": angles, "t": tt}


def sample(gesture_name: str, t: float, base_P: dict, rig, bm) -> dict:
    """在 t 时刻采样手势，返回姿态矩阵。

    循环手势用 t % duration；一次性手势超过 duration 就停在末帧。
    """
    g = GESTURES.get(gesture_name)
    if g is None:
        raise KeyError(f"未知手势 {gesture_name!r}")
    dur = g["duration"]
    tt = (t % dur) if g["loop"] else min(t, dur)

    tracks = _build_tracks(g["keys"], base_P)
    P = dict(base_P)
    for k, tr in tracks.items():
        if tr is not None:
            P[k] = tr.value_at(tt)

    basis = pose_gen.build(P, rig, bm, active_side=g.get("side"))
    return {"M": rig.compute(basis), "P": P, "t": tt}


def list_gestures() -> list[str]:
    return sorted(GESTURES)




