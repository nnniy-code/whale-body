"""tests/test_arm_swing.py — 摆臂跟步态相位（v1.9）。

设计依据：视觉验证发现「走路时两臂固定往后 22°，不是摆臂」

v1.8：`walk.P_default.arm_swing = 22` 被 `pose_gen` 当**常量**用，
      而且左右同号 —— 等于「两臂一起往后背」。

现在：`phase` 给了就摆动，左右反相（左腿在前 → 左臂在后）。

怎么测：python tests/test_arm_swing.py
预期现象：[arm_swing] ALL PASS
调参位置：core/pose_gen.py build(phase=...) / core/assembly.py _gait_phase
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import pose_gen  # noqa: E402
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

    Pw = dict(fams["walk"]["P_default"])
    A = float(Pw.get("arm_swing", 0.0))

    def hand_y(phase):
        b = pose_gen.build(Pw, rf, bm, phase=phase)
        M = rf.compute(b)
        return float(M["leftHand"][1, 3]), float(M["rightHand"][1, 3])

    print("[1] 给了 phase 就摆动，且左右反相")
    l0, r0 = hand_y(0.0)
    l1, r1 = hand_y(0.5)
    dl, dr = l1 - l0, r1 - r0
    check("左臂动了（>10mm）", abs(dl) > 0.010, f"{dl*1000:+.0f}mm")
    check("右臂动了（>10mm）", abs(dr) > 0.010, f"{dr*1000:+.0f}mm")
    check("左右方向相反", dl * dr < 0, f"左 {dl*1000:+.0f} / 右 {dr*1000:+.0f}mm")

    print("[2] 半个周期后在两端（phase 0 与 0.5 是极值）")
    l2, r2 = hand_y(0.25)
    check("phase=0.25 接近中立", abs(l2 - (l0 + l1) / 2) < abs(l1 - l0) * 0.45,
          f"{l2:.4f}")

    print("[3] 不给 phase 就是静态角度（非周期动作不变）")
    a = pose_gen.build(Pw, rf, bm)
    c = pose_gen.build(Pw, rf, bm)
    same = all(float(np.max(np.abs(np.asarray(a[k]) - np.asarray(c[k])))) < 1e-12 for k in a)
    check("两次一致", same)
    Ma = rf.compute(a)
    check("左右手高度相同（同号静态摆）",
          abs(float(Ma['leftHand'][2, 3]) - float(Ma['rightHand'][2, 3])) < 0.02,
          f"{float(Ma['leftHand'][2,3]):.3f} vs {float(Ma['rightHand'][2,3]):.3f}")

    print("[4] 幅度和 arm_swing 同量级")
    span = abs(l1 - l0)
    check("手部前后跨度 > 20mm", span > 0.02, f"{span*1000:.0f}mm / arm_swing={A}")

    print("[5] 接进 build_full：走起来手真的在摆")
    rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                 collision_table=tab, lengths=lengths,
                 capsule_baseline=base, capsule_only=ONLY)
    rt.set_motion(family="walk")
    ys_l, ys_r = [], []
    for _ in range(24):
        rt.tick(1 / 30)
        b, _d = rt.build_full(Pw, "walk", rt.t)
        M = rig.compute(b)
        ys_l.append(float(M["leftHand"][1, 3]))
        ys_r.append(float(M["rightHand"][1, 3]))
    span_l = max(ys_l) - min(ys_l)
    span_r = max(ys_r) - min(ys_r)
    check("左手有前后行程（>15mm）", span_l > 0.015, f"{span_l*1000:.0f}mm")

    # 手的世界 y 里混着「人往前走」的公共斜坡，必须先去掉趋势再比相位
    def detrend(a):
        x = np.arange(len(a), dtype=float)
        c = np.polyfit(x, np.asarray(a, dtype=float), 1)
        return np.asarray(a, dtype=float) - np.polyval(c, x)

    corr = float(np.corrcoef(detrend(ys_l), detrend(ys_r))[0, 1])
    check("去趋势后左右负相关（反相）", corr < -0.5, f"corr={corr:.2f}")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
