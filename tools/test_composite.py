"""tools/test_composite.py — 验证「多动作叠加 + 过渡」接进 runtime。

怎么测：python tools/test_composite.py
预期现象：
  1. walk + wave 叠加：脚在走路、右手在挥
  2. 切换动作时有 200ms 过渡（不瞬移）
  3. 权高者赢（wave 的 weight 压过另一个手臂动作）
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import pose_gen  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402

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

    print("[1] 单动作（对照）")
    r1 = rt.handle_composite([{"intent": "walk"}], now=time.perf_counter())
    check("解出", r1["basis"] is not None)
    check("无叠加层", r1["layers"] == 0, str(r1["layers"]))
    # v1.9：handle_composite 返回的是 **basis**（局部矩阵），读世界坐标必须先 FK。
    # basis 的平移基本为 0；世界矩阵的 z 就是手的高度（~0.9m）。这里卡住接口语义。
    check("返回 basis 而非世界矩阵",
          abs(float(r1["basis"]["rightHand"][2, 3])) < 1e-6,
          f"basis_z={float(r1['basis']['rightHand'][2, 3]):.4f}")
    M1 = rf.compute(r1["basis"])
    walk_hand = float(M1["rightHand"][2, 3])
    walk_foot = float(M1["rightFoot"][2, 3])
    print(f"    walk  hand_z={walk_hand:.3f} foot_z={walk_foot:.3f}")

    print("[2] walk + wave 叠加")
    t0 = time.perf_counter()
    r2 = rt.handle_composite([
        {"intent": "walk"},
        {"intent": "wave", "groups": ["arm.right"], "weight": 1.0},
    ], now=t0)
    check("两层", r2["layers"] == 1, str(r2["layers"]))
    check("两个都解出", all(e is not None for e in r2["entries"]), str(r2["sources"]))
    # basis 是局部矩阵：直接读 z 会是 0，必须 FK 一次才是世界坐标
    M2 = rf.compute(r2["basis"])
    mh = float(M2["rightHand"][2, 3])
    mf = float(M2["rightFoot"][2, 3])
    print(f"    merged hand_z={mh:.3f} foot_z={mf:.3f}")
    check("右手举高（挥手）", mh > 0.85, f"{mh:.3f}")
    check("脚保持走路", abs(mf - walk_foot) < 0.005, f"{mf:.3f} vs {walk_foot:.3f}")
    check("右手和纯走路不同", abs(mh - walk_hand) > 0.15, f"{mh:.3f} vs {walk_hand:.3f}")

    print("[3] 过渡：不瞬移")
    b0 = rt.current_basis(now=t0)
    M0 = rf.compute(b0)
    h0 = M0["rightHand"][0:3, 3].copy()
    worst = 0.0
    prev = h0
    for i in range(1, 41):
        now = t0 + i * 0.005
        M = rf.compute(rt.current_basis(now=now))
        h = M["rightHand"][0:3, 3]
        worst = max(worst, float(np.linalg.norm(h - prev)))
        prev = h.copy()
    print(f"    最大单步 {worst*1000:.1f}mm")
    check("200ms 内无跳变（<60mm/帧）", worst < 0.06, f"{worst*1000:.1f}mm")
    b_end = rt.current_basis(now=t0 + 0.5)
    h_end = float(rf.compute(b_end)["rightHand"][2, 3])
    check("过渡结束到达目标", abs(h_end - mh) < 0.01, f"{h_end:.3f} vs {mh:.3f}")
    check("过渡结束后仍是 basis",
          abs(float(rt.current_basis(now=t0 + 0.5)["rightHand"][2, 3])) < 1e-6)

    print("[4] 权重：大的赢")
    r3 = rt.handle_composite([
        {"intent": "walk"},
        {"intent": "wave", "groups": ["arm.right"], "weight": 0.3},
        {"intent": "stretch", "groups": ["arm.right"], "weight": 0.9},
    ], now=time.perf_counter())
    check("三层里两层叠加", r3["layers"] == 2, str(r3["layers"]))
    check("有冲突记录（arm.right 都被改）", len(r3["conflicts"]) >= 2, str(r3["conflicts"]))
    win = r3["report"].get("winner", {})
    check("stretch（weight 0.9）赢", win.get("rightUpperArm") == 1, str(win.get("rightUpperArm")))

    print("[5] 全部失败 → 返回空")
    r4 = rt.handle_composite([
        {"intent": "nonexistent_family_xyz"},
    ], now=time.perf_counter())
    check("basis 为 None", r4["basis"] is None)
    check("有缺口", len(r4["gaps"]) == 1, str(len(r4["gaps"])))

    print("[6] 空列表")
    r5 = rt.handle_composite([], now=time.perf_counter())
    check("返回空结构", r5["basis"] is None and r5["entries"] == [])

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




