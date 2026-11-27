"""tests/test_solver.py — P7 验收（CMA-ES + 三段搜索的第一段）。

怎么测：python tests/test_solver.py
预期现象：[solver] ALL PASS
调参位置：core/solver.py 的 sigma / budget，core/cmaes.py 的超参
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bones import BoneMap  # noqa: E402
from core.cmaes import cma_es  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.solver import P_KEYS, evaluate_vec, fast_rig, solve, vec_to_P  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    anchors = json.load(open(os.path.join(ROOT, "config", "anchors.json"), encoding="utf-8"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]

    print("[1] CMA-ES 基本功（3 维二次型）")
    def quad(x):
        return float(((x - np.array([0.3, -0.2, 0.1])) ** 2).sum())
    bx, bf, ev = cma_es(quad, np.zeros(3), 0.5, np.full(3, -2.0), np.full(3, 2.0), max_evals=800, seed=1)
    check("收敛到 <1e-4", bf < 1e-4, f"f={bf:.2e} evals={ev}")
    check("解接近真值", np.linalg.norm(bx - np.array([0.3, -0.2, 0.1])) < 0.02,
          str(np.round(bx, 3)))

    print("[2] 单次求值速度")
    P0 = fams["wave"]["P_default"]
    vec = np.array([P0[k] for k in P_KEYS])
    preds = [{"name": "hand_above_head", "args": {"side": "right"}}]
    t0 = time.perf_counter()
    rig_f = fast_rig(rig)
    for _ in range(200):
        evaluate_vec(vec, rig_f, bm, preds, anchors)
    ms = (time.perf_counter() - t0) / 200 * 1000
    check("< 0.5 ms", ms < 0.5, f"{ms:.4f}ms")

    print("[3] 求解：手抬到头顶（从 wave 默认出发）")
    res = solve(rig, bm, fams["wave"], preds, anchors, budget=3000, seed=2)
    print(f"    converged={res['converged']} evals={res['evals']} calc={res['calc_ms']}ms")
    print(f"    arm_lift={res['params']['P']['arm_lift']:.1f} elbow={res['params']['P']['elbow_bend']:.1f}")
    check("收敛", res["converged"], str(res["violations"])[:120])
    check("手确实举高了（判效果不判参数）",
          res["converged"] and not res["violations"],
          f"arm_lift={res['params']['P']['arm_lift']:.1f}")

    print("[4] 预算与耗时")
    check("evals ≤ 预算", res["evals"] <= 3000, str(res["evals"]))
    check("calc < 1s", res["calc_ms"] < 1000, f"{res['calc_ms']}ms")

    print("[5] 不可达 → 明确失败（D5）")
    impossible = [{"name": "hand_near_anchor",
                   "args": {"side": "right", "anchor": "tail_tip", "tol_mm": 5}}]
    res2 = solve(rig, bm, fams["wave"], impossible, anchors, budget=1500, seed=3)
    check("不收敛", not res2["converged"])
    check("带违规定位", len(res2["violations"]) > 0, str(len(res2["violations"])))
    if res2["violations"]:
        v0 = res2["violations"][0]
        check("有 detail", bool(v0.get("detail")), str(v0.get("detail"))[:120])

    print("[6] 结果可复现（同 seed）")
    a = solve(rig, bm, fams["wave"], preds, anchors, budget=800, seed=7)
    b = solve(rig, bm, fams["wave"], preds, anchors, budget=800, seed=7)
    da = abs(a["params"]["P"]["arm_lift"] - b["params"]["P"]["arm_lift"])
    check("两次一致", da < 1e-9, f"{da:.2e}")

    print("[7] 跨族（pat_head）")
    res3 = solve(rig, bm, fams["pat_head"],
                 [{"name": "hand_near_anchor",
                   "args": {"side": "right", "anchor": "head_top", "tol_mm": 90}}],
                 anchors, budget=3000, seed=4)
    check("收敛", res3["converged"], str(res3["violations"])[:120])

    print("[8] vec ↔ dict 往返")
    v = np.arange(len(P_KEYS), dtype=float)
    d = vec_to_P(v)
    v2 = np.array([d[k] for k in P_KEYS])
    check("往返一致", np.allclose(v, v2))
    check(f"P 维度 = {len(P_KEYS)}（v2 扩了 arm_roll / wrist_bend）",
          len(P_KEYS) == 12, str(len(P_KEYS)))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())






