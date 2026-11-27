"""tests/test_gestures.py — 预设手势（关键帧 + Hermite 插值）。

怎么测：python tests/test_gestures.py
预期现象：[gestures] ALL PASS
调参位置：core/gestures.py 的 GESTURES
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bones import BoneMap  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.gestures import GESTURES, list_gestures, sample, sample_joint  # noqa: E402

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
    base_P = fams["wave"]["P_default"]

    print("[1] 手势清单")
    names = list_gestures()
    check("至少 5 个", len(names) >= 5, str(names))
    check("有 wave", "wave" in names)

    print("[2] wave 采样：手会上下动")
    zs = []
    for i in range(20):
        t = 1.4 * i / 20
        M = sample("wave", t, base_P, rig, bm)["M"]
        zs.append(float(M["rightHand"][2, 3]))
    check("手高有变化", max(zs) - min(zs) > 0.03, f"range={max(zs)-min(zs):.3f}m")
    check("手在肩以上", min(zs) > 0.7, f"min={min(zs):.3f}")

    print("[3] wave 采样：左手不动")
    ls = []
    for i in range(10):
        M = sample("wave", 1.4 * i / 10, base_P, rig, bm)["M"]
        ls.append(float(M["leftHand"][2, 3]))
    check("左手几乎不变", max(ls) - min(ls) < 0.02, f"range={max(ls)-min(ls):.3f}")

    print("[4] 循环边界连续")
    a = sample("wave", 1.39, base_P, rig, bm)["M"]
    b = sample("wave", 0.0, base_P, rig, bm)["M"]
    d = float(np.linalg.norm(a["rightHand"][0:3, 3] - b["rightHand"][0:3, 3]))
    check("首尾差 < 30mm", d < 0.03, f"{d*1000:.1f}mm")

    print("[5] 采样间隔小（无跳变）")
    prev = None
    worst = 0.0
    for i in range(200):
        M = sample("wave", 1.4 * i / 200, base_P, rig, bm)["M"]
        if prev is not None:
            worst = max(worst, float(np.linalg.norm(
                M["rightHand"][0:3, 3] - prev["rightHand"][0:3, 3])))
        prev = M
    check("相邻帧 < 25mm", worst < 0.025, f"{worst*1000:.1f}mm")

    print("[6] nod 只动头")
    hs, rs = [], []
    for i in range(10):
        M = sample("nod", 0.7 * i / 10, base_P, rig, bm)["M"]
        hs.append(float(M["head"][2, 3]))
        rs.append(float(M["rightHand"][2, 3]))
    check("手没动", max(rs) - min(rs) < 0.005, f"{max(rs)-min(rs):.4f}")
    check("有采样", len(hs) == 10)

    print("[7] 一次性手势到末尾停住")
    a = sample("stretch", 1.6, base_P, rig, bm)["M"]
    b = sample("stretch", 5.0, base_P, rig, bm)["M"]
    d = float(np.linalg.norm(a["rightHand"][0:3, 3] - b["rightHand"][0:3, 3]))
    check("超过 duration 停在末帧", d < 1e-6, f"{d*1000:.3f}mm")

    print("[8] 未知手势抛错")
    try:
        sample("nonexistent", 0, base_P, rig, bm)
        check("抛 KeyError", False)
    except KeyError:
        check("抛 KeyError", True)

    print("[9] 全部手势都能采样")
    ok = 0
    for g in list_gestures():
        try:
            if "joint_keys" in GESTURES[g]:
                sample_joint(g, 0.1, rig, bm)
            else:
                sample(g, 0.1, base_P, rig, bm)
            ok += 1
        except Exception as e:
            print(f"    {g} 失败: {e}")
    check("全部可采样", ok == len(list_gestures()), f"{ok}/{len(list_gestures())}")

    print("[10] 关节角手势 wave_joint")
    hs, es = [], []
    for i in range(21):
        t = 1.4 * i / 21
        M = sample_joint("wave_joint", t, rig, bm)["M"]
        hs.append(float(M["rightHand"][2, 3]))
        es.append(float(M["rightLowerArm"][2, 3]))
    msg = f"hand_range={max(hs)-min(hs):.3f} elbow_range={max(es)-min(es):.3f}"
    check("手上下摆 > 100mm", max(hs) - min(hs) > 0.10, msg)
    check("肘也动（跟着上臂）", max(es) - min(es) > 0.05, msg)
    check("手始终高于肘", all(h > e for h, e in zip(hs, es)), msg)
    check("肘接近肩高（±80mm）",
          all(abs(e - float(sample_joint("wave_joint", 0, rig, bm)["M"]["rightShoulder"][2, 3])) < 0.08
              for e in es), msg)
    check("手在右侧（x<0，不跨中线）",
          all(float(sample_joint("wave_joint", 1.4 * i / 21, rig, bm)["M"]["rightHand"][0, 3]) < 0.0
              for i in range(21)))

    print("[11] 关节角手势：循环边界连续")
    a = sample_joint("wave_joint", 1.39, rig, bm)["M"]
    b = sample_joint("wave_joint", 0.0, rig, bm)["M"]
    d = float(np.linalg.norm(a["rightHand"][0:3, 3] - b["rightHand"][0:3, 3]))
    check("首尾差 < 30mm", d < 0.03, f"{d*1000:.1f}mm")

    print("[12] 关节角手势：无跳变")
    prev = None
    worst = 0.0
    for i in range(200):
        M = sample_joint("wave_joint", 1.4 * i / 200, rig, bm)["M"]
        if prev is not None:
            worst = max(worst, float(np.linalg.norm(
                M["rightHand"][0:3, 3] - prev["rightHand"][0:3, 3])))
        prev = M
    check("相邻帧 < 25mm", worst < 0.025, f"{worst*1000:.1f}mm")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

