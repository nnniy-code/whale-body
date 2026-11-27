"""tests/test_foot_target.py — 腿的三种驱动（步态 / 静态 / 指定点）验收。

怎么测：python tests/test_foot_target.py
预期现象：[foot_target] ALL PASS

**重写说明**：旧版在 tools/ 下，多个 case 共享 state 变量，反复报 263mm 假象，
而 probe_assembly_diff.py 逐项对比是 0.0mm。这版每个 case 独立建 state。
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.assembly import build_full_body  # noqa: E402
from core.body_state import BodyState  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.foot_target import (  # noqa: E402
    ANKLE_HEIGHT, GaitTarget, PointTarget, StaticTarget,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    M0 = rig.compute({n: np.eye(4) for n in rigd["order"]})

    # 每个 case 用**独立的** start（从 rig rest 读，不共享可变对象）
    def fresh_start():
        return {s: [float(M0[f"{s}Foot"][0, 3]), float(M0[f"{s}Foot"][1, 3]), 0.0]
                for s in ("left", "right")}

    P = dict(fams["walk"]["P_default"])
    G_base = dict(fams["walk"]["G"])
    G_base.update({"period": 1.0, "stride": 0.5, "foot_lift": 0.06})

    def errs(diag) -> float:
        return max(diag["err_mm"].values()) if diag["err_mm"] else 999.0

    print("[1] GaitTarget：9 帧（state 固定）")
    worst = 0.0
    for i in range(9):
        gt = GaitTarget(G_base, fresh_start())       # 每次新建，避免状态残留
        st = BodyState()
        _b, d = build_full_body(P, gt, st, i / 8, rig, bm)
        worst = max(worst, errs(d))
    check("走路误差 < 1mm", worst < 1.0, f"{worst:.1f}mm")

    print("[2] GaitTarget：边走边算（state 与 t 同步）")
    gt = GaitTarget(G_base, fresh_start())
    st = BodyState(vel=0.5)
    worst = 0.0
    for i in range(9):
        _b, d = build_full_body(P, gt, st, i / 8, rig, bm)
        worst = max(worst, errs(d))
        st.step(0.125)
    check("同步推进误差 < 1mm", worst < 1.0, f"{worst:.1f}mm")

    print("[3] StaticTarget：站立")
    _b, d = build_full_body(P, StaticTarget(fresh_start()), BodyState(), 0.0, rig, bm)
    check("站立误差 < 1mm", errs(d) < 1.0, f"{errs(d):.1f}mm")

    print("[4] StaticTarget：蹲下 150mm")
    _b, d = build_full_body(P, StaticTarget(fresh_start(), hip_drop=0.15),
                            BodyState(), 0.0, rig, bm)
    check("蹲下误差 < 1mm", errs(d) < 1.0, f"{errs(d):.1f}mm")

    print("[5] StaticTarget：单脚站（右腿抬 120mm）")
    _b, d = build_full_body(P, StaticTarget(fresh_start(), hip_drop=0.02,
                                            lift={"right": 0.12}),
                            BodyState(), 0.0, rig, bm)
    M = rig.compute(_b)
    lz, rz = float(M["leftFoot"][2, 3]), float(M["rightFoot"][2, 3])
    check("左脚在地面", abs(lz - ANKLE_HEIGHT) < 0.01, f"{lz:.3f}")
    check("右脚抬起", rz > 0.15, f"{rz:.3f}")

    print("[6] PointTarget：左脚前 15cm")
    pts = fresh_start()
    pts["left"] = [pts["left"][0], pts["left"][1] - 0.15, 0.0]
    _b, d = build_full_body(P, PointTarget(pts, hip_drop=0.05), BodyState(), 0.0, rig, bm)
    check("指定点误差 < 1mm", errs(d) < 1.0, f"{errs(d):.1f}mm")

    print("[7] 身体移动：脚跟着走")
    _b, d = build_full_body(P, StaticTarget(fresh_start()), BodyState(x=0.6, y=-0.3),
                            0.0, rig, bm)
    lf = _b["leftFoot"][0:3, 3] if "leftFoot" in _b else rig.compute(_b)["leftFoot"][0:3, 3]
    M = rig.compute(_b)
    lf = M["leftFoot"][0:3, 3]
    check("左脚 x 平移 0.6", abs(float(lf[0]) - 0.6 - 0.076) < 0.02, f"{lf[0]:.3f}")
    check("左脚 y 平移 -0.3", abs(float(lf[1]) + 0.3 + 0.068) < 0.02, f"{lf[1]:.3f}")

    print("[8] hip_drop 自动估算")
    lows = [GaitTarget({**G_base, "stride": s}, fresh_start()).hip_drop()
            for s in (0.2, 0.4, 0.6)]
    check("stride 越大降得越多", lows[0] < lows[1] < lows[2], str(lows))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
