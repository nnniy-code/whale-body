"""tests/test_trajectory.py — P0 验收。

怎么测：python tests/test_trajectory.py
预期现象：[trajectory] ALL PASS (7 checks)
调参位置：core/trajectory.py
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.trajectory import (  # noqa: E402
    build_track,
    check_c1,
    find_velocity_plateaus,
    hermite,
    hermite_vel,
    quintic,
    quintic_acc,
    quintic_vel,
    sample_uniform,
)

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def test_hermite_basis() -> None:
    print("[1] Hermite 端点与速度")
    p0, p1, v0, v1, T = 0.3, -1.7, 2.5, -0.8, 1.4
    check("h(0)=p0", abs(hermite(p0, v0, p1, v1, T, 0.0) - p0) < 1e-12)
    check("h(1)=p1", abs(hermite(p0, v0, p1, v1, T, 1.0) - p1) < 1e-12)
    check("h'(0)=v0", abs(hermite_vel(p0, v0, p1, v1, T, 0.0) - v0) < 1e-9)
    check("h'(1)=v1", abs(hermite_vel(p0, v0, p1, v1, T, 1.0) - v1) < 1e-9)


def test_track_endpoints() -> None:
    print("[2] 轨道端点")
    tr = build_track([0.0, 0.6, 1.4], [0.0, 1.0, -0.5])
    check("t=0", abs(tr.value_at(0.0) - 0.0) < 1e-12)
    check("t=end", abs(tr.value_at(tr.duration) - (-0.5)) < 1e-12)


def test_track_endpoint_velocity() -> None:
    print("[3] 端点速度可指定")
    tr = build_track([0.0, 1.0], [0.0, 1.0], start_v=2.0, end_v=-1.0)
    check("start_v", abs(tr.velocity_at(0.0) - 2.0) < 1e-9,
          f"got {tr.velocity_at(0.0):.6f}")
    check("end_v", abs(tr.velocity_at(1.0) - (-1.0)) < 1e-9,
          f"got {tr.velocity_at(1.0):.6f}")


def test_c1_continuity() -> None:
    print("[4] 段间接缝 C1 连续")
    tr = build_track([0.0, 0.8, 1.6, 2.4], [0.0, 1.0, 0.2, 1.5])
    worst = 0.0
    for t, vl, vr in check_c1(tr):
        worst = max(worst, abs(vl - vr))
    check("接缝速度跳变 <1e-6", worst < 1e-6, f"worst={worst:.3e}")


def test_no_plateau() -> None:
    print("[5] 无 0 速平台（非端点）")
    # 典型挥手：三段，来回摆
    tr = build_track([0.0, 0.4, 0.8, 1.2], [0.0, 1.0, 0.0, 1.0])
    plateaus = find_velocity_plateaus(tr, fps=30, min_frames=3)
    check("无平台", len(plateaus) == 0, f"found={plateaus}")


def test_quintic() -> None:
    print("[6] 五次曲线端点速度/加速度为 0")
    check("s(0)=0", abs(quintic(0.0)) < 1e-12)
    check("s(1)=1", abs(quintic(1.0) - 1.0) < 1e-12)
    check("s'(0)=0", abs(quintic_vel(0.0)) < 1e-12)
    check("s'(1)=0", abs(quintic_vel(1.0)) < 1e-12)
    check("s''(0)=0", abs(quintic_acc(0.0)) < 1e-12)
    check("s''(1)=0", abs(quintic_acc(1.0)) < 1e-12)


def test_sampling() -> None:
    print("[7] 固定帧率采样")
    tr = build_track([0.0, 1.0], [0.0, 1.0])
    s = sample_uniform(tr, fps=30)
    check("样本数 = 30*dur+1", len(s) == 31, f"got {len(s)}")
    check("首样本 t=0", abs(s[0][0]) < 1e-12)
    check("末样本 t=dur", abs(s[-1][0] - tr.duration) < 1e-9)


def test_reject_power_ease() -> None:
    print("[8] 禁幂缓动（源码扫描）")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hits = []
    for dirpath, _dirnames, filenames in os.walk(root):
        if os.sep + ".git" in dirpath:
            continue
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dirpath, fn)
            if os.path.abspath(p) == os.path.abspath(__file__):
                continue
            with open(p, "r", encoding="utf-8", errors="ignore") as fh:
                for i, line in enumerate(fh, 1):
                    if "**0.55" in line or "** 0.55" in line:
                        hits.append(f"{fn}:{i}")
    check("无 t**0.55", len(hits) == 0, str(hits))


def main() -> int:
    test_hermite_basis()
    test_track_endpoints()
    test_track_endpoint_velocity()
    test_c1_continuity()
    test_no_plateau()
    test_quintic()
    test_sampling()
    test_reject_power_ease()

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
