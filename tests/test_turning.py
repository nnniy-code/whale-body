"""tests/test_turning.py — 转身：朝向必须真的落到骨架上。

设计依据：HANDOFF §7 P6「转 90° 不抽搐」、§22.6 第 3 项、v1.9 修复 #1

问题：v1.8 里 `BodyState.yaw` 只改了「往哪个方向走」，
      全项目没有任何地方把 yaw 写进骨骼 → 身体永远朝 -Y。
      `test_runtime_body` 只断言了 `body.yaw`，所以一直是假绿。

怎么测：python tests/test_turning.py
预期现象：[turning] ALL PASS
调参位置：core/assembly.py build_full_body / place_hips
"""

from __future__ import annotations

import json
import math
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


def facing(M) -> float:
    """身体朝向：左右肩连线在 XY 平面的角度（度）。"""
    l = M["leftShoulder"][0:3, 3]
    r = M["rightShoulder"][0:3, 3]
    v = r[0:2] - l[0:2]
    return math.degrees(math.atan2(float(v[1]), float(v[0])))


def delta(a0: float, a1: float) -> float:
    return ((a1 - a0 + 180.0) % 360.0) - 180.0


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

    P_idle = dict(fams["idle"]["P_default"])

    print("[1] yaw=0 时朝向不动（基线）")
    rt = new_rt()
    a_0 = facing(rig.compute(rt.build_full(P_idle, "idle", 0.0)[0]))
    for _ in range(6):
        rt.tick(1 / 12)
    a_1 = facing(rig.compute(rt.build_full(P_idle, "idle", 0.0)[0]))
    check("不转就不动", abs(delta(a_0, a_1)) < 0.5, f"{delta(a_0, a_1):+.1f}°")

    print("[2] turn_rate=90 跑 1 秒 → 骨架转 90°")
    rt = new_rt()
    a_0 = facing(rig.compute(rt.build_full(P_idle, "idle", 0.0)[0]))
    rt.set_motion(turn_rate=90.0)
    for _ in range(12):
        rt.tick(1 / 12)
    a_1 = facing(rig.compute(rt.build_full(P_idle, "idle", 0.0)[0]))
    d = delta(a_0, a_1)
    check("BodyState.yaw=90", abs(rt.body.yaw - 90.0) < 1.0, f"{rt.body.yaw:.1f}°")
    check("骨架同时转 90°（±5°）", abs(d - 90.0) < 5.0, f"{d:+.1f}°")

    print("[3] spin 族：set_motion(family) 会带出 turn_rate")
    rt = new_rt()
    rt.set_motion(family="spin")
    check("turn_rate 来自族", abs(rt.body.turn_rate - 90.0) < 1e-6, f"{rt.body.turn_rate:.1f}")
    P_spin = dict(fams["spin"]["P_default"])
    a_0 = facing(rig.compute(rt.build_full(P_spin, "spin", 0.0)[0]))
    for _ in range(12):
        rt.tick(1 / 12)
    a_1 = facing(rig.compute(rt.build_full(P_spin, "spin", 1.0)[0]))
    d = delta(a_0, a_1)
    check("spin 真的转了 90°", abs(d - 90.0) < 5.0, f"{d:+.1f}°")

    print("[4] 转身时腿必须健康（不能因为够不着而空转）")
    rt = new_rt()
    rt.set_motion(turn_rate=90.0)
    for _ in range(12):
        rt.tick(1 / 12)
    b, diag = rt.build_full(P_idle, "idle", 0.0)
    worst_ik = max(diag["err_mm"].values()) if diag["err_mm"] else 0.0
    check("IK 误差 < 1mm", worst_ik < 1.0, f"{worst_ik:.1f}mm")
    check("没有 IK 失败", not diag["ik_fail"], str(diag["ik_fail"]))
    check("diag 记录 yaw", abs(float(diag.get("yaw", -1)) - 90.0) < 1.0, str(diag.get("yaw")))

    print("[5] 已知遗留：转身时脚随身体转（「支撑脚当轴」未做，只记录）")
    rt = new_rt()
    rt.set_motion(turn_rate=90.0)
    M0 = rig.compute(rt.build_full(P_idle, "idle", 0.0)[0])
    f0 = {s: M0[f"{s}Foot"][0:3, 3].copy() for s in ("left", "right")}
    for _ in range(12):
        rt.tick(1 / 12)
    M1 = rig.compute(rt.build_full(P_idle, "idle", 0.0)[0])
    drift = max(float(np.linalg.norm(M1[f"{s}Foot"][0:3, 3] - f0[s])) for s in ("left", "right"))
    print(f"    脚世界滑移 {drift*1000:.0f}mm（要「不滑」得先做支撑脚当轴）")
    check("滑移在可解释范围（<250mm）", drift < 0.25, f"{drift*1000:.0f}mm")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
