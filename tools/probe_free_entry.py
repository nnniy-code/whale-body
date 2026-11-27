"""tools/probe_free_entry.py — 为什么 free + 谓词 返回 no_entry。"""
import json
import sys

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
    return Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                   collision_table=tab, lengths=L, capsule_baseline=base,
                   capsule_only=ONLY)


def main():
    rt = setup()
    preds = [{"name": "hand_near_anchor",
              "args": {"side": "right", "anchor": "head", "dist_mm": 120}}]
    act = {"intent": "free", "predicates": preds, "use_llm_predicates": True}
    print("act:", json.dumps(act, ensure_ascii=False))
    r = rt.handle_intent(act)
    print("source :", r.get("source"))
    print("entry  :", bool(r.get("entry")))
    print("gap kind:", (r.get("capability_gap") or {}).get("kind"))
    print("gap     :", json.dumps(
        (r.get("capability_gap") or {}).get("detail"), ensure_ascii=False, default=str)[:400])
    print("llm_ignored:", act.get("_llm_predicates_ignored"))
    fam = rt.families.get("free") or {}
    print("free 族的模板:", [p["name"] for p in (fam.get("predicates_template") or [])])
    print("free 族有没有 P_default:", bool(fam.get("P_default")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
