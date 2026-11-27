"""tests/test_foot_lock.py — 转身时支撑脚不滑（§22.6 第 3 项）。

设计依据：HANDOFF §22.6「转身时脚不滑」、v1.9

v1.8：脚是「身体局部坐标」，身体一转脚跟着转 → 边走边转 90°/s 时
      支撑脚一个支撑期滑 **112mm**（物理上就是在冰面上原地打转）。

做法：**支撑脚当轴**。支撑脚在世界里钉死，身体位置由它反推：

    world_foot = body_xy + Rz(yaw) · local_foot      ← 锁定
    body_xy    = lock_xy − Rz(yaw) · local_foot      ← 每帧反推

所以支撑脚不动，而身体（含髋）绕着它转 —— 髋到脚的距离是刚体不变的，
不会像「身体绕自己转、脚钉死」那样超出腿长（那会差 441mm > 427mm）。

怎么测：python tests/test_foot_lock.py
预期现象：[foot_lock] ALL PASS
调参位置：core/runtime.py _apply_foot_lock / core/foot_target.py stance_side
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

    G = fams["walk"]["G"]
    period, duty = float(G["period"]), float(G["duty"])
    P = dict(fams["walk"]["P_default"])

    def run(turn_deg, seconds=2.2):
        """量**轴心脚**（rt._lock_side）的滑移。

        duty=0.62 > 0.5 存在双支撑期，两只脚按相位都算「支撑」，
        但只有一只是旋转轴。非轴心的那只在转身时必然要挪（人得换重心），
        这里单独统计，只作为信息，不算失败。
        """
        rt = new_rt()
        rt.set_motion(family="walk")
        rt.body.turn_rate = turn_deg
        t = 0.0
        cur, origin = None, None
        worst, other = 0.0, 0.0
        other_origin = {}
        for _ in range(int(seconds / DT)):
            rt.tick(DT)
            t += DT
            b, _d = rt.build_full(P, "walk", t)
            M = rig.compute(b)
            side = rt._lock_side
            if side is None:
                continue
            p = M[f"{side}Foot"][0:3, 3].copy()
            if side != cur:
                cur, origin = side, p
            else:
                worst = max(worst, float(np.linalg.norm(p[0:2] - origin[0:2])) * 1000)
            # 非轴心的另一只脚：只在其**支撑相**内统计（摆动相本来就在飞）
            ph = (t / period) % 1.0
            for s in ("left", "right"):
                off = 0.0 if s == "left" else float(G.get("phase_offset", 0.5))
                if s == side or ((ph + off) % 1.0) >= duty:
                    other_origin.pop(s, None)
                    continue
                q = M[f"{s}Foot"][0:3, 3].copy()
                if s not in other_origin:
                    other_origin[s] = q
                other = max(other, float(np.linalg.norm(q[0:2] - other_origin[s][0:2])) * 1000)
        return worst, rt, other

    print("[1] 直走：轴心脚仍然不滑（回归）")
    w0, _, _ = run(0.0)
    check("滑移 < 2mm", w0 < 2.0, f"{w0:.1f}mm")

    print("[2] 边走边转 90°/s：轴心脚不滑")
    w1, rt1, other1 = run(90.0)
    check("轴心脚滑移 < 5mm（修前 112mm）", w1 < 5.0, f"{w1:.1f}mm")
    print(f"    非轴心脚同期最大位移 {other1:.0f}mm（换重心时必然发生，只记录）")
    check("确实转了（yaw > 60°）", abs(rt1.body.yaw) > 60, f"{rt1.body.yaw:.0f}°")

    print("[3] 身体是绕支撑脚走的，不是原地转")
    d = float(np.hypot(rt1.body.x, rt1.body.y))
    check("身体位置有变化（>50mm）", d > 0.05, f"{d*1000:.0f}mm")

    print("[4] 脚锁不会吃掉前进（速度仍是步态自洽值）")
    rt2 = new_rt()
    rt2.set_motion(family="walk")
    for _ in range(30):
        rt2.tick(DT)
        rt2.build_full(P, "walk", rt2.t)
    v = float(np.hypot(rt2.body.x, rt2.body.y)) / rt2.t
    want = rt2.speed_for("walk")
    check("平均速度 ≈ stride/(duty×period)", abs(v - want) < 0.03, f"{v:.3f} vs {want:.3f}")

    print("[5] 非步态族（idle）不受影响")
    rt3 = new_rt()
    rt3.set_motion(turn_rate=90.0)
    Pi = dict(fams["idle"]["P_default"])
    for _ in range(12):
        rt3.tick(DT)
        rt3.build_full(Pi, "idle", 0.0)
    check("idle 不前进", float(np.hypot(rt3.body.x, rt3.body.y)) < 1e-6,
          f"{float(np.hypot(rt3.body.x, rt3.body.y)):.4f}")
    check("idle 仍然能转", abs(rt3.body.yaw) > 30, f"{rt3.body.yaw:.0f}°")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
