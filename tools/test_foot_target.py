"""tools/test_foot_target.py — 验证三种脚目标源（步态 / 静态 / 指定点）。

怎么测：python tools/test_foot_target.py
预期现象：[foot_target] ALL PASS，三种源的脚误差都 < 1mm
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import pose_gen  # noqa: E402
from core.body_state import BodyState  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.foot_target import (  # noqa: E402
    ANKLE_HEIGHT, GaitTarget, PointTarget, StaticTarget, attach_legs,
)

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


from core.assembly import build_full_body, translate_local  # noqa: E402


from core.assembly import build_full_body as build_full  # noqa: E402


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    M0 = rig.compute({n: np.eye(4) for n in rigd["order"]})
    start = {s: [float(M0[f"{s}Foot"][0, 3]), float(M0[f"{s}Foot"][1, 3]), 0.0]
             for s in ("left", "right")}
    P = dict(fams["walk"]["P_default"])

    print("[1] GaitTarget（走路）")
    G = dict(fams["walk"]["G"])
    G.update({"period": 1.0, "stride": 0.5, "foot_lift": 0.06})
    gt = GaitTarget(G, start)
    print(f"    {gt.describe()}")
    # 只推进 t（步态相位），state 固定 —— 两者都推进会双重推进
    st = BodyState(vel=0.5)
    worst = 0.0
    for i in range(9):
        t = i / 8
        b, d = build_full(P, gt, st, t, rig, bm)
        worst = max(worst, max(d["err_mm"].values()))
    check("走路 9 帧误差 < 1mm", worst < 1.0, f"{worst:.1f}mm")

    # 再测：state 前进 + t 同步推进（真实用法）
    st2 = BodyState(vel=0.5)
    worst2 = 0.0
    for i in range(9):
        t = i / 8
        b, d = build_full(P, gt, st2, t, rig, bm)
        worst2 = max(worst2, max(d["err_mm"].values()))
        st2.step(0.125)
    check("边走边算（state+t 同步）误差 < 1mm", worst2 < 1.0, f"{worst2:.1f}mm")

    print("[2] StaticTarget（站立）")
    srt = StaticTarget(start, hip_drop=0.0)
    b, d = build_full(P, srt, BodyState(), 0.0, rig, bm)
    check("站立误差 < 1mm", max(d["err_mm"].values()) < 1.0,
          f"{max(d['err_mm'].values()):.1f}mm")

    print("[3] StaticTarget 蹲下 150mm")
    crouch = StaticTarget(start, hip_drop=0.15)
    b, d = build_full(P, crouch, BodyState(), 0.0, rig, bm)
    M = rig.compute(b)
    check("蹲下误差 < 1mm", max(d["err_mm"].values()) < 1.0,
          f"{max(d['err_mm'].values()):.1f}mm")
    hips_z = float(M["hips"][2, 3])
    check("髋明显降低（<0.40）", hips_z < 0.40, f"{hips_z:.3f}")

    print("[4] StaticTarget 单脚站（右腿抬 120mm）")
    onefoot = StaticTarget(start, hip_drop=0.02, lift={"right": 0.12})
    b, d = build_full(P, onefoot, BodyState(), 0.0, rig, bm)
    M = rig.compute(b)
    rz = float(M["rightFoot"][2, 3])
    lz = float(M["leftFoot"][2, 3])
    check("右脚抬高", rz > 0.15, f"z={rz:.3f}")
    check("左脚仍在地面", abs(lz - ANKLE_HEIGHT) < 0.01, f"z={lz:.3f}")

    print("[5] PointTarget（指定点：左脚前 15cm）")
    pts = {s: list(start[s]) for s in ("left", "right")}
    pts["left"] = [pts["left"][0], pts["left"][1] - 0.15, 0.0]
    pt = PointTarget(pts, hip_drop=0.05)
    b, d = build_full(P, pt, BodyState(), 0.0, rig, bm)
    check("指定点误差 < 1mm", max(d["err_mm"].values()) < 1.0,
          f"{max(d['err_mm'].values()):.1f}mm")

    print("[6] 身体移动时脚目标跟着走")
    st2 = BodyState(x=0.6, y=-0.3)
    b, d = build_full(P, srt, st2, 0.0, rig, bm)
    M = rig.compute(b)
    lf = M["leftFoot"][0:3, 3]
    check("左脚 x 平移 0.6", abs(float(lf[0]) - 0.6 - start["left"][0]) < 0.02,
          f"x={lf[0]:.3f}")
    check("左脚 y 平移 -0.3", abs(float(lf[1]) + 0.3 - start["left"][1]) < 0.02,
          f"y={lf[1]:.3f}")

    print("[7] 髋降的自动估算")
    for stride in (0.2, 0.4, 0.6):
        G2 = dict(G)
        G2["stride"] = stride
        g2 = GaitTarget(G2, start)
        print(f"    stride={stride} → hip_drop={g2.hip_drop():.3f}")
    check("stride 越大降得越多",
          GaitTarget({**G, "stride": 0.6}, start).hip_drop()
          > GaitTarget({**G, "stride": 0.2}, start).hip_drop())

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



