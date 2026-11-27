"""tools/sweep_targets.py — 扫目标组合，找「求解器能解到哪」的边界。

怎么测：python tools/sweep_targets.py
用途：不是调参，是**测求解器的可行域边界**——哪几组约束能同时满足、哪几组冲突。
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import pose_gen  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.solver import MAIN21, make_frame, solve  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    anch = json.load(open(os.path.join(ROOT, "config", "anchors.json"), encoding="utf-8"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    caps = json.load(open(os.path.join(ROOT, "config", "capsules.json"), encoding="utf-8"))["bones"]
    tab = load_table()
    bmj = json.load(open(os.path.join(ROOT, "config", "bones_map.json"), encoding="utf-8"))["bones"]
    lengths = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    rf = rig.subset(MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"])
    base = baseline_gaps(rf.compute(zero), caps, rf, lengths, chain_skip=3, only=ONLY)

    print(f"{'out_mm':>7} {'elbow_tol':>10} {'elbow_d':>8} {'conv':>5} {'evals':>6} {'hand_x':>8} {'hand_z':>8}")
    rows = []
    for out_mm in (100, 130, 160, 190):
        for el_tol in (50, 80):
            preds = [
                {"name": "hand_near_anchor", "args": {
                    "side": "right", "anchor": "head_top",
                    "dist_mm": 100, "tol_mm": 30}},
                {"name": "elbow_near_anchor", "args": {
                    "side": "right", "anchor": "chest",
                    "dist_mm": 220, "tol_mm": el_tol}},
                {"name": "arm_not_crossed", "args": {"side": "right"}},
                {"name": "hand_outside_body", "args": {"side": "right", "min_mm": out_mm}},
            ]
            res = solve(rig, bm, fams["wave"], preds, anch, budget=1200, seed=7,
                        active_side="right", capsules=caps, collision_table=tab,
                        lengths=lengths, capsule_baseline=base, capsule_only=ONLY)
            hx = hz = float("nan")
            de = float("nan")
            if res["converged"]:
                P = dict(fams["wave"]["P_default"])
                P.update(res["params"]["P"])
                M = rf.compute(pose_gen.build(P, rf, bm, active_side="right"))
                f = make_frame(M, anch)
                hx = float(M["rightHand"][0, 3]) * 1000
                hz = float(M["rightHand"][2, 3]) * 1000
                de = float(np.linalg.norm(f.p("rightLowerArm") - f.anchor("chest")) * 1000)
            print(f"{out_mm:7d} {el_tol:10d} {de:8.0f} {str(res['converged']):>5} "
                  f"{res['evals']:6d} {hx:8.0f} {hz:8.0f}")
            rows.append({"outside_mm": out_mm, "elbow_tol": el_tol,
                         "converged": res["converged"], "evals": res["evals"],
                         "hand_x_mm": None if not res["converged"] else round(hx, 1)})

    ok = [r for r in rows if r["converged"]]
    print(f"\n可行 {len(ok)}/{len(rows)} 组")
    if ok:
        best = min(ok, key=lambda r: r["evals"])
        print(f"最省：outside={best['outside_mm']} elbow_tol={best['elbow_tol']} "
              f"evals={best['evals']} hand_x={best['hand_x_mm']}")
    out = os.path.join(ROOT, "docs", "target_feasibility.json")
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("WROTE", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
