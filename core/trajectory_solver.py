"""core/trajectory_solver.py — 起点 → 终点的轨迹求解。

设计依据：DEVLOG 2026-11-27「直线插值 7/8 不撞」

**不是路径规划**。实测 87.5% 的动作直线插值就合法，
所以算法是：

    直线采样 20 帧 → 逐帧检测 → 全过就用
    → 有撞的帧 → 二分找最大合法 s → 替换

**成本**：无撞 0.1ms / 有撞 20-80ms（一次性规划）

**判据**（用户定的）：
    1. 不穿模（胶囊）
    2. 不超关节范围（no_hyperextension 等）
    3. 路程最短（直线本身就最短，只在必要时绕）
"""

from __future__ import annotations

import numpy as np


def lerp_P(Pa: dict, Pb: dict, s: float) -> dict:
    """两个 P 之间的线性插值。"""
    keys = set(Pa) | set(Pb)
    return {k: float(Pa.get(k, 0.0)) * (1.0 - s) + float(Pb.get(k, 0.0)) * s
            for k in keys}


def ease(s: float) -> float:
    """smoothstep：两端慢、中间快。让过渡不突兀。"""
    s = max(0.0, min(1.0, float(s)))
    return s * s * (3.0 - 2.0 * s)


def _check(P: dict, rig, bm, side, anchors_cfg, caps, tab, lengths, base, only,
           predicates=None) -> tuple[bool, str]:
    """检查一个 P 是否合法。返回 (ok, reason)。"""
    from core.collision import check_capsules
    from core.fk import Rig  # noqa: F401
    from core.pose_gen import build
    from core.predicates import evaluate_one
    from core.solver import make_frame

    try:
        M = rig.compute(build(P, rig, bm, active_side=side))
    except Exception as e:
        return False, f"build:{e}"

    cv = check_capsules(M, caps, rig, tab, baseline=base, lengths=lengths,
                        chain_skip=3, only=only)
    if cv:
        return False, f"capsule:{len(cv)}"

    f = make_frame(M, anchors_cfg)
    for s in ("left", "right"):
        e, _ = evaluate_one("no_hyperextension", f, side=s, min_deg=20)
        if e >= 1.0:
            return False, f"hyperext:{s}"
    for s in ("left", "right"):
        e, _ = evaluate_one("knee_angle", f, side=s, min_deg=0, max_deg=160)
        if e >= 1.0:
            return False, f"knee:{s}"
    return True, "ok"


def solve_path(P_start: dict, P_end: dict, n_frames: int = 20,
               rig=None, bm=None, side: str | None = None,
               anchors_cfg=None, caps=None, tab=None, lengths=None,
               base=None, only=None, max_repair: int = 6) -> dict:
    """求解起点到终点的轨迹。

    返回：
        {
          "frames": [P, ...],       # 长度 n_frames，含起点和终点
          "mode": "straight" | "repaired" | "failed",
          "repaired": [帧号...],     # 被修过的帧
          "cost_ms": float,
        }
    """
    import time
    t0 = time.perf_counter()

    # **先检终点**：终点本身就违规的话，路径怎么修都没用，先把终点缩回来
    end_scaled = 1.0
    if rig is not None:
        ok_end, _why = _check(P_end, rig, bm, side, anchors_cfg, caps, tab,
                              lengths, base, only)
        if not ok_end:
            lo_k, hi_k = 0.0, 1.0
            for _ in range(8):
                mid = (lo_k + hi_k) * 0.5
                P_try = lerp_P(P_start, P_end, mid)
                ok, _w = _check(P_try, rig, bm, side, anchors_cfg, caps, tab,
                                lengths, base, only)
                if ok:
                    lo_k = mid
                else:
                    hi_k = mid
            end_scaled = max(0.05, lo_k)
            P_end = lerp_P(P_start, P_end, end_scaled)

    frames = [lerp_P(P_start, P_end, ease(i / max(1, n_frames - 1)))
              for i in range(n_frames)]
    frames[0] = dict(P_start)
    frames[-1] = dict(P_end)

    if rig is None:
        return {"frames": frames, "mode": "straight", "repaired": [],
                "cost_ms": round((time.perf_counter() - t0) * 1000, 2)}

    # 逐帧检测
    bad = []
    for i, P in enumerate(frames):
        ok, _why = _check(P, rig, bm, side, anchors_cfg, caps, tab, lengths, base, only)
        if not ok:
            bad.append(i)

    if not bad:
        return {"frames": frames, "mode": "straight", "repaired": [],
                "cost_ms": round((time.perf_counter() - t0) * 1000, 2)}

    # 局部修正：对每个撞的帧，二分找最大合法 s
    repaired = []
    for idx in bad[:max_repair]:
        lo = (idx - 1) / max(1, n_frames - 1)      # 上一个已知合法的 s
        hi = idx / max(1, n_frames - 1)            # 撞的 s
        best_s = lo
        for _ in range(8):                          # 二分 8 次 ≈ 1/256 精度
            mid = (lo + hi) * 0.5
            P_try = lerp_P(P_start, P_end, ease(mid))
            ok, _w = _check(P_try, rig, bm, side, anchors_cfg, caps, tab,
                            lengths, base, only)
            if ok:
                best_s = mid
                lo = mid
            else:
                hi = mid
        frames[idx] = lerp_P(P_start, P_end, ease(best_s))
        repaired.append(idx)

    # 再检一遍
    still_bad = []
    for i, P in enumerate(frames):
        ok, _w = _check(P, rig, bm, side, anchors_cfg, caps, tab, lengths, base, only)
        if not ok:
            still_bad.append(i)

    mode = "repaired" if not still_bad else "failed"
    return {
        "frames": frames,
        "mode": mode,
        "repaired": repaired,
        "end_scaled": round(end_scaled, 3),
        "still_bad": still_bad,
        "cost_ms": round((time.perf_counter() - t0) * 1000, 2),
    }

