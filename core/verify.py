"""core/verify.py — 入库前的验证（原 L3，改成立即执行）。

设计依据：HANDOFF §7 P8；DEVLOG 2026-11-24「L3 夜间转正是错的」

**为什么不再叫夜间任务**：
  L2 求解 20ms + 本节验证 2.8ms = 23ms，用户无感。
  「夜间」是按 BVH（300ms/条）估的，而 BVH 已被证明不能当主判据。
  所以：**求解完立刻验证，通过才入库**，库里没有 provisional 中间态。

检查项：
  1. 参数范围（P 必须在族的 P_range 内）
  2. 反关节（左右肘角 ≥ min_deg）
  3. 姿势底线（躯干直立、不穿地）
  4. 当初求解用的谓词全部重跑
  5. 胶囊碰撞（21 主骨 + 尾巴）
"""

from __future__ import annotations

import numpy as np

from core.collision import check_capsules
from core.predicates import evaluate_series
from core.solver import MAIN21, make_frame

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]

# 自然度底线：**所有**入库条目强制检查，不管 LLM 写了什么。
# 这些是几何/生理约束，LLM 不知道该写（实测：它会把手臂写成反关节）。
NATURAL_BASELINE = [
    {"name": "torso_upright", "args": {"max_deg": 50}},
    {"name": "no_ground_penetration", "args": {"tol_mm": 5}},
    {"name": "no_hyperextension", "args": {"side": "right", "min_deg": 20}},
    {"name": "no_hyperextension", "args": {"side": "left", "min_deg": 20}},
    {"name": "wrist_neutral", "args": {"side": "right", "max_deg": 60}},
    {"name": "wrist_neutral", "args": {"side": "left", "max_deg": 60}},
    {"name": "knee_angle", "args": {"side": "right", "min_deg": 0, "max_deg": 160}},
    {"name": "knee_angle", "args": {"side": "left", "min_deg": 0, "max_deg": 160}},
    {"name": "shoulder_range", "args": {"side": "right", "max_deg": 178}},
    {"name": "shoulder_range", "args": {"side": "left", "max_deg": 178}},
    # 方向判据：光查夹角抓不到反折（实测反折和正常都能通过 knee_angle）
    {"name": "knee_forward", "args": {"side": "right", "min_mm": 10}},
    {"name": "knee_forward", "args": {"side": "left", "min_mm": 10}},
]

BASELINE_PREDICATES = NATURAL_BASELINE     # 向后兼容


def range_violations(P: dict, family: dict) -> list[str]:
    """P 值越界检查。"""
    rng = family.get("P_range") or {}
    bad = []
    for k, v in P.items():
        if k in rng:
            lo, hi = rng[k]
            try:
                fv = float(v)
            except (TypeError, ValueError):
                bad.append(f"{k}={v!r} 不是数字")
                continue
            if not (lo - 1e-6 <= fv <= hi + 1e-6):
                bad.append(f"{k}={fv:.1f} 不在 [{lo},{hi}]")
    return bad


def verify_pose(P: dict, family: dict, predicates: list[dict], M, rig_fast,
                anchors_cfg: dict, capsules: dict, collision_table: dict,
                lengths: dict, baseline: dict | None,
                side: str | None = None) -> dict:
    """对一组参数 + 已算好的姿态做入库前验证。

    返回 {"ok": bool, "reason": str, "checks": {...}}
    """
    checks = {}

    # 1) 参数范围
    bad = range_violations(P, family)
    checks["range"] = {"ok": not bad, "bad": bad[:3]}
    if bad:
        return {"ok": False, "reason": "参数越界：" + "; ".join(bad[:2]), "checks": checks}

    # 2) 谓词（当初用的 + 底线）
    frame = make_frame(M, anchors_cfg)
    worst_name, worst_err = "?", 0.0
    pred_ok = True
    for p in list(predicates or []) + BASELINE_PREDICATES:
        err, _w, _d = evaluate_series(p["name"], [frame], **(p.get("args") or {}))
        if err > worst_err:
            worst_name, worst_err = p["name"], float(err)
        if err >= 1.0:
            pred_ok = False
    checks["predicates"] = {"ok": pred_ok, "worst": worst_name,
                            "err": round(worst_err, 3)}
    if not pred_ok:
        return {"ok": False,
                "reason": f"谓词 {worst_name} err={worst_err:.2f}",
                "checks": checks}

    # 3) 胶囊
    caps_v = check_capsules(M, capsules, rig_fast, collision_table,
                            baseline=baseline, lengths=lengths,
                            chain_skip=3, only=ONLY)
    checks["capsules"] = {"ok": not caps_v, "count": len(caps_v),
                          "worst": (caps_v[0] if caps_v else None)}
    if caps_v:
        c = caps_v[0]
        return {"ok": False,
                "reason": f"胶囊 {c['bone']}×{c['other']} 超 {c['over_mm']:.0f}mm",
                "checks": checks}

    return {"ok": True, "reason": "", "checks": checks}


