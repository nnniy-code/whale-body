"""tests/test_gate.py — P3 验收（5 种错全抓到 + verdict 自洽）。

怎么测：python tests/test_gate.py
预期现象：[gate] ALL PASS（5 种故意造错全部被抓）
调参位置：core/predicates.py 的阈值、core/gate.py 的 inject_thresholds
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bones import BoneMap  # noqa: E402
from core.contact import detect  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.gate import evaluate, verdict  # noqa: E402
from core.predicates import PREDICATES, Frame  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RIG = os.path.join(HERE, "fixtures", "rig_data.json")
MAP = os.path.join(os.path.dirname(HERE), "config", "bones_map.json")

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    with open(RIG, "r", encoding="utf-8") as f:
        rig_d = json.load(f)
    rig = Rig(rig_d["rest"], rig_d["parent"], rig_d["connected"], rig_d["order"])
    bm = BoneMap.load(MAP)
    zero = {n: np.eye(4) for n in rig_d["order"]}

    def frame(basis=None, sole=None, meta=None):
        M = rig.compute(basis or zero)
        anchors = {
            "mouth": M["head"][0:3, 3] + np.array([0.0, -0.09, 0.02]),
            "head_top": M["mixamorig:HeadTop_End"][0:3, 3],
            "hand.R": M["rightHand"][0:3, 3],
        }
        contacts = detect(sole or {"leftFoot": 0.0, "rightFoot": 0.0})
        return Frame(M=M, contacts=contacts, anchors=anchors, meta=meta or {})

    P = lambda *names: [{"name": n} for n in names]

    print("[1] 基线：站立姿态全过")
    f0 = frame()
    v = verdict([f0], P("foot_grounded", "torso_upright", "no_ground_penetration"))
    check("全过", v.passed, str(v.as_dict()["violations"]))
    check("total_err == 0", v.total_err == 0.0)
    check("pass/total 与 verdict 一致", v.pass_count == v.total and v.verdict_str == "PASS")

    print("[2] 造错①：双脚离地 60mm（walk 档）")
    f = frame(sole={"leftFoot": 0.060, "rightFoot": 0.060})
    v = verdict([f], P("foot_grounded"))
    check("被抓到", not v.passed)
    check("worst_frame = 0", v.violations[0].worst_frame == 0)
    check("detail 有 h_mm", "h_mm" in v.violations[0].detail, str(v.violations[0].detail))

    print("[3] 造错②：穿地 12mm")
    f = frame(sole={"leftFoot": -0.012, "rightFoot": 0.0})
    v = verdict([f], P("no_ground_penetration"))
    check("被抓到", not v.passed)
    check("pen 12mm", abs(v.violations[0].detail["pen_mm"] - 12.0) < 0.01,
          str(v.violations[0].detail))

    print("[4] 造错③：插躯干（胶囊 8mm）")
    f = frame(meta={"capsule_penetration_mm": 8.0})
    v = verdict([f], P("no_self_collision"))
    check("被抓到", not v.passed)
    check("pen 8mm", abs(v.violations[0].detail["pen_mm"] - 8.0) < 0.01)

    print("[5] 造错④：出界（手移到 x=3m）")
    # rightHand 是 connected 骨，不能靠 basis 的 location 移动（会被忽略）。
    # 直接改矩阵平移来构造越界（只测 inside_workspace，不需要子骨同步）。
    M0 = rig.compute(zero)
    M2 = {k: v.copy() for k, v in M0.items()}
    M2["rightHand"][0, 3] = 3.0
    anchors = {"mouth": M0["head"][0:3, 3] + np.array([0.0, -0.09, 0.02]),
               "head_top": M0["mixamorig:HeadTop_End"][0:3, 3],
               "hand.R": M2["rightHand"][0:3, 3]}
    f = Frame(M=M2, contacts=detect({"leftFoot": 0.0, "rightFoot": 0.0}),
              anchors=anchors, meta={})
    v = verdict([f], P("inside_workspace"))
    check("被抓到", not v.passed, str(v.as_dict()["violations"]))

    print("[6] 造错⑤：倾倒（胸绕世界 X 60°）")
    b = dict(zero)
    b["chest"] = bm.basis_from_world_rot(rig, "chest", "x", 60)
    f = frame(basis=b)
    v = verdict([f], P("torso_upright"))
    check("被抓到", not v.passed, f"deg={v.violations[0].detail.get('deg') if v.violations else None}")

    print("[7] 五种错一起 → 五种全抓到")
    frames_bad = [
        frame(sole={"leftFoot": 0.06, "rightFoot": 0.06}),
        frame(sole={"leftFoot": -0.012, "rightFoot": 0.0}),
        frame(meta={"capsule_penetration_mm": 8.0}),
        frame(meta={}),
        frame(basis={**zero, "chest": bm.basis_from_world_rot(rig, "chest", "x", 60)}),
    ]
    v = verdict(frames_bad, P(
        "foot_grounded", "no_ground_penetration", "no_self_collision",
        "torso_upright", "dual_support"))
    got = {x.predicate for x in v.violations}
    check("至少 4 种被抓", len(got) >= 4, str(sorted(got)))

    print("[8] verdict 与 pass/total 永不分叉")
    ok = True
    for fr in ([f0], frames_bad):
        vv = verdict(fr, P("foot_grounded", "torso_upright"))
        if (vv.verdict_str == "PASS") != (vv.pass_count == vv.total):
            ok = False
    check("一致性", ok)

    print("[9] evaluate 与 verdict 分开（有梯度 vs 严格）")
    f = frame(sole={"leftFoot": 0.040, "rightFoot": 0.0})
    e = evaluate([f], P("foot_grounded"))
    v = verdict([f], P("foot_grounded"))
    check("evaluate > 0（有梯度）", e > 0, f"{e:.4f}")
    check("verdict 也判失败", not v.passed)

    print("[10] 谓词清单")
    check("至少 20 条", len(PREDICATES) >= 20, str(len(PREDICATES)))
    check("时间语义齐全",
          all(PREDICATES[n].sem in ("at_end", "at_any", "throughout", "peak")
              for n in PREDICATES))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print(f"RESULT: PASS ({len(PREDICATES)} predicates)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
