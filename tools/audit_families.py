"""tools/audit_families.py — 18 个族到底在干什么（有没有名不副实）。

对每个族：应用它的 P_default + G，算 FK，跟 idle 基线比关键关节的位移。

判据（按族名该动的地方）：
  挥手     右手该高过头
  指       右手该往前伸
  伸懒腰   双手该抬高
  坐/跪/趴 髋该下降
  走/跑    脚该动、髋该降
  转身     朝向该变
  抖尾/歪头 尾巴/头该动

用法：python tools/audit_families.py
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]
KEYS = ["hips", "head", "leftHand", "rightHand", "leftFoot", "rightFoot",
        "WhaleTail_04", "leftLowerLeg", "rightLowerLeg"]

# 族名 → (该动的骨, 期望方向说明)
EXPECT = {
    "wave": ("rightHand", "高过头"),
    "point": ("rightHand", "往前伸"),
    "stretch": ("rightHand", "抬高"),
    "pat_head": ("rightHand", "抬高"),
    "sit": ("hips", "下降"),
    "kneel": ("hips", "下降"),
    "lie_down": ("hips", "下降"),
    "walk": ("leftFoot", "移动"),
    "run": ("leftFoot", "移动"),
    "spin": ("hips", "转向"),
    "tail_wag": ("WhaleTail_04", "动"),
    "tail_drop": ("WhaleTail_04", "动"),
    "head_tilt_fam": ("head", "动"),
    "lean_in": ("head", "前倾"),
    "rest": ("hips", "变化"),
    "talk": ("head", "动"),
    "idle": ("hips", "呼吸"),
    "free": ("rightHand", "动"),
    "lift_leg_left": ("leftFoot", "离地"),
    "lift_leg_right": ("rightFoot", "离地"),
    "arms_out": ("leftHand", "抬到肩高"),
}


def load():
    rigd = json.load(open(os.path.join(ROOT, "tests/fixtures/rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    rf = rig.subset(ONLY)
    bm = BoneMap.load(os.path.join(ROOT, "config/bones_map.json"))
    anch = json.load(open(os.path.join(ROOT, "config/anchors.json"), encoding="utf-8"))
    fams = json.load(open(os.path.join(ROOT, "config/seed_families.json"), encoding="utf-8"))["families"]
    caps = json.load(open(os.path.join(ROOT, "config/capsules.json"), encoding="utf-8"))["bones"]
    tab = load_table()
    bmj = json.load(open(os.path.join(ROOT, "config/bones_map.json"), encoding="utf-8"))["bones"]
    L = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, L, chain_skip=3, only=ONLY)
    return dict(rig=rig, bm=bm, anch=anch, fams=fams, caps=caps, tab=tab, L=L, base=base)


def mk(E):
    return Runtime(E["rig"], E["bm"], E["anch"], E["fams"], Library(),
                   capsules=E["caps"], collision_table=E["tab"], lengths=E["L"],
                   capsule_baseline=E["base"], capsule_only=ONLY)


def pose_of(E, rt, fam, t=0.3):
    P = dict(E["fams"][fam].get("P_default") or {})
    b, d = rt.build_full(P, fam, t)
    return E["rig"].compute(b), d


def rot_deg(A, B) -> float:
    """两个 4x4 的旋转差（度）。歪头这种「转但不移动」的动作只能这么测。"""
    Ra = np.asarray(A)[0:3, 0:3]
    Rb = np.asarray(B)[0:3, 0:3]
    R = Ra.T @ Rb
    tr = float(R[0, 0] + R[1, 1] + R[2, 2])
    return float(np.degrees(np.arccos(max(-1.0, min(1.0, (tr - 1.0) * 0.5)))))


def main() -> int:
    E = load()
    rows = []

    rt0 = mk(E)
    M0, _ = pose_of(E, rt0, "idle", 0.0)

    for fam in sorted(E["fams"]):
        rt = mk(E)
        G = E["fams"][fam].get("G") or {}
        tr = float(G.get("turn_rate", 0.0))
        yaw0 = float(rt.body.yaw)
        if abs(tr) > 1e-9:
            rt.set_motion(family=fam)
            for _ in range(12):
                rt.tick(1 / 30)
        M, diag = pose_of(E, rt, fam, 0.3)
        dyaw = abs(float(rt.body.yaw) - yaw0)
        pos, rot = {}, {}
        for k in KEYS:
            if k in M and k in M0:
                pos[k] = round(float(np.linalg.norm(M[k][0:3, 3] - M0[k][0:3, 3])) * 1000, 1)
                rot[k] = round(rot_deg(M0[k], M[k]), 1)
        # 「动了」= 位置 >2mm 或 旋转 >2°
        unit = max([max(pos.get(k, 0), rot.get(k, 0) * 5) for k in KEYS] or [0])
        exp_bone, exp_txt = EXPECT.get(fam, ("?", ""))
        exp_moved = max(pos.get(exp_bone, 0), rot.get(exp_bone, 0) * 5)
        rows.append({"fam": fam, "max": round(unit, 1), "dyaw": round(dyaw, 1),
                     "pos": pos, "rot": rot, "expect": exp_bone,
                     "expect_score": round(exp_moved, 1),
                     "expect_pos": pos.get(exp_bone, 0.0),
                     "expect_rot": rot.get(exp_bone, 0.0),
                     "hip": round(float(M["hips"][2, 3]), 3),
                     "ik": max(diag["err_mm"].values()) if diag.get("err_mm") else None})

    print(f"{'族':14s} {'位移mm':>7s} {'转角°':>6s} {'该动的骨':>10s} {'Δmm':>7s} {'Δ°':>6s} {'髋高':>6s} {'转向':>5s} 判定")
    print("-" * 88)
    dead, weak = [], []
    for r in rows:
        ok = r["max"] >= 2.0
        if not ok:
            dead.append(r["fam"])
        elif r["expect_score"] < 2.0 and r["dyaw"] < 5.0:
            weak.append(r["fam"])
        # 该动的骨取「最大变化的那一项」显示
        best = "位移" if r["expect_pos"] >= r["expect_rot"] * 5 else "转角"
        val = r["expect_pos"] if best == "位移" else r["expect_rot"]
        print(f"{r['fam']:14s} {r['max']:7.1f} {'-':>6s} {r['expect']:>10s} "
              f"{r['expect_pos']:7.1f} {r['expect_rot']:6.1f} {r['hip']:6.3f} {r['dyaw']:5.1f} "
              f"{'✅' if ok else '❌ 几乎不动'}")

    print("-" * 88)
    print("几乎不动的族:", dead or "无")
    print("整体动了但「该动的骨」没动的族:", weak or "无")
    print("注：LEG 类动作（抬腿/踢腿）在模型里**没有对应单元** —— "
          "P 只覆盖上半身，腿只能整体走/站。")
    with open(os.path.join(ROOT, "docs", "family_audit.json"), "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
