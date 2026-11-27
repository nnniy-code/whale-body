"""tests/test_perframe_composite.py — 每帧调 composite 时，过渡不能被反复清零。

设计依据：HANDOFF §16 待定项 #3、§7 P0「big_steps → 0」、v1.9 修复 #2

问题：`transition_to` 是「动作切换」语义。每帧调它会把 200ms 计时清零，
      输出退化成「延迟一帧的硬切」——实测 walk→walk+wave 单帧跳 433.9mm。
      `tools/test_composite.py` 只调一次再采样，所以一直测不出来。

怎么测：python tests/test_perframe_composite.py
预期现象：[perframe_composite] ALL PASS
调参位置：core/runtime.py set_pose / transition_to / handle_composite
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.blend import progress  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]
FAILS: list[str] = []
DT = 1 / 30.0
WALK = [{"intent": "walk"}]
WAVE = [{"intent": "walk"}, {"intent": "wave", "groups": ["arm.right"]}]


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

    print("[1] switched 只在动作组合变化时为 True")
    rt = new_rt()
    t0 = time.perf_counter()
    r1 = rt.handle_composite(WALK, now=t0)
    r2 = rt.handle_composite(WALK, now=t0 + DT)
    r3 = rt.handle_composite(WAVE, now=t0 + 2 * DT)
    r4 = rt.handle_composite(WAVE, now=t0 + 3 * DT)
    check("首帧 switched", r1.get("switched") is True, str(r1.get("switched")))
    check("同组第二帧不 switched", r2.get("switched") is False, str(r2.get("switched")))
    check("换动作时 switched", r3.get("switched") is True, str(r3.get("switched")))
    check("同组第三帧不 switched", r4.get("switched") is False, str(r4.get("switched")))

    print("[2] 每帧调用时，动作切换不瞬移")
    rt = new_rt()
    prev, worst, at = None, 0.0, ""
    for i in range(18):
        now = t0 + i * DT
        rt.handle_composite(WALK if i < 8 else WAVE, now=now)
        M = rig.compute(rt.current_basis(now=now))
        pos = {b: M[b][0:3, 3].copy() for b in ("rightHand", "head", "rightFoot")}
        if prev is not None:
            for b in pos:
                d = float(np.linalg.norm(pos[b] - prev[b])) * 1000
                if d > worst:
                    worst, at = d, f"f{i}:{b}"
        prev = pos
    # 200ms 内走完 ~425mm，smoothstep 峰值 ≈ 1.5×Δ/T×dt ≈ 105mm/帧
    check("单帧位移 < 150mm（修前 433.9mm）", worst < 150.0, f"{worst:.0f}mm ({at})")

    print("[3] 切换后 200ms 内收敛到目标")
    rt = new_rt()
    t1 = t0 + 100.0
    rt.handle_composite(WALK, now=t1)
    rt.handle_composite(WAVE, now=t1)
    target = float(rig.compute(rt._basis)["rightHand"][2, 3])
    end = float(rig.compute(rt.current_basis(now=t1 + 0.25))["rightHand"][2, 3])
    check("到达目标（±10mm）", abs(end - target) < 0.01, f"{end:.3f} vs {target:.3f}")

    print("[4] set_pose 不重启过渡；transition_to 会（对照组）")
    rt = new_rt()
    P_idle = dict(fams["idle"]["P_default"])
    A = rt.basis_for_from_P(P_idle, "idle")
    B = rt.basis_for_from_P(dict(fams["wave"]["P_default"]), "wave")
    t2 = t0 + 200.0
    rt.set_pose(A, now=t2)
    rt.transition_to(B, now=t2)

    def alpha(at):
        return progress((at - rt._blend_t0) * 1000.0, rt._blend_ms)

    a1 = alpha(t2 + 0.1)
    rt.set_pose(B, now=t2 + 0.1)
    a2 = alpha(t2 + 0.1)
    check("set_pose 前 alpha 已推进", abs(a1 - 0.5) < 0.02, f"{a1:.2f}")
    check("set_pose 不重置 alpha", abs(a2 - a1) < 1e-9, f"{a2:.2f}")
    rt.transition_to(B, now=t2 + 0.1)
    a3 = alpha(t2 + 0.1)
    check("transition_to 会重置 alpha（对照）", a3 < 1e-9, f"{a3:.2f}")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
