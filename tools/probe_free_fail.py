"""tools/probe_free_fail.py — 单独跑失败的两个动作，看完整错误。"""
import json
import sys
import traceback

import numpy as np

sys.path.insert(0, r"D:\DSproject\body-v2")

from core.bones import BoneMap
from core.collision import baseline_gaps, load_table
from core.fk import Rig
from core.library import Library
from core.runtime import Runtime
from core.solver import MAIN21

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]


def setup():
    R = r"D:\DSproject\body-v2"
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
                 collision_table=tab, lengths=L, capsule_baseline=base,
                 capsule_only=ONLY)
    return rt


def main():
    rt = setup()
    cases = {
        "摊手": [
            {"name": "hand_below_shoulder", "args": {"side": "right", "margin_mm": 30}},
            {"name": "hand_below_shoulder", "args": {"side": "left", "margin_mm": 30}},
            {"name": "elbow_angle", "args": {"side": "right", "min_deg": 20, "max_deg": 60}},
            {"name": "elbow_angle", "args": {"side": "left", "min_deg": 20, "max_deg": 60}},
        ],
        "抱膝": [
            {"name": "torso_lean", "args": {"deg": -28, "tol_deg": 12}},
            {"name": "hand_below_waist", "args": {"side": "right", "margin_mm": -40}},
            {"name": "elbow_angle", "args": {"side": "right", "min_deg": 100, "max_deg": 150}},
        ],
    }
    for name, preds in cases.items():
        print(f"\n=== {name} ===")
        try:
            r = rt.handle_intent({"intent": "free", "predicates": preds,
                                  "use_llm_predicates": True})
            print("  source:", r.get("source"))
            print("  entry :", bool(r.get("entry")))
            if r.get("capability_gap"):
                print("  gap   :", json.dumps(r["capability_gap"].get("detail"),
                                              ensure_ascii=False, default=str)[:500])
            if r.get("detail"):
                print("  detail:", str(r["detail"])[:500])
        except Exception:
            traceback.print_exc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
