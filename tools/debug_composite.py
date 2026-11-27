"""tools/debug_composite.py — 排查 handle_composite 的叠加结果异常。"""
import json
import sys
import time

import numpy as np

sys.path.insert(0, r"D:\DSproject\body-v2")

from core.bones import BoneMap
from core.collision import baseline_gaps, load_table
from core.fk import Rig
from core.library import Library
from core.runtime import Runtime
from core.solver import MAIN21

R = r"D:\DSproject\body-v2"
ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]


def main():
    d = json.load(open(R + r"\tests\fixtures\rig_data.json", encoding="utf-8"))
    rig = Rig(d["rest"], d["parent"], d["connected"], d["order"])
    rf = rig.subset(ONLY)
    bm = BoneMap.load(R + r"\config\bones_map.json")
    anch = json.load(open(R + r"\config\anchors.json", encoding="utf-8"))
    fams = json.load(open(R + r"\config\seed_families.json", encoding="utf-8"))["families"]
    caps = json.load(open(R + r"\config\capsules.json", encoding="utf-8"))["bones"]
    tab = load_table()
    bmj = json.load(open(R + r"\config\bones_map.json", encoding="utf-8"))["bones"]
    L = {n: x["len_mm"] / 1000.0 for n, x in bmj.items()}
    z = {n: np.eye(4) for n in d["order"]}
    base = baseline_gaps(rf.compute(z), caps, rf, L, chain_skip=3, only=ONLY)
    rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                 collision_table=tab, lengths=L, capsule_baseline=base, capsule_only=ONLY)

    # 第一次：walk 单独（复现测试顺序）
    rt.handle_composite([{"intent": "walk"}], now=time.perf_counter())

    # 第二次：叠加
    r = rt.handle_composite(
        [{"intent": "walk"}, {"intent": "wave", "groups": ["arm.right"], "weight": 1.0}],
        now=time.perf_counter())
    print("entries:", [(e.family if e else None) for e in r["entries"]])
    print("sources:", r["sources"])
    print("merged hand_z:", round(float(r["basis"]["rightHand"][2, 3]), 3))

    for i, e in enumerate(r["entries"]):
        if not e:
            continue
        s = (e.metrics or {}).get("side")
        M = rt.pose_for_from_P(e.params["P"], e.family, s)
        hz = float(M["rightHand"][2, 3])
        print(f"  entry{i} {e.family:8s} side={s} hand_z={hz:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
