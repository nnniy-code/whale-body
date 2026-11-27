"""tests/test_motion_speed.py — 速度自洽：步态族的速度不是自由量。

设计依据：HANDOFF §7 P6「walk 滑移 <3mm」、v1.9 修复 #3

问题：支撑相脚要在世界里不动，身体就必须以 `v = stride/(duty×period)` 前进。
      vel 填错脚就滑（实测 vel=0.5 时一个支撑期滑 118mm，而测试全绿）。

怎么测：python tests/test_motion_speed.py
预期现象：[motion_speed] ALL PASS
调参位置：core/gait.py body_speed / core/runtime.py set_motion
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
from core.gait import body_speed  # noqa: E402
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


def slip_of(rt, rig, fams, vel, dt=1 / 30.0, seconds=2.2):
    """走 seconds 秒，返回支撑脚相对「支撑期起点」的最大世界位移（mm）。"""
    P = dict(fams["walk"]["P_default"])
    G = fams["walk"]["G"]
    period, duty = float(G["period"]), float(G["duty"])
    rt.set_motion(vel=vel)
    t, origin, worst, synced = 0.0, {}, 0.0, None
    for _ in range(int(seconds / dt)):
        rt.tick(dt)
        t += dt
        b, diag = rt.build_full(P, "walk", t)
        if diag.get("vel_sync"):
            synced = diag["vel_sync"]
        M = rig.compute(b)
        ph = (t / period) % 1.0
        for side, off in (("left", 0.0), ("right", 0.5)):
            if ((ph + off) % 1.0) < duty:
                p = M[f"{side}Foot"][0:3, 3].copy()
                if side not in origin:
                    origin[side] = p
                worst = max(worst, float(np.linalg.norm(p[0:2] - origin[side][0:2])) * 1000)
            else:
                origin.pop(side, None)
    return worst, synced


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

    print("[1] body_speed = stride/(duty×period)")
    G = fams["walk"]["G"]
    v = body_speed(G)
    want = float(G["stride"]) / (float(G["duty"]) * float(G["period"]))
    check("walk 速度", abs(v - want) < 1e-6, f"{v:.4f} m/s")
    check("站桩族返回 0（不定义速度）", body_speed(fams["idle"]["G"]) == 0.0)

    print("[2] Runtime.speed_for / set_motion(family)")
    rt = new_rt()
    check("speed_for 一致", abs(rt.speed_for("walk") - v) < 1e-6, f"{rt.speed_for('walk'):.4f}")
    rt.set_motion(family="walk")
    check("set_motion 推导出正确 vel", abs(rt.body.vel - v) < 1e-6, f"{rt.body.vel:.4f}")
    rt.set_motion(family="spin")
    check("spin 带出 turn_rate", abs(rt.body.turn_rate) > 1e-9, f"{rt.body.turn_rate:.1f}°/s")

    print("[3] 填错速度会自动纠正（不再滑）")
    w, sync = slip_of(new_rt(), rig, fams, vel=0.5)
    check("vel=0.5（错的）滑移 < 2mm", w < 2.0, f"{w:.1f}mm")
    check("纠正被记录进 diag", sync is not None, str(sync))
    check("纠正到正确值", sync is not None and abs(sync["now"] - v) < 1e-4, str(sync))

    print("[4] 正确速度本来就是 0 滑移")
    w2, sync2 = slip_of(new_rt(), rig, fams, vel=v)
    check("vel=正确值 滑移 < 1mm", w2 < 1.0, f"{w2:.1f}mm")
    check("正确值下不产生纠正", sync2 is None, str(sync2))

    print("[5] 从步态族切到站桩族，速度必须归零（否则会一直往前滑）")
    rt = new_rt()
    rt.set_motion(family="walk")
    Pw = dict(fams["walk"]["P_default"])
    for _ in range(20):
        rt.tick(DT)
        rt.build_full(Pw, "walk", rt.t)
    y_before = rt.body.y
    check("走的时候有速度", abs(rt.body.vel) > 0.1, f"{rt.body.vel:.3f}")
    b, diag = rt.build_full(dict(fams["sit"]["P_default"]), "sit", rt.t)
    check("切到 sit 后 vel=0", abs(rt.body.vel) < 1e-9, f"{rt.body.vel:.4f}")
    check("diag 记录了停机", (diag.get("vel_sync") or {}).get("now") == 0.0,
          str(diag.get("vel_sync")))
    for _ in range(30):
        rt.tick(DT)
    check("不再移动", abs(rt.body.y - y_before) < 1e-9,
          f"{rt.body.y:.4f} vs {y_before:.4f}")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
