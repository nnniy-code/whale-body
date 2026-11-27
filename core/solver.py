"""core/solver.py — L2 求解：P 空间上的 CMA-ES。

设计依据：HANDOFF §4.5（三段搜索）、§8.5（纯 FK 求值）

本版范围（P7 第一刀）：
  求解**单帧姿态**的 P（10 维），G 固定为族默认。
  走路/跑步这类「轨迹」由 P6 的 gait 负责，不进 L2。
  后续若要搜 G，接口已留好（solve 接受 g_space）。

求值成本：0.05 ms/次（numpy FK），3000 次 ≈ 150 ms。
"""

from __future__ import annotations

import time

import numpy as np

from core import gate as G
from core import pose_gen
from core.cmaes import cma_es
from core.predicates import Frame

P_KEYS = ["lean", "side_bend", "head_nod", "head_tilt", "head_yaw",
          "arm_swing", "arm_lift", "arm_roll", "elbow_bend", "wrist_bend",
          "hand_bias", "tail_curl"]

# 全局兜底范围（族的 P_range 覆盖它；此处保证新增维度有定义）
DEFAULT_BOUNDS = {
    "lean": (-25.0, 25.0),
    "side_bend": (-20.0, 20.0),
    "head_nod": (-25.0, 25.0),
    "head_tilt": (-30.0, 30.0),
    "head_yaw": (-35.0, 35.0),
    "arm_swing": (-60.0, 60.0),
    "arm_lift": (-20.0, 180.0),
    "arm_roll": (-90.0, 90.0),      # v2 新增：上臂扭转 → 控制肘尖朝向
    "elbow_bend": (0.0, 140.0),
    "wrist_bend": (-45.0, 45.0),    # v2 新增：腕
    "hand_bias": (-40.0, 40.0),
    "tail_curl": (-30.0, 30.0),
}

# 求解只需要这些骨（21 主骨 + 尾巴 4 节）。WhaleTail_01 的父级是 hips，闭包成立。
MAIN21 = [
    "hips", "leftUpperLeg", "leftLowerLeg", "leftFoot",
    "rightUpperLeg", "rightLowerLeg", "rightFoot",
    "spine", "chest", "upperChest",
    "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand",
    "neck", "head", "mixamorig:HeadTop_End",
    "rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand",
]
FAST_BONES = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]


def fast_rig(rig):
    """返回只含 MAIN21 + 尾巴的 Rig（FK 从 0.52ms 降到 ~0.05ms）。"""
    try:
        return rig.subset(FAST_BONES)
    except ValueError:
        return rig


def vec_to_P(vec, keys=None) -> dict:
    keys = keys or P_KEYS
    return {k: float(v) for k, v in zip(keys, vec)}


def P_to_vec(P: dict, keys=None):
    keys = keys or P_KEYS
    return np.array([float(P.get(k, 0.0)) for k in keys], dtype=float)


def bounds_of(family: dict, keys=None):
    """族的 P_range 优先；没写的维度回落到 DEFAULT_BOUNDS。

    这样新增维度（arm_roll / wrist_bend）不用把 12 个族全部改一遍。
    """
    keys = keys or P_KEYS
    rng = family.get("P_range", {})
    lo_l, hi_l = [], []
    for k in keys:
        if k in rng:
            a, b = rng[k]
        else:
            a, b = DEFAULT_BOUNDS.get(k, (-30.0, 30.0))
        lo_l.append(float(a))
        hi_l.append(float(b))
    return np.array(lo_l), np.array(hi_l)


def make_frame(M: dict, anchors_cfg: dict, meta: dict | None = None) -> Frame:
    """由 FK 结果构造一个 Frame（含锚点与接触状态）。"""
    import numpy as np
    from core.contact import detect

    pts = anchors_cfg.get("points", {})
    anchors = {}
    for name, d in pts.items():
        b = d.get("bone")
        if b is None or b not in M:
            continue
        off = np.array(d.get("offset_m", [0, 0, 0]), dtype=float)
        anchors[name] = (M[b] @ np.array([*off, 1.0]))[0:3]

    # 脚底（用踝近似，误差几毫米）
    sole = {}
    for side in ("left", "right"):
        b = f"{side}Foot"
        if b in M:
            sole[b] = float(M[b][0:3, 3][2]) - 0.08

    return Frame(M=M, contacts=detect(sole), anchors=anchors, meta=meta or {})


