"""tests/test_secondary.py — 二次运动：头发/裙摆的惯性滞后。

设计依据：v1.9「209 骨里 134 根二次骨，v1.8 全是死的」

怎么测：python tests/test_secondary.py
预期现象：[secondary] ALL PASS
调参位置：core/secondary.py（stiffness / damping / gain / max_deg / scale）
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.collision import region_of  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.secondary import DEFAULT_GROUPS, SecondaryMotion  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def world_rot(axis: str, deg: float) -> np.ndarray:
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    m = np.eye(4)
    if axis == "z":
        m[0:3, 0:3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    elif axis == "x":
        m[0:3, 0:3] = [[1, 0, 0], [0, c, -s], [0, s, c]]
    return m


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])

    print("[1] 建表：hair + skirt + ribbon")
    sm = SecondaryMotion(rig)
    by = {}
    for n in sm.bones:
        by[region_of(n)] = by.get(region_of(n), 0) + 1
    check("总数 140（hair 70 + skirt 64 + ribbon 6）", len(sm.bones) == 140, str(len(sm.bones)))
    check("按深度排好序", all(sm._depth[sm.bones[i]] <= sm._depth[sm.bones[i + 1]]
                              for i in range(len(sm.bones) - 1)))
    print(f"    分区 {by}")

    print("[2] 静止 → 零滞后（不能自己抖）")
    sm.reset()
    basis = {n: np.eye(4) for n in rig.names}
    worst = 0.0
    for _ in range(30):
        out = sm.step(basis, 1 / 30)
    for n in sm.bones:
        worst = max(worst, float(np.linalg.norm(sm.applied[n])))
    check("30 帧静止后滞后 ≈ 0", worst < 1e-6, f"{worst:.6f} rad")

    print("[3] 头突然转 25° → 头发反向滞后")
    sm.reset()
    for _ in range(5):
        sm.step(basis, 1 / 30)
    head_turn = {n: np.eye(4) for n in rig.names}
    head_turn["head"] = world_rot("z", 25.0)
    out = sm.step(head_turn, 1 / 30)
    snap = sm.snapshot()
    check("有滞后", snap["max_deg"] > 0.05, f"{snap['max_deg']:.2f}° @ {snap['bone']}")
    hair = [n for n in sm.bones if region_of(n) == "hair"]
    z_mean = float(np.mean([sm.applied[n][2] for n in hair]))
    check("方向与旋转相反（-Z）", z_mean < -1e-4, f"z={z_mean:+.4f}")

    print("[4] 钳位：狂甩也不会超过 max_deg")
    sm2 = SecondaryMotion(rig, max_deg=12.0)
    b = {n: np.eye(4) for n in rig.names}
    sm2.step(b, 1 / 30)
    worst = 0.0
    for i in range(40):
        b2 = dict(b)
        b2["hips"] = world_rot("z", 170.0 if i % 2 == 0 else -170.0)
        sm2.step(b2, 1 / 30)
        for n in sm2.bones:
            worst = max(worst, float(np.linalg.norm(sm2.applied[n])))
    check("最大滞后 ≤ 12°", np.degrees(worst) <= 12.0 + 1e-6, f"{np.degrees(worst):.2f}°")

    print("[5] 关掉就是原样（回退开关）")
    off = SecondaryMotion(rig, groups=())
    same = off.step(basis, 1 / 30)
    check("空 groups 不接管任何骨", len(off.bones) == 0, str(len(off.bones)))
    check("原样返回同一个对象", same is basis)

    print("[6] 性能：一帧多少 ms")
    sm3 = SecondaryMotion(rig)
    sm3.step(basis, 1 / 30)
    t0 = time.perf_counter()
    N = 30
    for _ in range(N):
        sm3.step(basis, 1 / 30)
    ms = (time.perf_counter() - t0) * 1000 / N
    check("单帧 < 8ms", ms < 8.0, f"{ms:.2f}ms/帧（130 骨 + 209 骨 FK）")

    print("[7] 默认分区覆盖对")
    check("DEFAULT_GROUPS 是 hair/skirt/ribbon", DEFAULT_GROUPS == ("hair", "skirt", "ribbon"))

    print("[8] 走路时的「风」：往前走得越快步拖得越多")
    import types
    base_b = {n: np.eye(4) for n in rig.names}

    def run_with_speed(v):
        sm = SecondaryMotion(rig)
        st = types.SimpleNamespace(vel=v, yaw=0.0, bob=0.0)
        for _ in range(20):                     # 让弹簧收敛
            sm.step(base_b, 1 / 30, st)
        return sm.snapshot()["max_deg"]

    still = run_with_speed(0.0)
    walk = run_with_speed(0.32)
    fast = run_with_speed(0.8)
    check("站着几乎不甩（<0.5°）", still < 0.5, f"{still:.2f}°")
    check("走起来明显（>3°）", walk > 3.0, f"{walk:.2f}°")
    check("越快越明显", fast > walk + 1.0, f"{fast:.2f}° vs {walk:.2f}°")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
