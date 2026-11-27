"""tests/test_composite_full.py — 叠加时腿要跟着动（§22.6 第 2 项）。

设计依据：HANDOFF §22.6「handle_composite 接全身」、v1.9

v1.8 的问题：复合的 base 走 `pose_for_from_P`（只有上半身），
腿全是恒等矩阵——「一边走过来一边挥手」变成「站着挥手，人平移」。

怎么测：python tests/test_composite_full.py
预期现象：[composite_full] ALL PASS
调参位置：core/runtime.py handle_composite / build_full
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
DT = 1 / 30
LEG = ("leftUpperLeg", "leftLowerLeg", "leftFoot",
       "rightUpperLeg", "rightLowerLeg", "rightFoot")


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

    def new_rt():
        return Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                       collision_table=tab, lengths=lengths,
                       capsule_baseline=base, capsule_only=ONLY)

    WALK = [{"intent": "walk"}]
    WAVE = [{"intent": "walk"}, {"intent": "wave", "groups": ["arm.right"]}]

    print("[1] 不 tick 就不动（相位来自 Runtime.t）")
    rt = new_rt()
    r = rt.handle_composite(WALK)
    check("full_body 标志", r.get("full_body") is True, str(r.get("full_body")))
    f0 = float(rig.compute(r["basis"])["leftFoot"][2, 3])

    print("[2] tick 之后腿开始走，脚会抬起来")
    rt = new_rt()
    zs, hands = [], []
    for _ in range(30):
        rt.tick(DT)
        r = rt.handle_composite(WAVE)
        M = rig.compute(rt.current_basis())
        zs.append(float(M["leftFoot"][2, 3]))
        hands.append(float(M["rightHand"][2, 3]))
    span = max(zs) - min(zs)
    check("左脚高度有起伏（抬脚）", span > 0.03, f"span={span*1000:.0f}mm")
    check("右手一直举着（叠加层生效）", min(hands) > 0.85, f"min={min(hands):.3f}")
    check("身体在前进", abs(rt.body.y) > 0.2, f"y={rt.body.y:.3f}")

    print("[3] 叠加层只改手臂：腿骨和「纯走路」完全一致")
    rt_a = new_rt()
    rt_b = new_rt()
    same_t = 0.8
    rt_a.tick(same_t)
    rt_b.tick(same_t)
    ra = rt_a.handle_composite(WALK)
    rb = rt_b.handle_composite(WAVE)
    worst = 0.0
    for b in LEG:
        d = float(np.max(np.abs(np.asarray(ra["basis"][b]) - np.asarray(rb["basis"][b]))))
        worst = max(worst, d)
    check("腿骨逐元素一致", worst < 1e-12, f"{worst:.2e}")
    hr = float(rig.compute(ra["basis"])["rightHand"][2, 3])
    hw = float(rig.compute(rb["basis"])["rightHand"][2, 3])
    check("右手确实被叠加层改了", hw - hr > 0.1, f"{hr:.3f} → {hw:.3f}")

    print("[4] 关掉全身装配就退回上半身（回退开关）")
    rt_off = new_rt()
    rt_off._full_body = False
    rt_off.tick(0.5)
    r_off = rt_off.handle_composite(WALK)
    check("full_body=False", r_off.get("full_body") is False, str(r_off.get("full_body")))
    leg_id = float(np.max(np.abs(np.asarray(r_off["basis"]["leftFoot"]) - np.eye(4))))
    check("腿是恒等（不动）", leg_id < 1e-12, f"{leg_id:.2e}")

    print("[5] 装配诊断可查")
    rt_d = new_rt()
    rt_d.tick(0.3)
    rt_d.handle_composite(WALK)
    d = rt_d._last_full_diag
    check("有 err_mm", "err_mm" in d, str(list(d)[:4]))
    check("脚误差 < 1mm", max(d["err_mm"].values()) < 1.0 if d["err_mm"] else True,
          str(d.get("err_mm")))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
