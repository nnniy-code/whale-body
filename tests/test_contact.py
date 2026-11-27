"""tests/test_contact.py — P2 验收。

怎么测：python tests/test_contact.py
预期现象：[contact] ALL PASS
调参位置：core/contact.py 的 DEFAULT_TOL_MM / PENETRATION_TOL_MM
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contact import (  # noqa: E402
    detect, dual_support_ratio, phase_region, stance_frames,
)

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    print("[1] 站立：双脚着地")
    c = detect({"leftFoot": 0.000, "rightFoot": 0.000})
    check("左 ground", c["leftFoot"].grounded)
    check("右 ground", c["rightFoot"].grounded)
    check("高度 0", abs(c["leftFoot"].height_mm) < 1e-9)

    print("[2] 抬起 200mm：该脚离地，另一只不动")
    c = detect({"leftFoot": 0.200, "rightFoot": 0.000})
    check("左 swing", not c["leftFoot"].grounded, f"h={c['leftFoot'].height_mm:.1f}")
    check("右 stance", c["rightFoot"].grounded)

    print("[3] 阈值边界（35mm）")
    c = detect({"leftFoot": 0.0349, "rightFoot": 0.0351})
    check("34.9mm 算着地", c["leftFoot"].grounded)
    check("35.1mm 算离地", not c["rightFoot"].grounded)

    print("[4] 穿地检测")
    c = detect({"leftFoot": -0.010, "rightFoot": 0.000})
    check("左穿地 10mm", abs(c["leftFoot"].penetration_mm - 10.0) < 1e-6,
          f"{c['leftFoot'].penetration_mm:.2f}")
    check("左不 ground（穿地不算着地）", not c["leftFoot"].grounded)
    check("below 标志", c["leftFoot"].below)

    print("[5] 轻微穿地 2mm：仍算着地（<3mm 容差）")
    c = detect({"leftFoot": -0.002, "rightFoot": 0.000})
    check("2mm 不算穿地违规", c["leftFoot"].grounded,
          f"pen={c['leftFoot'].penetration_mm:.2f}")

    print("[6] 相位区间")
    check("walk duty=0.62 phase=0.3 -> stance", phase_region(0.3, 0.62) == "stance")
    check("walk duty=0.62 phase=0.8 -> swing", phase_region(0.8, 0.62) == "swing")
    check("run duty=0.35 phase=0.2 -> stance", phase_region(0.2, 0.35) == "stance")
    check("run duty=0.35 phase=0.9 -> swing", phase_region(0.9, 0.35) == "swing")
    check("phase 取模", phase_region(1.3, 0.62) == "stance")
    try:
        phase_region(0.5, 1.5)
        check("非法 duty 抛错", False)
    except ValueError:
        check("非法 duty 抛错", True)

    print("[7] 连续帧统计（walk 双支撑）")
    frames = []
    for i in range(100):
        p = (i / 100) % 1.0
        lz = 0.0 if p < 0.62 else 0.10
        rz = 0.0 if ((p + 0.5) % 1.0) < 0.62 else 0.10
        frames.append(detect({"leftFoot": lz, "rightFoot": rz}))
    ratio = dual_support_ratio(frames)
    # duty=0.62 时双支撑占比 = 2*duty - 1 = 0.24，不是 >0.5。
    # gait-guide 的「walk duty>0.5」指的是单脚支撑相占比（=有双支撑相），
    # 不是「双脚同时着地的帧超过一半」。
    check("双支撑占比 ≈ 0.24", abs(ratio - 0.24) < 0.03, f"{ratio:.3f}")
    check("双支撑相存在", ratio > 0.1, f"{ratio:.3f}")

    st = stance_frames(frames)
    check("两脚都有支撑帧", "leftFoot" in st and "rightFoot" in st)
    check("支撑帧数相近（对称）", abs(len(st["leftFoot"]) - len(st["rightFoot"])) <= 2,
          f"{len(st['leftFoot'])} vs {len(st['rightFoot'])}")

    print("[8] run 模式（duty 0.35）双支撑更低")
    frames = []
    for i in range(100):
        p = (i / 100) % 1.0
        lz = 0.0 if p < 0.35 else 0.12
        rz = 0.0 if ((p + 0.5) % 1.0) < 0.35 else 0.12
        frames.append(detect({"leftFoot": lz, "rightFoot": rz}))
    r2 = dual_support_ratio(frames)
    check("run 双支撑 < 0.2", r2 < 0.2, f"{r2:.3f}")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