def evaluate_vec(vec, rig, bm, predicates, anchors_cfg, thresholds=None,
                 active_side=None, meta=None) -> float:
    P = vec_to_P(vec)
    basis = pose_gen.build(P, rig, bm, active_side=active_side)
    M = rig.compute(basis)
    f = make_frame(M, anchors_cfg, meta)
    return G.evaluate([f], predicates, thresholds)


def solve(rig, bm, family: dict, predicates: list[dict], anchors_cfg: dict,
          thresholds: dict | None = None, budget: int = 3000,
          sigma: float = 0.22, active_side=None, seed: int = 0,
          init_P: dict | None = None, log=None, trace_id: str | None = None,
          capsules: dict | None = None, collision_table: dict | None = None,
          lengths: dict | None = None, capsule_baseline: dict | None = None,
          capsule_only=None):
    """在 P 空间求解。返回 dict：params / evals / calc_ms / converged / gaps。

    **胶囊不在搜索循环里**（3.4ms/次会拖成 10 秒）。只在解出最优解后测一次：
      - 不过 → converged=False，violations 里带骨名，交给 L3 罚项回灌
    这符合设计里「求解快、复检准、夜间回灌」的分工。
    """
    if not family or "P_range" not in family:
        raise ValueError("family 缺 P_range")
    lo, hi = bounds_of(family)
    init = dict(family["P_default"]) if init_P is None else dict(init_P)
    x0 = np.clip(P_to_vec(init), lo, hi)
    span = float(np.mean(hi - lo)) or 1.0
    sig = float(sigma) * span

    meta = dict(thresholds or {})
    t0 = time.perf_counter()

    rig_fast = fast_rig(rig)          # 21 骨 + 尾巴，FK 快 10 倍

    # ---- 阶段 1：粗定位（位置类谓词，快速缩小范围）----
    # 用户 2026-11-24 提的：全空间 10 维 3000 次不够，应该先给范围再精调。
    coarse = [p for p in predicates
              if p["name"] in ("hand_above_shoulder", "hand_above_head",
                               "hand_near_anchor", "hand_at_point", "elbow_below_shoulder")]
    rest_budget = int(budget)
    x1 = x0
    ev1 = 0
    if coarse and len(coarse) < len(predicates):
        n1 = max(200, int(budget * 0.35))
        x1, _f1, ev1 = cma_es(
            lambda v: evaluate_vec(v, rig_fast, bm, coarse, anchors_cfg,
                                   thresholds, active_side, meta),
            x0, max(1e-4, sig), lo, hi, max_evals=n1, seed=seed,
        )
        rest_budget = budget - ev1

    # ---- 阶段 2：在粗解附近精调（全部谓词、步长收窄）----
    span1 = float(np.mean(hi - lo)) or 1.0
    sig2 = max(1e-4, sig * 0.35)
    best_P, best_f, ev2 = cma_es(
        lambda v: evaluate_vec(v, rig_fast, bm, predicates, anchors_cfg,
                               thresholds, active_side, meta),
        x1, sig2, lo, hi, max_evals=max(200, rest_budget), seed=seed + 1,
    )
    evals = ev1 + ev2
    calc_ms = (time.perf_counter() - t0) * 1000.0

    P_best = vec_to_P(best_P)
    basis = pose_gen.build(P_best, rig_fast, bm, active_side=active_side)
    M = rig_fast.compute(basis)
    frame = make_frame(M, anchors_cfg, meta)
    v = G.verdict([frame], predicates, thresholds,
                  log=log, trace_id=trace_id, family=family.get("label"))

    gaps = [{"predicate": x.predicate, "err": round(x.err, 4),
             "detail": x.detail} for x in v.violations]

    # ---- 胶囊复检（只在最优解上做一次）----
    caps_report = None
    if capsules and collision_table is not None:
        from core.collision import check_capsules
        caps_v = check_capsules(M, capsules, rig_fast, collision_table,
                                baseline=capsule_baseline, lengths=lengths,
                                chain_skip=3, only=capsule_only)
        caps_report = caps_v
        for c in caps_v:
            gaps.append({
                "predicate": "no_self_collision",
                "err": 1.0,
                "detail": {"bone": c["bone"], "other": c["other"],
                           "regions": c["regions"], "over_mm": c["over_mm"]},
            })
        if caps_v:
            v.passed = False

    return {
        "params": {"P": P_best, "G": dict(family.get("G", {}))},
        "evals": int(evals),
        "calc_ms": round(calc_ms, 2),
        "converged": bool(v.passed),
        "total_err": round(v.total_err, 6),
        "violations": gaps,
        "capsules": caps_report,
        "verdict": v.as_dict(),
    }
