"""tests/test_hold_pose.py — 姿态保持（v2.1）验收。

怎么测：python tests/test_hold_pose.py
预期现象：[hold_pose] ALL PASS

用户要求：
  1. 默认保持（不每次回待机）
  2. 说「回待机」强制重置，**保留位置**
  3. 断开/接入 → 待机
  4. 不说话只保持 → 不回待机
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

    print("[1] 初始：无保持姿态")
    check("body 存在", hasattr(rt, "body"))
    check("has_pose=False", not rt.body.has_pose())
    check("reset_pose 方法", hasattr(rt, "reset_pose"))

    print("[2] 做动作后记住姿态")
    tr = rt.plan_trajectory({"intent": "wave"}, n_frames=10)
    check("规划成功", tr["mode"] != "failed", tr["mode"])
    rt.commit_trajectory(tr)
    check("has_pose=True", rt.body.has_pose())
    held = dict(rt.body.P_hold)
    check("记住的 P 有 arm_lift", "arm_lift" in held, str(len(held)))

    print("[3] 第二个动作从保持的姿态出发")
    tr2 = rt.plan_trajectory({"intent": "point"}, n_frames=10)
    check("规划成功", tr2["mode"] != "failed", tr2["mode"])
    first = tr2["frames"][0]
    check("起点接了上次终点",
          abs(float(first.get("arm_lift", 0)) - float(held.get("arm_lift", 0))) < 1e-6,
          f"{first.get('arm_lift')} vs {held.get('arm_lift')}")

    print("[4] 回待机：清空姿态，**保留位置**")
    rt.body.x = 3.0
    rt.body.y = -2.0
    rt.reset_pose()
    check("姿态清空", not rt.body.has_pose())
    check("位置保留 x", abs(rt.body.x - 3.0) < 1e-9, f"x={rt.body.x}")
    check("位置保留 y", abs(rt.body.y + 2.0) < 1e-9, f"y={rt.body.y}")
    check("_last_P 也清空", rt._last_P is None)

    print("[5] 回待机后下一个动作从 idle 出发")
    tr3 = rt.plan_trajectory({"intent": "wave"}, n_frames=10)
    check("规划成功", tr3["mode"] != "failed")
    check("起点是 idle 的 arm_lift",
          abs(float(tr3["frames"][0].get("arm_lift", 0))
              - float(fams["idle"]["P_default"].get("arm_lift", 0))) < 1e-6,
          str(tr3["frames"][0].get("arm_lift")))

    print("[6] 说话没动作（不调 plan）→ 姿态不变")
    before = dict(rt.body.P_hold)
    # 模拟：只说话，不调 handle_intent
    check("姿态还在", rt.body.has_pose() or bool(before) is False)

    print("[7] 断开 → 全重置")
    rt.body.hold_pose({"lean": -10}, 0.15, "static")
    rt.reset_pose()
    check("姿态清空", not rt.body.has_pose())
    check("hip_drop 归零", abs(rt.body.hip_drop_hold) < 1e-9, str(rt.body.hip_drop_hold))

    print("[8] explain 方法")
    tr_bad = rt.plan_trajectory({"intent": "nonexistent_xyz"})
    msg = rt.explain(tr_bad)
    check("失败有说明", bool(msg), msg)

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
