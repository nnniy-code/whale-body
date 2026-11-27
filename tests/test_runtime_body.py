"""tests/test_runtime_body.py — D 第②步：runtime 接 BodyState + 全身装配。

怎么测：python tests/test_runtime_body.py
预期现象：[runtime_body] ALL PASS
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

    rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                 collision_table=tab, lengths=lengths,
                 capsule_baseline=base, capsule_only=ONLY)

    print("[1] Runtime 有 BodyState")
    check("body 存在", hasattr(rt, "body"))
    check("初始位置 0", rt.body.x == 0 and rt.body.y == 0)
    check("tick 可调用", hasattr(rt, "tick"))

    print("[2] tick 推进身体")
    rt.set_motion(vel=0.5)
    for _ in range(12):
        rt.tick(1 / 12)
    print(f"    {rt.body}")
    check("前进了（|y| > 0.3）", abs(rt.body.y) > 0.3, f"y={rt.body.y:.3f}")
    check("bob 有起伏", abs(rt.body.bob) >= 0.0)

    print("[3] 停下后不再前进")
    rt.set_motion(vel=0.0)
    y0 = rt.body.y
    for _ in range(12):
        rt.tick(1 / 12)
    check("位置不变", abs(rt.body.y - y0) < 1e-9, f"{rt.body.y:.4f} vs {y0:.4f}")

    print("[4] build_full：walk 族用步态，脚误差 0")
    rt2 = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                  collision_table=tab, lengths=lengths,
                  capsule_baseline=base, capsule_only=ONLY)
    P = dict(fams["walk"]["P_default"])
    worst = 0.0
    for i in range(9):
        _b, diag = rt2.build_full(P, "walk", i / 8)
        if diag["err_mm"]:
            worst = max(worst, max(diag["err_mm"].values()))
    check("走路 9 帧误差 < 1mm", worst < 1.0, f"{worst:.1f}mm")

    print("[5] build_full：idle 族站着不动")
    _b, diag = rt2.build_full(dict(fams["idle"]["P_default"]), "idle", 0.0)
    check("idle 误差 < 1mm",
          max(diag["err_mm"].values()) < 1.0 if diag["err_mm"] else True,
          str(diag["err_mm"]))

    print("[6] 身体移动时脚跟着走")
    rt2.body.x = 0.8
    b, diag = rt2.build_full(dict(fams["idle"]["P_default"]), "idle", 0.0)
    M = rig.compute(b)
    lf = M["leftFoot"][0:3, 3]
    check("左脚 x ≈ 0.8 + rest", abs(float(lf[0]) - 0.8 - 0.076) < 0.02, f"{lf[0]:.3f}")

    print("[7] 转身")
    rt3 = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                  collision_table=tab, lengths=lengths,
                  capsule_baseline=base, capsule_only=ONLY)
    rt3.set_motion(vel=0.5, turn_rate=90.0)
    for _ in range(12):
        rt3.tick(1 / 12)
    print(f"    {rt3.body}")
    check("有朝向变化（|yaw| > 30°）", abs(rt3.body.yaw) > 30, f"{rt3.body.yaw:.1f}°")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
