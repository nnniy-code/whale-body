"""tools/probe_leg_actions.py — 验证腿的动作（坐/跪/抬腿/蹲/踮脚）能出姿势。"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.bones import BoneMap
from core.collision import baseline_gaps, load_table
from core.fk import Rig
from core.library import Library
from core.runtime import Runtime
from core.solver import MAIN21

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]


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
    L = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, lengths=L, chain_skip=3, only=ONLY)
    rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                 collision_table=tab, lengths=L, capsule_baseline=base,
                 capsule_only=ONLY)

    cases = ["idle", "sit", "kneel", "rest", "lie_down", "crouch",
             "lift_leg_left", "lift_leg_right", "tiptoe"]
    print(f"{'族':<16} {'髋z':>6} {'左踝z':>7} {'右踝z':>7} {'误差':>6} {'说明'}")
    print("-" * 70)
    for name in cases:
        fam = fams.get(name)
        if not fam:
            print(f"{name:<16} {'—':>6} 族不存在")
            continue
        P = dict(fam.get("P_default") or {})
        try:
            b, d = rt.build_full(P, name, 0.0)
        except Exception as e:
            print(f"{name:<16} build 失败: {e}")
            continue
        M = rig.compute(b)
        hz = float(M["hips"][2, 3])
        lz = float(M["leftFoot"][2, 3])
        rz = float(M["rightFoot"][2, 3])
        err = max(d["err_mm"].values()) if d.get("err_mm") else -1
        fail = list((d.get("ik_fail") or {}).keys())
        note = f"IK失败{fail}" if fail else ("OK" if err < 5 else f"误差{err:.0f}mm")
        print(f"{name:<16} {hz:>6.3f} {lz:>7.3f} {rz:>7.3f} {err:>6.1f} {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
