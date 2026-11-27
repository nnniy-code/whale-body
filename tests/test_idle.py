"""tests/test_idle.py — L0 底噪：呼吸 + 头部视线跟随。

设计依据：v1.9「runtime 里写着『视线跟随由 L0 处理』，但 L0 根本不存在」

怎么测：python tests/test_idle.py
预期现象：[idle] ALL PASS
调参位置：core/idle.py（breath_deg / breath_period / max_yaw_deg）
"""

from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bones import BoneMap  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.idle import IdleMotion  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def rot_deg(m: np.ndarray) -> float:
    tr = float(m[0, 0] + m[1, 1] + m[2, 2])
    return math.degrees(math.acos(max(-1.0, min(1.0, (tr - 1.0) * 0.5))))


def angle_between(a, b) -> float:
    a = a / (np.linalg.norm(a) + 1e-12)
    b = b / (np.linalg.norm(b) + 1e-12)
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(a, b))))))


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    basis = {n: np.eye(4) for n in rig.names}
    local_fwd = np.asarray(rig.rest["head"])[0:3, 0:3].T @ np.array([0.0, -1.0, 0.0])

    print("[1] 呼吸：胸骨周期性起伏，幅度受控")
    idle = IdleMotion(rig, bm, breath_deg=0.9, breath_period=4.0)
    chest_a, spine_a = [], []
    for _ in range(120):                       # 4 秒 @30fps
        out = idle.step(basis, 1 / 30)
        chest_a.append(rot_deg(np.asarray(out["chest"])[0:3, 0:3]))
        spine_a.append(rot_deg(np.asarray(out["spine"])[0:3, 0:3]))
    check("幅度 ≈ 0.9°", 0.6 < max(chest_a) < 1.2, f"{max(chest_a):.2f}°")
    check("脊柱也有（约一半）", 0.2 < max(spine_a) < 0.7, f"{max(spine_a):.2f}°")

    print("[2] 呼吸一个周期后回到起点")
    idle2 = IdleMotion(rig, bm, breath_deg=0.9, breath_period=2.0)
    a = idle2.step(basis, 0.0)
    for _ in range(60):                        # 2 秒
        b = idle2.step(basis, 1 / 30)
    d = rot_deg(np.asarray(b["chest"])[0:3, 0:3])
    check("回位误差 < 0.05°", d < 0.05, f"{d:.4f}°")

    print("[3] 视线：正前方的目标要看得准")
    idle3 = IdleMotion(rig, bm, breath=False, max_yaw_deg=25.0)
    M0 = rig.compute(basis)
    hp = np.asarray(M0["head"])[0:3, 3]
    target = hp + np.array([0.0, -1.0, 0.05])
    idle3.set_look(target)
    out = idle3.step(basis, 1 / 30)
    M = rig.compute(out)
    fwd = np.asarray(M["head"])[0:3, 0:3] @ local_fwd
    err = angle_between(fwd, target - hp)
    check("朝向误差 < 2°", err < 2.0, f"{err:.2f}°")

    print("[4] 限幅：背后的目标只转 max_yaw")
    idle4 = IdleMotion(rig, bm, breath=False, max_yaw_deg=18.0)
    target2 = hp + np.array([0.0, 1.0, 0.0])    # 正后方
    idle4.set_look(target2)
    out = idle4.step(basis, 1 / 30)
    M = rig.compute(out)
    fwd = np.asarray(M["head"])[0:3, 0:3] @ local_fwd
    turned = angle_between(fwd, np.asarray(M0["head"])[0:3, 0:3] @ local_fwd)
    check("实际转角 ≈ 18°", abs(turned - 18.0) < 0.5, f"{turned:.2f}°")
    check("背对时仍然背对（没乱转）", angle_between(fwd, target2 - hp) > 150,
          f"{angle_between(fwd, target2 - hp):.1f}°")

    print("[5] 全关 = 原样")
    off = IdleMotion(rig, bm, breath=False)
    same = off.step(basis, 1 / 30)
    diff = max(float(np.max(np.abs(np.asarray(same[k]) - np.asarray(basis[k]))))
               for k in basis)
    check("不改 basis", diff < 1e-12, f"{diff:.2e}")

    print("[6] dt<=0 不崩")
    idle5 = IdleMotion(rig, bm)
    check("dt=0 原样", idle5.step(basis, 0.0) is basis)

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
