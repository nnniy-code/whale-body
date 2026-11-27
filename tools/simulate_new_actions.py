"""tools/simulate_new_actions.py — 用「她没有的动作」模拟真实用法。

怎么测：python tools/simulate_new_actions.py
模拟：LLM 输出 {"intent": "free", "predicates": [...]} → 求解 → 规划轨迹
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
from core.library import Library  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]

# 18 个族里没有的动作，用谓词描述（模拟 LLM 的 free 输出）
NEW_ACTIONS = {
    "摸耳朵": [
        {"name": "hand_near_anchor", "args": {"side": "right", "anchor": "head", "dist_mm": 120}},
        {"name": "head_tilt", "args": {"deg": 12, "tol_deg": 8}},
    ],
    "打哈欠": [
        {"name": "hand_above_head", "args": {"side": "right"}},
        {"name": "torso_lean", "args": {"deg": -12, "tol_deg": 10}},
        {"name": "head_nod", "args": {"deg": -10, "tol_deg": 8}},
    ],
    "摊手": [
        # hand_below_shoulder 不存在，用 hand_outside_body + 肘角代替
        {"name": "hand_outside_body", "args": {"side": "right", "min_mm": 40}},
        {"name": "hand_outside_body", "args": {"side": "left", "min_mm": 40}},
        {"name": "elbow_angle", "args": {"side": "right", "min_deg": 20, "max_deg": 90}},
        {"name": "elbow_angle", "args": {"side": "left", "min_deg": 20, "max_deg": 90}},
    ],
    "抱膝": [
        {"name": "torso_lean", "args": {"deg": -20, "tol_deg": 18}},
        {"name": "hand_below_waist", "args": {"side": "right", "margin_mm": -40}},
        {"name": "elbow_angle", "args": {"side": "right", "min_deg": 100, "max_deg": 150}},
    ],
    "歪头想事": [
        {"name": "head_tilt", "args": {"deg": 22, "tol_deg": 8}},
        {"name": "head_yaw", "args": {"deg": 12, "tol_deg": 10}},
        {"name": "torso_upright", "args": {"max_deg": 20}},
    ],
    "拍胸口": [
        {"name": "hand_near_anchor", "args": {"side": "right", "anchor": "chest", "dist_mm": 130}},
        {"name": "elbow_angle", "args": {"side": "right", "min_deg": 80, "max_deg": 130}},
    ],
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

    hdr = f"{'动作':<10} {'求解':>4} {'模式':>9} {'帧数':>4} {'违规':>4} {'耗时':>7} {'z值':>6}"
    print(hdr)
    print("-" * len(hdr))
    n_ok = 0
    for name, preds in NEW_ACTIONS.items():
        rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                     collision_table=tab, lengths=lengths,
                     capsule_baseline=base, capsule_only=ONLY)
        act = {"intent": "free", "predicates": preds, "use_llm_predicates": True}
        tr = rt.plan_trajectory(act, n_frames=20)
        conv = "✓" if tr.get("entry") else "✗"
        mode = tr.get("mode", "-")
        nf = len(tr.get("frames") or [])
        bad = len(tr.get("still_bad") or [])
        cost = tr.get("cost_ms", 0)
        z = (tr.get("entry").metrics or {}).get("z") if tr.get("entry") else None
        print(f"{name:<10} {conv:>4} {mode:>9} {nf:>4} {bad:>4} {cost:>7.0f} "
              f"{(round(float(z),3) if z else '-'):>6}")
        if tr.get("entry") and mode != "failed":
            n_ok += 1
        else:
            gap = tr.get("gaps") or []
            for g in gap:
                print(f"            gap: {g.get('kind')} {g.get('detail')}")

    print(f"\n成功的：{n_ok} / {len(NEW_ACTIONS)}")
    return 0 if n_ok == len(NEW_ACTIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())




