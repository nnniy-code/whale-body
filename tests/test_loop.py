"""tests/test_loop.py — 常驻帧循环：时钟 / 写骨 / 全身装配的契约。

设计依据：v1.9「主循环」——v1.8 的能力全困在测试里，图上不了屏。

怎么测：python tests/test_loop.py
预期现象：[loop] ALL PASS
调参位置：core/loop.py
"""

from __future__ import annotations

import json
import os
import sys
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.loop import BodyLoop  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


class StubRT:
    """只实现 loop 用到的那几个方法。"""

    def __init__(self):
        self.ticks = []
        self.basis = {"a": np.eye(4)}
        self.last_t = None
        self.last_family = None
        self.body = types.SimpleNamespace(as_dict=lambda: {"x": 0.0})

    def tick(self, dt):
        self.ticks.append(dt)

    def current_basis(self):
        return self.basis

    def build_full(self, P, family, t, active_side=None):
        self.last_t, self.last_family = t, family
        return {"a": np.eye(4)}, {"err_mm": {"left": 0.0, "right": 0.0}}

    def handle_composite(self, acts, now=None, **kw):
        return {"basis": self.basis, "now": now, "acts": acts}

    rig = types.SimpleNamespace(compute=lambda b: {"rightHand": np.eye(4)})


def main() -> int:
    print("[1] 契约：时钟累加 / 每帧算姿态 / 每帧写骨")
    rt = StubRT()
    seen = []
    loop = BodyLoop(rt, writer=lambda b: seen.append(b) or 7)
    loop.step(0.1)
    loop.step(0.2)
    check("t 累加", abs(loop.t - 0.3) < 1e-9, f"{loop.t:.3f}")
    check("tick 收到 dt", rt.ticks == [0.1, 0.2], str(rt.ticks))
    check("writer 每帧调用", len(seen) == 2, str(len(seen)))
    check("written 计数", loop.written == 14, str(loop.written))
    check("frames 计数", loop.frames == 2, str(loop.frames))
    check("记录 last_basis", loop.last_basis is not None)

    print("[2] P+family → 走 build_full（全身），t 用的是 loop 的时钟")
    loop.step(0.05, P={"lean": 0}, family="walk")
    check("传给 build_full 的 t", abs(rt.last_t - 0.35) < 1e-9, f"{rt.last_t}")
    check("族名透传", rt.last_family == "walk", str(rt.last_family))
    check("diag 被保存", loop.last_diag.get("err_mm") is not None)

    print("[3] 不写骨的两条路")
    rt2 = StubRT()
    loop2 = BodyLoop(rt2, writer=lambda b: seen.append(b) or 1)
    loop2.step(0.1, write=False)
    check("write=False 不调用 writer", loop2.written == 0, str(loop2.written))
    loop3 = BodyLoop(StubRT())          # 没有 writer
    b, _ = loop3.step(0.1)
    check("无 writer 不崩", b is not None)
    check("无 writer 时 written=0", loop3.written == 0)

    print("[4] 真 Runtime：走 2 秒（60 帧）全身不崩、脚不滑")
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

    rtr = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                  collision_table=tab, lengths=lengths,
                  capsule_baseline=base, capsule_only=ONLY)
    rtr.set_motion(family="walk")
    P = dict(fams["walk"]["P_default"])
    loop4 = BodyLoop(rtr, writer=lambda b: 1)
    worst_err = 0.0
    for _ in range(60):
        b, diag = loop4.step(1 / 30, P=P, family="walk")
        if diag.get("err_mm"):
            worst_err = max(worst_err, max(diag["err_mm"].values()))
    check("60 帧", loop4.frames == 60, str(loop4.frames))
    check("t ≈ 2.0s", abs(loop4.t - 2.0) < 1e-6, f"{loop4.t:.3f}")
    check("脚 IK 误差 < 1mm", worst_err < 1.0, f"{worst_err:.1f}mm")
    check("body 真的前进了", abs(rtr.body.y) > 0.4, f"y={rtr.body.y:.3f}")

    print("[5] 姿态连续（相邻帧没有瞬移）")
    seq = []
    loop5 = BodyLoop(rtr)
    for _ in range(30):
        b, _ = loop5.step(1 / 30, P=P, family="walk")
        seq.append(rig.compute(b)["rightHand"][0:3, 3].copy())
    steps = [float(np.linalg.norm(seq[i + 1] - seq[i])) * 1000 for i in range(len(seq) - 1)]
    check("单帧最大位移 < 200mm", max(steps) < 200, f"{max(steps):.0f}mm")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
