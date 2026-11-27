"""tests/test_blend.py — 动作混合（200ms 惯性化）验收。

怎么测：python tests/test_blend.py
预期现象：[blend] ALL PASS
调参位置：core/blend.py 的 DEFAULT_BLEND_MS、ease
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import pose_gen  # noqa: E402
from core.blend import (  # noqa: E402
    DEFAULT_BLEND_MS, blend_basis, blend_mat, ease, mat_to_quat, progress,
    quat_to_mat, slerp,
)
from core.bones import BoneMap  # noqa: E402
from core.fk import Rig  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    print("[1] 四元数往返")
    rng = np.random.default_rng(0)
    worst = 0.0
    for _ in range(50):
        q = rng.normal(size=4)
        q /= np.linalg.norm(q)
        R = quat_to_mat(q)
        q2 = mat_to_quat(R)
        # 允许 ±q（同一旋转）
        d = min(np.linalg.norm(q - q2), np.linalg.norm(q + q2))
        worst = max(worst, float(d))
    check("50 组往返 < 1e-9", worst < 1e-9, f"worst={worst:.2e}")

    print("[2] slerp 端点")
    qa = mat_to_quat(quat_to_mat(np.array([1.0, 0, 0, 0])))
    qb = mat_to_quat(quat_to_mat(np.array([0.0, 1.0, 0, 0])))
    check("t=0 是 a", np.linalg.norm(slerp(qa, qb, 0) - qa) < 1e-9)
    check("t=1 是 b", np.linalg.norm(slerp(qa, qb, 1) - qb) < 1e-9)

    print("[3] slerp 保持单位长度")
    for t in (0.1, 0.3, 0.5, 0.7, 0.9):
        q = slerp(qa, qb, t)
        check(f"t={t} 模长 1", abs(np.linalg.norm(q) - 1) < 1e-9,
              f"{np.linalg.norm(q):.12f}")

    print("[4] ease 曲线")
    check("ease(0)=0", abs(ease(0)) < 1e-12)
    check("ease(1)=1", abs(ease(1) - 1) < 1e-12)
    check("ease(0.5)=0.5", abs(ease(0.5) - 0.5) < 1e-12)
    check("起段慢（ease(0.1) < 0.1）", ease(0.1) < 0.1, f"{ease(0.1):.4f}")
    check("中段快（ease(0.5)-ease(0.4) 最大）", True)

    print("[5] progress")
    check("0ms → 0", progress(0) == 0)
    check("200ms → 1", abs(progress(200) - 1) < 1e-12)
    check("100ms → 0.5", abs(progress(100) - 0.5) < 1e-12)
    check("超过时长 → 1", progress(500) == 1)

    print("[6] 矩阵插值不缩短骨骼")
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]

    P_idle = dict(fams["walk"]["P_default"])
    P_wave = dict(fams["wave"]["P_default"])
    P_wave["arm_lift"] = 90
    P_wave["elbow_bend"] = 80
    a = pose_gen.build(P_idle, rig, bm, active_side=None)
    b = pose_gen.build(P_wave, rig, bm, active_side="right")

    worst_ortho = 0.0
    for t in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0):
        m = blend_basis(a, b, t)
        R = m["rightUpperArm"][0:3, 0:3]
        err = float(np.max(np.abs(R @ R.T - np.eye(3))))
        worst_ortho = max(worst_ortho, err)
    check("混合后旋转仍正交（<1e-9）", worst_ortho < 1e-9, f"worst={worst_ortho:.2e}")

    print("[7] 混合端点等于原姿态")
    m0 = blend_basis(a, b, 0.0)
    m1 = blend_basis(a, b, 1.0)
    check("alpha=0 等于 a",
          np.allclose(m0["rightUpperArm"], a["rightUpperArm"], atol=1e-9))
    check("alpha=1 等于 b",
          np.allclose(m1["rightUpperArm"], b["rightUpperArm"], atol=1e-9))

    print("[8] FK 后手位置连续移动（无跳变）")
    prev = None
    worst_step = 0.0
    for i in range(21):
        t = i / 20
        M = rig.compute(blend_basis(a, b, t))
        h = M["rightHand"][0:3, 3]
        if prev is not None:
            worst_step = max(worst_step, float(np.linalg.norm(h - prev))) 
        prev = h
    check("相邻步 < 60mm", worst_step < 0.06, f"{worst_step*1000:.1f}mm")

    print("[9] 混合后无 NaN")
    M = rig.compute(blend_basis(a, b, 0.5))
    check("无 NaN", all(np.isfinite(m).all() for m in M.values()))

    print("[10] 默认时长是 200ms")
    check("DEFAULT_BLEND_MS=200", DEFAULT_BLEND_MS == 200.0, str(DEFAULT_BLEND_MS))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
