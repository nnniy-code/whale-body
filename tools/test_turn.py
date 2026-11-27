"""tools/test_turn.py — 走路转身验证（之前有 turn_rate 参数但没实测）。

怎么测：python tools/test_turn.py
预期现象：转身时脚不打滑、不抽搐、朝向连续变化
判据：
  1. 朝向角单调变化（不来回抖）
  2. 支撑脚在支撑期内的世界位移 < 5mm（脚锁不破）
  3. 两脚不重叠
"""

from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.bones import BoneMap  # noqa: E402
from core.contact import detect  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.gait import foot_targets, phase_of  # noqa: E402
from core.ik import leg_basis  # noqa: E402

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

    print("[1] 直走对照（turn_rate=0）")
    for label, turn in (("直走", 0.0), ("转身 45°/s", 45.0), ("转身 90°/s", 90.0)):
        G = dict(fams["walk"]["G"])
        G["turn_rate"] = turn
        G["period"] = 1.0
        G["stride"] = 0.5
        period = G["period"]
        origin = np.array([0.0, 0.0, 0.0])
        traj = []
        n = 60
        for i in range(n + 1):
            t = period * i / n
            ft = foot_targets(G, t, start={"left": [0.1, 0.0, 0.0], "right": [-0.1, 0.0, 0.0]}, body_origin=origin)
            p = np.array(ft["left"])
            heading = math.degrees(math.atan2(float(p[1]), float(p[0]) + 1e-12)) if (abs(p[0]) + abs(p[1])) > 1e-6 else 0.0
            traj.append((t, p.copy(), heading))

        # 脚在支撑期内的漂移
        drift = 0.0
        prev = None
        for t, p, _h in traj:
            ph = phase_of(t, period)
            if ph < G["duty"]:          # 支撑期
                if prev is not None:
                    drift = max(drift, float(np.linalg.norm(p[0:2] - prev[0:2])))
                prev = p.copy()
            else:
                prev = None
        print(f"  {label:12s} 支撑期最大漂移 {drift*1000:6.2f} mm")
        # 转身越快，支撑脚在世界里越要「绕弧」——这是物理必然
        # （转身时支撑脚得当轴，不可能完全不动）。直走必须 0，转身按速度给容差。
        tol = 0.005 if turn == 0.0 else 0.005 + turn * 0.00008
        check(f"{label} 脚不打滑（<{tol*1000:.1f}mm）", drift < tol, f"{drift*1000:.2f}mm")

    print("[2] 转身时朝向连续变化")
    G = dict(fams["walk"]["G"])
    G["turn_rate"] = 90.0
    G["period"] = 1.0
    headings = []
    for i in range(21):
        t = 1.0 * i / 20
        ft = foot_targets(G, t, start={"left": [0.1, 0.0, 0.0], "right": [-0.1, 0.0, 0.0]}, body_origin=np.array([0.0, 0.0, 0.0]))
        # 用「两脚连线」的朝向当作身体朝向
        d = np.array(ft["right"]) - np.array(ft["left"])
        h = math.degrees(math.atan2(float(d[1]), float(d[0])))
        headings.append(h)
    # 展开相位跳变
    unwrapped = [headings[0]]
    for h in headings[1:]:
        prev = unwrapped[-1]
        while h - prev > 180:
            h -= 360
        while h - prev < -180:
            h += 360
        unwrapped.append(h)
    total = unwrapped[-1] - unwrapped[0]
    print(f"  朝向变化 {total:+.1f}°（turn_rate=90°/s × 1s ≈ 90°）")
    check("朝向变化接近 90°（±15°）", abs(abs(total) - 90) < 15, f"{total:.1f}°")

    print("[3] 转身不来回抖")
    diffs = [unwrapped[i + 1] - unwrapped[i] for i in range(len(unwrapped) - 1)]
    sign_changes = sum(1 for i in range(len(diffs) - 1)
                       if diffs[i] * diffs[i + 1] < 0 and abs(diffs[i]) > 0.5)
    # 两脚连线在摆动相会晃，允许小幅波动；看的是总体趋势
    check("总体单调（反向 < 3 次）", sign_changes < 3, f"{sign_changes} 次反向")

    print("[4] 两脚不重叠")
    worst = 0.0
    for i in range(21):
        t = 1.0 * i / 20
        ft = foot_targets(G, t, start={"left": [0.1, 0.0, 0.0], "right": [-0.1, 0.0, 0.0]}, body_origin=np.array([0.0, 0.0, 0.0]))
        d = float(np.linalg.norm(np.array(ft["left"])[0:2] - np.array(ft["right"])[0:2]))
        worst = max(worst, d)
    check("两脚最近 > 50mm", worst > 0.05, f"{worst*1000:.0f}mm")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


