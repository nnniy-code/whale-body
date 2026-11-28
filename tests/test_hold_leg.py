"""tests/test_hold_leg.py — 「蹲下后举手不站起来」验收（用户 2026-11-28 报的 bug）。

怎么测：python tests/test_hold_leg.py
预期现象：[hold_leg] ALL PASS
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


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

    def hip_z():
        b, _d = rt.build_full(rt.body.P_hold or dict(fams["idle"]["P_default"]),
                              "idle", 0.0)
        return float(rig.subset(ONLY).compute(b)["hips"][2, 3])

    print("[1] 蹲下 → 髋降低")
    tr = rt.plan_trajectory({"intent": "crouch"}, n_frames=10)
    check("crouch 规划成功", tr["mode"] not in ("failed", "no_entry"), tr["mode"])
    rt.commit_trajectory(tr)
    crouch_hip = rt.body.hip_drop_hold
    print(f"    保持的 hip_drop = {crouch_hip:.3f}")
    check("hip_drop 被记住 (>0.1)", crouch_hip > 0.1, f"{crouch_hip:.3f}")

    print("[2] 蹲着举手 → 腿**不能**站起")
    tr2 = rt.plan_trajectory({"intent": "wave"}, n_frames=10)
    check("wave 规划成功", tr2["mode"] not in ("failed", "no_entry"), tr2["mode"])
    # 关键：build_full 时的 foot_target 应该继承 hip_drop
    ft, _feet = rt.foot_target_for("wave")
    held = float(ft.hip_drop())
    print(f"    wave 时的 hip_drop = {held:.3f}（应等于 {crouch_hip:.3f}）")
    check("举手时腿还蹲着", abs(held - crouch_hip) < 1e-6, f"{held:.3f} vs {crouch_hip:.3f}")

    print("[3] 真正装配后髋的高度")
    b, _d = rt.build_full(dict(tr2.get("play_P") or {}), "wave", 0.0)
    M = rf.compute(b)
    wave_hip = float(M["hips"][2, 3])
    print(f"    举手时 hips z = {wave_hip:.3f}")
    check("髋仍明显低于站立 (0.529)", wave_hip < 0.42, f"{wave_hip:.3f}")

    print("[4] 回待机 → 腿站起来")
    rt.handle_intent({"intent": "reset"})
    ft2, _ = rt.foot_target_for("wave")
    check("reset 后 hip_drop=0", abs(float(ft2.hip_drop())) < 1e-9,
          f"{float(ft2.hip_drop()):.3f}")
    check("has_pose=False", not rt.body.has_pose())

    print("[5] 走路不继承蹲姿")
    rt.body.hold_pose({"lean": 0}, 0.20, {})     # 假装还蹲着
    ft3, _ = rt.foot_target_for("walk")
    check("walk 用 GaitTarget（不继承蹲）",
          type(ft3).__name__ == "GaitTarget", type(ft3).__name__)

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
