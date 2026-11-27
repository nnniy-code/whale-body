"""tests/test_gait.py — P6 步态验收。

怎么测：python tests/test_gait.py
预期现象：[gait] ALL PASS（支撑相零滑移、摆动抬脚、run 有腾空）
调参位置：config/seed_families.json 的 G
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.gait import foot_targets, is_airborne, phase_of, stance_or_swing  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"),
                          encoding="utf-8"))["families"]
    W = fams["walk"]["G"]
    R = fams["run"]["G"]
    start = {"left": [0.076, -0.068, 0.080], "right": [-0.076, -0.068, 0.080]}

    print("[1] 支撑相脚锁（零滑移）")
    period, duty = W["period"], W["duty"]
    t0 = 0.05 * period                     # 左腿刚进入支撑
    worst = 0.0
    for i in range(10):
        t = t0 + i * 0.01 * period
        p = phase_of(t, period)
        if stance_or_swing(p, duty) != "stance":
            continue
        a = foot_targets(W, t, start)["left"]
        b = foot_targets(W, t0, start)["left"]
        worst = max(worst, max(abs(a[k] - b[k]) for k in range(3)))
    check("左脚位置不变", worst < 1e-9, f"{worst:.2e}m")

    print("[2] 摆动相有抬脚")
    maxlift = 0.0
    for i in range(100):
        t = i * period / 100
        p = phase_of(t, period)
        if stance_or_swing(p, duty) != "swing":
            continue
        z = foot_targets(W, t, start)["left"][2]
        maxlift = max(maxlift, z - start["left"][2])
    check("抬脚接近 foot_lift", abs(maxlift - W["foot_lift"]) < 0.02,
          f"{maxlift*1000:.1f}mm vs {W['foot_lift']*1000:.0f}mm")

    print("[3] 双支撑占比 = 2×duty−1")
    n_dual = 0
    N = 200
    for i in range(N):
        t = i * period / N
        p = phase_of(t, period)
        # 用相位判定，不用高度——摆动相起落段高度会低于 35mm，会被误算成着地
        if (p % 1.0) < duty and ((p + 0.5) % 1.0) < duty:
            n_dual += 1
    ratio = n_dual / N
    expect = max(0.0, 2 * duty - 1)
    check(f"≈{expect:.2f}", abs(ratio - expect) < 0.03, f"{ratio:.3f}")

    print("[4] run 有腾空相")
    n_air = 0
    for i in range(N):
        t = i * R["period"] / N
        tg = foot_targets(R, t, start)
        if is_airborne(tg, 0.08, 35):
            n_air += 1
    check("存在双脚离地帧", n_air > 0, f"{n_air}/{N}")
    check("腾空占比 < 40%", n_air / N < 0.4, f"{n_air/N:.2f}")

    print("[5] 周期连续（无跳变）")
    prev = None
    worst_jump = 0.0
    for i in range(500):
        t = i * period / 500
        tg = foot_targets(W, t, start)
        if prev is not None:
            for k in ("left", "right"):
                d = sum((tg[k][j] - prev[k][j]) ** 2 for j in range(3)) ** 0.5
                worst_jump = max(worst_jump, d)
        prev = tg
    check("相邻采样位移 < 20mm", worst_jump < 0.02, f"{worst_jump*1000:.1f}mm")

    print("[6] stride 为负 = 倒退")
    G_back = dict(W)
    G_back["stride"] = -0.55
    ys = [foot_targets(G_back, i * period / 50, start)["left"][1] for i in range(50)]
    check("y 增大（往 +Y 退）", ys[-1] > ys[0], f"{ys[0]:.3f} -> {ys[-1]:.3f}")

    print("[7] 零 stride 原地踏步")
    G_in = dict(W)
    G_in["stride"] = 0.0
    ys = [foot_targets(G_in, i * period / 50, start)["left"][1] for i in range(50)]
    check("y 不变", max(ys) - min(ys) < 1e-9, f"{max(ys)-min(ys):.2e}")

    print("[8] 转向")
    G_turn = dict(W)
    G_turn["turn_rate"] = 90.0
    p0 = foot_targets(G_turn, 0.0, start)["left"]
    p1 = foot_targets(G_turn, period, start)["left"]
    check("方向改变", abs(p1[0] - p0[0]) > 0.01, f"dx={p1[0]-p0[0]:.3f}")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
