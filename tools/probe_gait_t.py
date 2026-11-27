"""tools/probe_gait_t.py — 逐 t 扫描 GaitTarget 的误差，找哪个相位出错。"""
import json
import sys

import numpy as np

sys.path.insert(0, r"D:\DSproject\body-v2")

from core.assembly import build_full_body
from core.body_state import BodyState
from core.bones import BoneMap
from core.fk import Rig
from core.foot_target import GaitTarget


def main():
    R = r"D:\DSproject\body-v2"
    d = json.load(open(R + r"\tests\fixtures\rig_data.json", encoding="utf-8"))
    rig = Rig(d["rest"], d["parent"], d["connected"], d["order"])
    bm = BoneMap.load(R + r"\config\bones_map.json")
    fams = json.load(open(R + r"\config\seed_families.json", encoding="utf-8"))["families"]
    M0 = rig.compute({n: np.eye(4) for n in d["order"]})
    P = dict(fams["walk"]["P_default"])
    G = dict(fams["walk"]["G"])
    G.update({"period": 1.0, "stride": 0.5, "foot_lift": 0.06})

    print(f"{'t':>6} {'err_L':>8} {'err_R':>8} {'tgt_L_y':>9} {'act_L_y':>9} {'ik_fail'}")
    for i in range(9):
        t = i / 8
        start = {s: [float(M0[f"{s}Foot"][0, 3]), float(M0[f"{s}Foot"][1, 3]), 0.0]
                 for s in ("left", "right")}
        gt = GaitTarget(G, start)
        st = BodyState()
        b, diag = build_full_body(P, gt, st, t, rig, bm)
        tl = diag["foot_targets"].get("left", [0, 0, 0])
        al = diag["foot_actual"].get("left", [0, 0, 0])
        fail = diag.get("ik_fail") or {}
        note = ""
        for k, v in fail.items():
            note += f"{k}:{v} "
        print(f"{t:6.3f} {diag['err_mm'].get('left', -1):8.1f} "
              f"{diag['err_mm'].get('right', -1):8.1f} "
              f"{tl[1]:9.3f} {al[1]:9.3f}  {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

