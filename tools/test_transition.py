"""tools/test_transition.py — 验证运行时的动作过渡（200ms 惯性化）。

怎么测：python tools/test_transition.py
预期现象：手位置从走路位置平滑滑到挥手位置，200ms 内，无瞬移
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import pose_gen  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402

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
    lengths = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, lengths, chain_skip=3, only=ONLY)

    rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                 collision_table=tab, lengths=lengths,
                 capsule_baseline=base, capsule_only=ONLY)

    P_walk = dict(fams["walk"]["P_default"])
    P_wave = dict(fams["wave"]["P_default"])
    P_wave["arm_lift"] = 95
    P_wave["elbow_bend"] = 80

    b_walk = pose_gen.build(P_walk, rig, bm, active_side=None)
    b_wave = pose_gen.build(P_wave, rig, bm, active_side="right")

    print("[1] 第一帧直接落位（不淡入）")
    t0 = time.perf_counter()
    rt.transition_to(b_walk, now=t0)
    b = rt.current_basis(now=t0)
    M = rf.compute(b)
    h_start = M["rightHand"][0:3, 3].copy()
    print(f"    起点 hand=({h_start[0]:.3f}, {h_start[2]:.3f})")

    print("[2] 切到挥手：200ms 内平滑过渡")
    rt.transition_to(b_wave, now=t0)
    prev = h_start.copy()
    worst = 0.0
    for i in range(1, 41):
        now = t0 + i * 0.005        # 每 5ms 采样，共 200ms
        M = rf.compute(rt.current_basis(now=now))
        h = M["rightHand"][0:3, 3]
        step = float(np.linalg.norm(h - prev))
        worst = max(worst, step)
        prev = h.copy()
        if i in (1, 10, 20, 30, 40):
            print(f"    t={i*5:3d}ms  hand_z={h[2]:.3f}  step={step*1000:5.1f}mm")
    print(f"    最大单步 {worst*1000:.1f}mm（瞬移会是几百 mm）")

    print("[3] 过渡结束停在目标姿态")
    M2 = rf.compute(rt.current_basis(now=t0 + 0.5))
    h_end = M2["rightHand"][0:3, 3]
    print(f"    终点 hand=({h_end[0]:.3f}, {h_end[2]:.3f})")
    ok = worst < 0.06
    print(f"\n{'PASS' if ok else 'FAIL'}: 最大单步 {worst*1000:.1f}mm < 60mm")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
