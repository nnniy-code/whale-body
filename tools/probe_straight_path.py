"""tools/probe_straight_path.py — 直线插值到底会不会穿模/超范围？

怎么测：python tools/probe_straight_path.py
回答：起点→终点直线插值 20 帧，有多少帧违规。
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
from core.collision import baseline_gaps, check_capsules, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.predicates import evaluate_one  # noqa: E402
from core.solver import MAIN21, make_frame  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]

# 几个典型动作的终点（手写的 P，模拟求解结果）
TARGETS = {
    "wave":      {"arm_lift": 135, "elbow_bend": 85, "arm_roll": 20},
    "point":     {"arm_lift": 90,  "elbow_bend": 10, "arm_swing": -35},
    "stretch":   {"arm_lift": 160, "elbow_bend": 15, "lean": -8},
    "pat_head":  {"arm_lift": 70,  "elbow_bend": 110},
    "sit":       {"lean": 12, "head_nod": 5},
    "lie_down":  {"lean": -22, "arm_lift": 10, "head_nod": 12},
    "tail_drop": {"tail_curl": -18, "head_nod": 8},
    "head_tilt": {"head_tilt": 18},
}


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    rf = rig.subset(ONLY)
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    anch = json.load(open(os.path.join(ROOT, "config", "anchors.json"), encoding="utf-8"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    caps = json.load(open(os.path.join(ROOT, "config", "capsules.json"), encoding="utf-8"))["bones"]
    tab = load_table()
    bmj = json.load(open(os.path.join(ROOT, "config", "bones_map.json"), encoding="utf-8"))["bones"]
    lengths = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, lengths, chain_skip=3, only=ONLY)

    P_start = dict(fams["idle"]["P_default"])

    hdr = f"{'动作':<12} {'帧数':>4} {'胶囊违规':>8} {'范围违规':>8} {'判定':>6}"
    print(hdr)
    print("-" * len(hdr))
    n_fail = 0
    for name, patch in TARGETS.items():
        P_end = dict(P_start)
        P_end.update(patch)
        bad_cap, bad_rng = 0, 0
        for i in range(20):
            s = i / 19
            P = {k: float(P_start.get(k, 0.0)) * (1 - s) + float(P_end.get(k, 0.0)) * s
                 for k in set(P_start) | set(P_end)}
            M = rf.compute(pose_gen.build(P, rf, bm, active_side="right"))
            cv = check_capsules(M, caps, rf, tab, baseline=base, lengths=lengths,
                                chain_skip=3, only=ONLY)
            if cv:
                bad_cap += 1
            f = make_frame(M, anch)
            hi_e, _ = evaluate_one("no_hyperextension", f, side="right", min_deg=20)
            hi_l, _ = evaluate_one("no_hyperextension", f, side="left", min_deg=20)
            if hi_e >= 1.0 or hi_l >= 1.0:
                bad_rng += 1
        verdict = "OK" if bad_cap == 0 and bad_rng == 0 else "撞"
        if verdict == "撞":
            n_fail += 1
        print(f"{name:<12} {20:>4} {bad_cap:>8} {bad_rng:>8} {verdict:>6}")

    print(f"\n直线插值会出问题的动作：{n_fail} / {len(TARGETS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
