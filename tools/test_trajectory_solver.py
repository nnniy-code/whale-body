"""tools/test_trajectory_solver.py — 验证轨迹求解（直线 / 修正）。"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.bones import BoneMap
from core.collision import baseline_gaps, check_capsules, load_table
from core.fk import Rig
from core.pose_gen import build
from core.solver import MAIN21
from core.trajectory_solver import solve_path

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]
TARGETS = {
    "wave":      {"arm_lift": 135, "elbow_bend": 85, "arm_roll": 20},
    "point":     {"arm_lift": 90,  "elbow_bend": 10, "arm_swing": -35},
    "stretch":   {"arm_lift": 160, "elbow_bend": 15, "lean": -8},
    "pat_head":  {"arm_lift": 70,  "elbow_bend": 110},
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
    print(f"{'动作':<12} {'模式':<10} {'修正帧':>6} {'剩余违规':>8} {'耗时ms':>8}")
    print("-" * 52)
    fails = 0
    for name, patch in TARGETS.items():
        P_end = dict(P_start)
        P_end.update(patch)
        r = solve_path(P_start, P_end, n_frames=20, rig=rf, bm=bm, side="right",
                       anchors_cfg=anch, caps=caps, tab=tab, lengths=lengths,
                       base=base, only=ONLY)
        nbad = len(r.get("still_bad") or [])
        print(f"{name:<12} {r['mode']:<10} {len(r['repaired']):>6} {nbad:>8} "
              f"{r['cost_ms']:>8.1f}")
        if r["mode"] == "failed":
            fails += 1

    print()
    if fails:
        print(f"RESULT: FAIL ({fails} 个动作修不好)")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
