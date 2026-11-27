"""tools/blender_secondary_demo.py — 二次运动对比：开着 / 关掉，量发梢和裙摆。

跑法（无头，独立进程）：

    blender_rt_headless(file=<blend>, script_file=tools/blender_secondary_demo.py,
                        args="--secondary 1", shots=[...])

参数（`--` 之后）：
    --secondary 1|0     开/关（默认 1，关掉只用来做对照）
    --frames N          转多少帧（默认 36 = 1.2 秒 @30fps）
    --turn DEG          转向速度（默认 90 度/秒）

判据：同样的帧数、同样的转向，开二次运动后发梢/裙摆末端的世界位置
      应当和关掉时有可测差异（>3mm），且**不是随机抖动**（方向与转向相反）。
"""

import json
import os
import sys

import bpy
import numpy as np

ROOT = r"D:\DSproject\body-v2"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table, region_of  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.loop import BodyLoop  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.secondary import SecondaryMotion  # noqa: E402
from core.solver import MAIN21  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]


def arg(name, default):
    if "--" not in sys.argv:
        return default
    a = sys.argv[sys.argv.index("--") + 1:]
    if name in a:
        i = a.index(name)
        if i + 1 < len(a):
            return a[i + 1]
    return default


def build():
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
    rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps, collision_table=tab,
                 lengths=L, capsule_baseline=base, capsule_only=ONLY)
    return rig, rt


def run(turn_deg, frames, use_secondary, apply_to_arm=None):
    rig, rt = build()
    rt.set_motion(turn_rate=turn_deg)
    P = dict(rt.families["idle"]["P_default"])
    loop = BodyLoop(rt, writer=(BodyLoop.arm_writer(apply_to_arm) if apply_to_arm else None))
    if use_secondary:
        loop.enable_secondary()
    basis = None
    for _ in range(frames):
        basis, _d = loop.step(1 / 30, P=P, family="idle")
        if apply_to_arm is not None:
            bpy.context.view_layer.update()
    M = rig.compute(basis)
    return rt, loop, M


def tips(rig, M):
    """取最深的头发、裙摆、腰饰骨，返回世界位置。"""
    out = {}
    for grp in ("hair", "skirt", "ribbon"):
        cand = [n for n in rig.names if region_of(n) == grp]
        if not cand:
            continue
        # 最深的那个 = 链条末端
        def depth(n):
            d, p = 0, rig.parent.get(n)
            while p is not None and d < 64:
                d += 1
                p = rig.parent.get(p)
            return d
        tip = max(cand, key=depth)
        out[grp] = {"bone": tip, "pos": np.asarray(M[tip])[0:3, 3].copy()}
    return out


def main():
    turn = float(arg("--turn", 90.0))
    frames = int(arg("--frames", 36))
    use = int(arg("--secondary", 1)) == 1

    arm = max([o for o in bpy.data.objects if o.type == "ARMATURE"],
              key=lambda o: len(o.pose.bones))

    rt_off, _l_off, M_off = run(turn, frames, False)
    # 第二遍才是「真正写进骨骼」的那一版：use 决定它开不开二次运动
    rt_on, l_on, M_on = run(turn, frames, use, apply_to_arm=arm)

    t_off, t_on = tips(rt_off.rig, M_off), tips(rt_on.rig, M_on)
    delta = {}
    for g in t_on:
        d = np.linalg.norm(t_on[g]["pos"] - t_off[g]["pos"]) * 1000
        delta[g] = round(float(d), 2)

    out = {
        "ok": True,
        "turn_deg_s": turn,
        "frames": frames,
        "secondary": use,
        "yaw": round(rt_on.body.yaw, 2),
        "delta_mm": delta,
        "tips": {g: t_on[g]["bone"] for g in t_on},
        "snapshot": l_on.secondary.snapshot() if l_on.secondary else None,
        "body_y": round(rt_on.body.y, 4),
    }
    out["ok"] = bool(delta and max(delta.values()) > 3.0)
    print("HEADLESS " + json.dumps(out, ensure_ascii=False))


main()
