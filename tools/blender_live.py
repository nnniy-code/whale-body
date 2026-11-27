"""tools/blender_live.py — 在 Blender 里跑 body-v2 的帧循环（只读场景，绝不保存）。

怎么跑（无头，独立进程，不碰 GUI 场景）：

    blender -b <blend> --python tools/blender_live.py

或者通过 DSH：`blender_rt_headless(file=<blend>, script_file=本文件)`

做什么：
  1. 找 armature，核对 209 骨是否和 rig_data.json 对得上
  2. 建 Runtime + BodyLoop，走 48 帧（1.6 秒）
  3. 每 12 帧校验一次「写进骨头的世界矩阵」== 「纯 Python FK 算出来的」
     —— 这是 P5 最重要的一条自检：写骨和 FK 必须是同一套数学
  4. 打印 HEADLESS {json}

判据：
  - 每骨位置误差 < 0.01mm（`binder.verify_roundtrip` 的历史值是 3e-7 m）
  - 脚 IK 误差 < 1mm
  - 场景不被保存（本脚本没有 wm.save，也不改文件）
"""

import json
import os
import sys

import bpy
import numpy as np

ROOT = r"D:\DSproject\body-v2"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import binder  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.loop import BodyLoop  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]
FRAMES = 48
DT = 1.0 / 30.0


def find_arm():
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not arms:
        raise RuntimeError("场景里没有 armature")
    return max(arms, key=lambda o: len(o.pose.bones))


def arg(name, default):
    if "--" not in sys.argv:
        return default
    a = sys.argv[sys.argv.index("--") + 1:]
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default


def main():
    out = {"blend": bpy.data.filepath, "ok": False}
    arm = find_arm()
    out["armature"] = arm.name
    out["bones"] = len(arm.pose.bones)
    mw = arm.matrix_world
    out["armature_is_identity"] = bool(
        np.allclose(np.array(mw), np.eye(4), atol=1e-6))

    rigd = json.load(open(os.path.join(ROOT, "tests/fixtures/rig_data.json"),
                          encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    rf = rig.subset(ONLY)
    missing = [n for n in rig.names if n not in arm.pose.bones]
    out["missing_bones"] = missing[:10]
    out["rig_bones"] = len(rig.names)

    bm = BoneMap.load(os.path.join(ROOT, "config/bones_map.json"))
    anch = json.load(open(os.path.join(ROOT, "config/anchors.json"), encoding="utf-8"))
    fams = json.load(open(os.path.join(ROOT, "config/seed_families.json"),
                          encoding="utf-8"))["families"]
    caps = json.load(open(os.path.join(ROOT, "config/capsules.json"),
                          encoding="utf-8"))["bones"]
    tab = load_table()
    bmj = json.load(open(os.path.join(ROOT, "config/bones_map.json"),
                         encoding="utf-8"))["bones"]
    lengths = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, lengths,
                         chain_skip=3, only=ONLY)

    rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                 collision_table=tab, lengths=lengths,
                 capsule_baseline=base, capsule_only=ONLY)
    rt.set_motion(family="walk")
    _turn = float(arg("--turn", "0") or 0)
    if _turn:
        rt.body.turn_rate = _turn
    P = dict(fams["walk"]["P_default"])

    loop = BodyLoop(rt)
    loop.attach(arm)
    # 全栈：L0 底噪 + 二次运动（可用 -- --idle 0 / --secondary 0 关掉）
    if arg("--idle", "1") == "1":
        loop.enable_idle()
    if arg("--secondary", "1") == "1":
        loop.enable_secondary()

    worst_rt = 0.0
    worst_ik = 0.0
    samples = []
    compose = arg("--compose", "")
    for i in range(FRAMES):
        if compose:
            acts = [{"intent": "walk"},
                    {"intent": compose, "groups": ["arm.right"]}]
            basis, diag = loop.step_actions(acts, DT)
        else:
            basis, diag = loop.step(DT, P=P, family="walk")
        bpy.context.view_layer.update()
        if diag.get("err_mm"):
            worst_ik = max(worst_ik, max(diag["err_mm"].values()))
        if i % 12 == 0:
            M = rig.compute(basis)
            worst = 0.0
            for n in rig.names:
                pb = arm.pose.bones.get(n)
                if pb is None:
                    continue
                t = pb.matrix.translation
                d = np.array([t.x, t.y, t.z]) - np.asarray(M[n])[0:3, 3]
                worst = max(worst, float(np.linalg.norm(d)) * 1000.0)
            worst_rt = max(worst_rt, worst)
            samples.append({
                "frame": i,
                "roundtrip_mm": round(worst, 6),
                "hand_z": round(float(M["rightHand"][2, 3]), 4),
                "foot_z": round(float(M["rightFoot"][2, 3]), 4),
            })

    # 场景状态（确认没被保存）
    out["file_dirty"] = bool(bpy.data.is_dirty)
    out["roundtrip_mm"] = round(worst_rt, 6)
    out["ik_err_mm"] = round(worst_ik, 2)
    out["frames"] = loop.frames
    out["t"] = round(loop.t, 3)
    out["written"] = loop.written
    out["samples"] = samples
    out["body"] = rt.body.as_dict()
    out["idle"] = loop.idle.snapshot() if loop.idle else None
    out["secondary"] = loop.secondary.snapshot() if loop.secondary else None
    out["compose"] = compose or None
    out["ok"] = bool(worst_rt < 0.01 and worst_ik < 1.0 and not missing)
    print("HEADLESS " + json.dumps(out, ensure_ascii=False))


main()
