"""tests/test_overlay.py — 多动作叠加验收。

怎么测：python tests/test_overlay.py
预期现象：[overlay] ALL PASS
调参位置：core/overlay.py 的 OVERLAY_GROUPS
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import pose_gen  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.overlay import (  # noqa: E402
    OVERLAY_GROUPS, bones_of, conflicts, merge, merge_layers,
)

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

    print("[1] 骨组展开")
    check("arm.right 3 根", len(bones_of(["arm.right"])) == 3, str(bones_of(["arm.right"])))
    check("arms 6 根", len(bones_of(["arms"])) == 6)
    check("legs 6 根", len(bones_of(["legs"])) == 6)
    check("未知组按单骨处理", bones_of(["customBone"]) == ["customBone"])
    check("去重", len(bones_of(["arm.right", "arms"])) == 6, str(len(bones_of(["arm.right", "arms"]))))

    print("[2] 走路 + 挥手叠加")
    P_walk = dict(fams["walk"]["P_default"])
    P_wave = dict(fams["wave"]["P_default"])
    P_wave["arm_lift"] = 90
    P_wave["elbow_bend"] = 80

    base = pose_gen.build(P_walk, rig, bm, active_side=None)
    over = pose_gen.build(P_wave, rig, bm, active_side="right")
    merged, covered = merge(base, over, ["arm.right"])

    check("覆盖了 3 根骨", len(covered) == 3, str(covered))
    check("右臂来自 wave",
          np.allclose(merged["rightUpperArm"], over["rightUpperArm"]))
    check("左臂来自 walk",
          np.allclose(merged["leftUpperArm"], base["leftUpperArm"]))
    check("腿来自 walk",
          np.allclose(merged["leftUpperLeg"], base["leftUpperLeg"]))

    print("[3] 合并后 FK 正常")
    M = rig.compute(merged)
    check("无 NaN", all(np.isfinite(m).all() for m in M.values()))
    hr = M["rightHand"][0:3, 3]
    check("右手举高", float(hr[2]) > 0.80, f"z={hr[2]:.3f}")
    check("右手在体侧", float(hr[0]) < 0, f"x={hr[0]:.3f}")

    print("[4] 不叠加时腿不动（对照）")
    M_wave_only = rig.compute(over)
    M_walk_only = rig.compute(base)
    # 比 basis 而不是世界位置 —— walk 的 torso_lean 会带着手臂整体偏移
    check("右臂 basis 等于纯挥手",
          np.allclose(merged["rightUpperArm"], over["rightUpperArm"]) and
          np.allclose(merged["rightLowerArm"], over["rightLowerArm"]))
    d_foot = float(np.linalg.norm(M["rightFoot"][0:3, 3] - M_walk_only["rightFoot"][0:3, 3]))
    check("脚等于纯走路", d_foot < 1e-6, f"{d_foot*1000:.3f}mm")

    print("[5] 冲突检测")
    c = conflicts(base, over, ["arms"])
    check("arms 组有真冲突（>=2 根）", len(c) >= 2, str(c))
    c2 = conflicts(base, over, ["legs"])
    check("legs 组无冲突（wave 不改腿）", len(c2) == 0, str(c2))

    print("[6] 分组叠加：只叠左手")
    merged2, covered2 = merge(base, over, ["arm.left"])
    # wave 只驱动 right，所以 left 在 over 里是恒等 —— 合并后左臂会被"复位"
    check("只覆盖左臂 3 根", len(covered2) == 3, str(covered2))

    print("[7] 全部组都能展开")
    ok = 0
    for g in OVERLAY_GROUPS:
        if bones_of([g]):
            ok += 1
    check("7 个组全部可用", ok == len(OVERLAY_GROUPS), f"{ok}/{len(OVERLAY_GROUPS)}")


    print("[8] 三层叠加：走路 + 挥手 + 转头")
    P_turn = dict(fams["idle"]["P_default"])
    P_turn["head_yaw"] = 28
    P_turn["head_tilt"] = 8
    b_turn = pose_gen.build(P_turn, rig, bm, active_side=None)

    merged3, rep = merge_layers(base, [(over, ["arm.right"]), (b_turn, ["head"])])
    check("覆盖两层", len(rep["per_layer"]) == 2, str(len(rep["per_layer"])))
    check("第一层 3 根（右臂）", len(rep["per_layer"][0]) == 3, str(rep["per_layer"][0]))
    check("第二层 3 根（头）", len(rep["per_layer"][1]) == 3, str(rep["per_layer"][1]))
    check("无冲突（臂 vs 头不重叠）", len(rep["conflicts"]) == 0, str(rep["conflicts"]))

    M3 = rig.compute(merged3)
    hr3 = M3["rightHand"][0:3, 3]
    check("右手仍举高", float(hr3[2]) > 0.80, f"z={hr3[2]:.3f}")
    # 头部确实转了
    import math as _m
    fwd = M3["head"][0:3, 2]
    yaw = _m.degrees(_m.atan2(float(fwd[0]), float(-fwd[1])))
    check("头转了（|yaw| > 15°）", abs(yaw) > 15, f"{yaw:.1f}°")

    print("[9] 三层冲突检测（两层都改头）")
    b_turn2 = pose_gen.build(dict(fams["idle"]["P_default"], head_yaw=-20), rig, bm, active_side=None)
    _, rep2 = merge_layers(base, [(b_turn, ["head"]), (b_turn2, ["head"])])
    check("检测到 3 根冲突", len(rep2["conflicts"]) == 3, str(rep2["conflicts"]))
    check("后层赢（conflicts 有序）", rep2["covered"]["head"] == [0, 1], str(rep2["covered"]["head"]))

    print("[10] 空层不影响")
    merged4, rep4 = merge_layers(base, [])
    check("等于 base", all(np.allclose(merged4[k], base[k]) for k in base))
    check("无覆盖", rep4["per_layer"] == [])


    print("[11] 优先级权重：高 weight 赢")
    Pt2 = dict(fams["idle"]["P_default"]); Pt2["head_yaw"] = -35
    low = pose_gen.build(dict(fams["idle"]["P_default"], head_yaw=15), rig, bm, active_side=None)
    high = pose_gen.build(Pt2, rig, bm, active_side=None)
    # 低权重先写、高权重后写
    _, r1 = merge_layers(base, [(low, ["head"], 0.3), (high, ["head"], 0.9)])
    check("高权重赢（第 1 层）", r1["winner"]["head"] == 1, str(r1["winner"]["head"]))
    # 高权重先写、低权重后写 —— 仍然高权重赢
    _, r2 = merge_layers(base, [(high, ["head"], 0.9), (low, ["head"], 0.3)])
    check("顺序不影响（仍是第 0 层）", r2["winner"]["head"] == 0, str(r2["winner"]["head"]))

    print("[12] 同权重：后写的赢")
    _, r3 = merge_layers(base, [(low, ["head"], 1.0), (high, ["head"], 1.0)])
    check("后写的赢（第 1 层）", r3["winner"]["head"] == 1, str(r3["winner"]["head"]))

    print("[13] 默认 weight=1.0")
    _, r4 = merge_layers(base, [(low, ["head"]), (high, ["head"])])
    check("后写的赢", r4["winner"]["head"] == 1, str(r4["winner"]["head"]))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())





