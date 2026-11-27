"""tools/compare_solver.py — 用「手工编排的关键帧」当标准答案，量求解器差在哪。

怎么测：python tools/compare_solver.py
用途：**不是拿关键帧当动作库**，而是当求解器的校准基准（用户 2026-11-24 明确）。

流程：
  1. 参考：gestures 的关节角关键帧（人工确认正确的姿势）
  2. 从参考姿态**反推谓词**（自动标注）
  3. 用求解器解同一组谓词
  4. 对比手/肘位置差、以及每个谓词的残差
  5. 报告「求解器差在哪」——是参数化不够、范围不够、还是谓词冲突
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.gestures import sample_joint  # noqa: E402
from core.predicates import Frame, evaluate_series  # noqa: E402
from core.solver import MAIN21, fast_rig, make_frame, solve  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]


def measure(M, rig) -> dict:
    """从姿态量出可比较的关键指标（毫米）。"""
    el = M["rightLowerArm"][0:3, 3]
    hd = M["rightHand"][0:3, 3]
    sh = M["rightShoulder"][0:3, 3]
    v1 = sh - el
    v2 = hd - el
    c = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-12))
    ang = float(np.degrees(np.arccos(max(-1.0, min(1.0, c)))))
    return {
        "hand": [round(float(x), 3) for x in hd],
        "elbow": [round(float(x), 3) for x in el],
        "shoulder": [round(float(x), 3) for x in sh],
        "hand_z_mm": round(hd[2] * 1000, 1),
        "elbow_z_mm": round(el[2] * 1000, 1),
        "hand_x_mm": round(hd[0] * 1000, 1),
        "elbow_angle_deg": round(ang, 1),
    }


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    rf = fast_rig(rig)
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    anch = json.load(open(os.path.join(ROOT, "config", "anchors.json"), encoding="utf-8"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    caps = json.load(open(os.path.join(ROOT, "config", "capsules.json"), encoding="utf-8"))["bones"]
    tab = load_table()
    bmj = json.load(open(os.path.join(ROOT, "config", "bones_map.json"), encoding="utf-8"))["bones"]
    lengths = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, lengths, chain_skip=3, only=ONLY)

    # ---- 1. 参考（人工确认正确的关键帧）----
    ref_M = sample_joint("wave_joint", 0.0, rig, bm)["M"]
    ref = measure(ref_M, rig)
    print("=== 参考（关键帧）===")
    for k in ("hand_x_mm", "hand_z_mm", "elbow_z_mm", "elbow_angle_deg"):
        print(f"  {k:18s} {ref[k]}")

    # ---- 2. 从参考反推谓词（自动标注）----
    # 用参考姿态的实际值当目标，容差给紧一点
    preds = [
        {"name": "hand_above_shoulder", "args": {"side": "right"}},
        {"name": "hand_elbow_gap", "args": {"side": "right", "min_mm": 85}},
        {"name": "elbow_angle", "args": {"side": "right",
                                         "min_deg": max(20, ref["elbow_angle_deg"] - 20),
                                         "max_deg": min(175, ref["elbow_angle_deg"] + 20)}},
        {"name": "arm_not_crossed", "args": {"side": "right"}},
    ]
    print("\n=== 反推的谓词 ===")
    for p in preds:
        print(f"  {p['name']} {p['args']}")

    # ---- 3. 求解 ----
    res = solve(rig, bm, fams["wave"], preds, anch, budget=3000, seed=3,
                active_side="right", capsules=caps, collision_table=tab,
                lengths=lengths, capsule_baseline=base, capsule_only=ONLY)
    print(f"\n=== 求解器 ===")
    print(f"  converged={res['converged']}  evals={res['evals']}  {res['calc_ms']}ms")
    if res["violations"]:
        for v in res["violations"][:4]:
            print(f"  ✗ {v['predicate']}  err={v['err']}  {v.get('detail')}")
    if res["capsules"]:
        for c in res["capsules"][:3]:
            print(f"  ✗ 胶囊 {c['bone']}×{c['other']} 超 {c['over_mm']}mm")

    sol_M = None
    if res["params"]["P"]:
        from core import pose_gen
        P = dict(fams["wave"]["P_default"])
        P.update(res["params"]["P"])
        sol_M = rf.compute(pose_gen.build(P, rf, bm, active_side="right"))
        sol = measure(sol_M, rig)
        print("\n=== 差距（求解器 - 参考）===")
        for k in ("hand_x_mm", "hand_z_mm", "elbow_z_mm", "elbow_angle_deg"):
            d = sol[k] - ref[k]
            flag = "OK" if abs(d) < 40 else ("偏" if abs(d) < 100 else "差得远")
            print(f"  {k:18s} 参考={ref[k]:8.1f}  解={sol[k]:8.1f}  Δ={d:+8.1f}  {flag}")
        print("\n=== 诊断 ===")
        if not res["converged"]:
            print("  求解没收敛 → 谓词在 P 的可行域里无解（参数化不够，或范围太窄）")
        elif abs(sol["hand_z_mm"] - ref["hand_z_mm"]) > 60:
            print("  收敛了但手高差得远 → P 的高度分辨率不够，或 arm_lift 范围限制")
        elif abs(sol["elbow_angle_deg"] - ref["elbow_angle_deg"]) > 30:
            print("  收敛了但肘角差 → elbow_bend → 实际夹角 的映射非线性")
        else:
            print("  接近参考 → 这个动作求解器能自己做，可以脱离关键帧")

    out = os.path.join(ROOT, "docs", "solver_vs_ref.json")
    json.dump({"reference": ref, "predicates": preds, "converged": res["converged"],
               "evals": res["evals"], "violations": res["violations"]},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\nWROTE", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


