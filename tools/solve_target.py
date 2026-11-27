"""tools/solve_target.py — 用「锚点 + 距离」当目标，让求解器解，量它到了哪。

怎么测：python tools/solve_target.py
**不需要参考姿态**——目标是身体自己的锚点坐标，不依赖任何人工调的姿势。
这样评价标准是独立的，不会变成「自己考自己」。
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

    # ---- 目标：手离头顶 100mm、肘离胸口 220mm、不跨中线 ----
    TARGET_HAND = 100.0
    TARGET_ELBOW = 220.0
    preds = [
        {"name": "hand_near_anchor", "args": {
            "side": "right", "anchor": "head_top",
            "dist_mm": TARGET_HAND, "tol_mm": 30}},
        {"name": "elbow_near_anchor", "args": {
            "side": "right", "anchor": "chest",
            "dist_mm": TARGET_ELBOW, "tol_mm": 50}},
        {"name": "arm_not_crossed", "args": {"side": "right"}},
        {"name": "hand_outside_body", "args": {"side": "right", "min_mm": 170}},
    ]
    print("=== 目标（锚点 + 距离，无参考姿态）===")
    for p in preds:
        print(f"  {p['name']} {p['args']}")

    res = solve(rig, bm, fams["wave"], preds, anch, budget=3000, seed=5,
                active_side="right", capsules=caps, collision_table=tab,
                lengths=lengths, capsule_baseline=base, capsule_only=ONLY)
    print(f"\n=== 求解 ===\n  converged={res['converged']}  evals={res['evals']}  {res['calc_ms']}ms")
    for v in res["violations"][:4]:
        print(f"  ✗ {v['predicate']} err={v['err']} {v.get('detail')}")
    for c in (res["capsules"] or [])[:3]:
        print(f"  ✗ 胶囊 {c['bone']}×{c['other']} 超 {c['over_mm']}mm")

    # ---- 量实际到达 ----
    if res["converged"]:
        P = dict(fams["wave"]["P_default"])
        P.update(res["params"]["P"])
        M = rf.compute(pose_gen.build(P, rf, bm, active_side="right"))
        f = make_frame(M, anch)
        dh = float(np.linalg.norm(f.p("rightHand") - f.anchor("head_top")) * 1000)
        de = float(np.linalg.norm(f.p("rightLowerArm") - f.anchor("chest")) * 1000)
        hz = float(M["rightHand"][2, 3]) * 1000
        ez = float(M["rightLowerArm"][2, 3]) * 1000
        hx = float(M["rightHand"][0, 3]) * 1000
        print("\n=== 实际到达 ===")
        print(f"  手→头顶   目标 {TARGET_HAND:6.0f}  实际 {dh:6.0f}  Δ={dh-TARGET_HAND:+6.0f}mm")
        print(f"  肘→胸口   目标 {TARGET_ELBOW:6.0f}  实际 {de:6.0f}  Δ={de-TARGET_ELBOW:+6.0f}mm")
        print(f"  手 z={hz:.0f}mm  x={hx:.0f}mm    肘 z={ez:.0f}mm")
        print("\n=== P 用了哪些维度 ===")
        for k in ("arm_lift", "arm_roll", "elbow_bend", "wrist_bend", "arm_swing"):
            print(f"  {k:12s} {res['params']['P'][k]:+7.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

