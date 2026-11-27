"""tools/simulate_hold_usage.py — 模仿用户用法：连续输入 + 保持 + 回待机。

怎么测：python tools/simulate_hold_usage.py
"""

from __future__ import annotations

import json
import os
import sys
import time

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


def make_rt(rig, bm, anch, fams, caps, tab, L, base):
    return Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                   collision_table=tab, lengths=L, capsule_baseline=base,
                   capsule_only=ONLY)


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
    rt = make_rt(rig, bm, anch, fams, caps, tab, L, base)

    # 用户模拟：连续 6 轮
    turns = [
        ("挥挥手给我看", {"intent": "wave"}),
        ("再挥一次", {"intent": "wave"}),
        ("伸个懒腰", {"intent": "stretch"}),
        ("（闲聊，她只说话）", None),              # 不给 act
        ("回待机", {"intent": "reset"}),
        ("挥挥手", {"intent": "wave"}),
    ]

    print(f"{'轮':>2} {'用户':<16} {'动作':<10} {'模式':>9} {'帧':>3} {'耗时':>6} "
          f"{'保持姿态':>8} {'位置':>12}")
    print("-" * 90)

    for i, (user_text, act) in enumerate(turns, 1):
        if act is None:
            # 闲聊：不调 handle_intent，姿态应该保持不变
            print(f"{i:>2} {user_text:<16} {'—':<10} {'—':>9} {'—':>3} {'—':>6} "
                  f"{'有' if rt.body.has_pose() else '无':>8} "
                  f"({rt.body.x:.1f},{rt.body.y:.1f})")
            continue

        tr = rt.plan_trajectory(act, n_frames=15)
        mode = tr.get("mode", "?")
        nf = len(tr.get("frames") or [])
        cost = tr.get("cost_ms", 0)
        msg = tr.get("message") or ""

        if tr.get("entry") and mode != "failed":
            rt.commit_trajectory(tr)

        hold = "有" if rt.body.has_pose() else "无"
        print(f"{i:>2} {user_text:<16} {act['intent']:<10} {mode:>9} {nf:>3} "
              f"{cost:>6.0f} {hold:>8} ({rt.body.x:.1f},{rt.body.y:.1f})")
        if msg:
            print(f"    → {msg}")

    print("\n=== 最终状态 ===")
    print(f"  body      : {rt.body}")
    print(f"  P_hold    : {len(rt.body.P_hold)} 个参数")
    print(f"  位置      : ({rt.body.x:.2f}, {rt.body.y:.2f})")

    # 检查断线重置
    print("\n=== 模拟断开 30 秒 ===")
    rt.body.x = 2.0
    rt.reset_pose()
    print(f"  重置后 has_pose = {rt.body.has_pose()}")
    print(f"  位置保留 = ({rt.body.x:.1f}, {rt.body.y:.1f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
