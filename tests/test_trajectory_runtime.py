"""tests/test_trajectory_runtime.py — 轨迹求解接入 runtime 的验收。

怎么测：python tests/test_trajectory_runtime.py
预期现象：[traj_runtime] ALL PASS
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


def make_rt(rig, rf, bm, anch, fams, caps, tab, lengths, base):
    return Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                   collision_table=tab, lengths=lengths,
                   capsule_baseline=base, capsule_only=ONLY)


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
    rt = make_rt(rig, rf, bm, anch, fams, caps, tab, lengths, base)

    print("[1] 有 plan_trajectory / commit_trajectory / trajectory_basis")
    check("三个方法都在",
          all(hasattr(rt, m) for m in ("plan_trajectory", "commit_trajectory", "trajectory_basis")))

    print("[2] 挥手：规划出 20 帧，全程合法")
    tr = rt.plan_trajectory({"intent": "wave"}, n_frames=20)
    print(f"    mode={tr['mode']} frames={len(tr['frames'])} "
          f"cost={tr['cost_ms']}ms repaired={len(tr.get('repaired') or [])}")
    check("有 20 帧", len(tr["frames"]) == 20, str(len(tr["frames"])))
    check("模式不是 failed", tr["mode"] != "failed", tr["mode"])
    check("无剩余违规", not tr.get("still_bad"), str(tr.get("still_bad")))
    check("有终点", tr.get("play_P") is not None)

    print("[3] 帧序列形状：起点 → 终点单调靠近")
    first, last = tr["frames"][0], tr["frames"][-1]
    check("首帧 = 起点 P",
          abs(float(first.get("arm_lift", 0)) - float(fams["idle"]["P_default"].get("arm_lift", 0))) < 5.0,
          f"{first.get('arm_lift')}")
    check("末帧 = 终点 P",
          abs(float(last.get("arm_lift", 0)) - float(tr["play_P"].get("arm_lift", 0))) < 1e-6,
          f"{last.get('arm_lift')} vs {tr['play_P'].get('arm_lift')}")

    print("[4] trajectory_basis 能生成 20 个 basis")
    side = (tr["entry"].metrics or {}).get("side")
    bases = rt.trajectory_basis(tr, "wave", 0.0, active_side=side)
    check("20 个 basis", len(bases) == 20, str(len(bases)))
    check("都是 dict", all(isinstance(b, dict) for b in bases))

    print("[5] commit_trajectory 记住终点")
    rt.commit_trajectory(tr)
    check("_last_P 被设置", rt._last_P is not None)

    print("[6] 第二个动作从上次终点出发")
    tr2 = rt.plan_trajectory({"intent": "point"}, n_frames=20)
    check("第二个动作也规划成功", tr2["mode"] != "failed", tr2["mode"])
    check("起点接了上次终点",
          abs(float(tr2["frames"][0].get("arm_lift", 0))
              - float(tr["play_P"].get("arm_lift", 0))) < 1e-6,
          f"{tr2['frames'][0].get('arm_lift')} vs {tr['play_P'].get('arm_lift')}")

    print("[7] 没有族时返回 no_entry")
    tr3 = rt.plan_trajectory({"intent": "nonexistent_xyz"})
    check("mode = no_entry", tr3["mode"] == "no_entry", tr3["mode"])
    check("frames 空", tr3["frames"] == [])

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
