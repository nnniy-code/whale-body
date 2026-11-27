"""tools/test_explain.py — 验证失败原因能转成用户看得懂的话。

怎么测：python tools/test_explain.py
预期现象：每个失败动作都有一句中文说明，带具体数字
"""

import json
import sys

import numpy as np

sys.path.insert(0, r"D:\DSproject\body-v2")

from core.bones import BoneMap
from core.collision import baseline_gaps, load_table
from core.explain import explain_detail, explain_gap, explain_trajectory
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


CASES = {
    "摊手（谓词矛盾）": [
        {"name": "hand_outside_body", "args": {"side": "right", "min_mm": 40}},
        {"name": "elbow_angle", "args": {"side": "right", "min_deg": 20, "max_deg": 90}},
    ],
    "抱膝（前倾不够）": [
        {"name": "torso_lean", "args": {"deg": -28, "tol_deg": 5}},
        {"name": "hand_below_waist", "args": {"side": "right", "margin_mm": -40}},
    ],
    "不存在的谓词": [
        {"name": "fly_to_moon", "args": {}},
    ],
    "正常动作（摸耳朵）": [
        {"name": "hand_near_anchor", "args": {"side": "right", "anchor": "head", "dist_mm": 220}},
    ],
}


def main():
    rt = setup()
    fails = 0
    print("=== plan_trajectory 的 message 字段 ===\n")
    for name, preds in CASES.items():
        tr = rt.plan_trajectory({"intent": "free", "predicates": preds,
                                 "use_llm_predicates": True}, n_frames=20)
        msg = tr.get("message") or ""
        mode = tr.get("mode", "?")
        print(f"[{name}]")
        print(f"  mode    = {mode}")
        print(f"  message = {msg}")
        # 正常动作不该有 message
        if name.startswith("正常"):
            if msg:
                print("  [FAIL] 正常动作不该有失败说明")
                fails += 1
        else:
            if not msg or not any(k in msg for k in ("做不出", "做不到", "会撞", "出了点错")):
                print("  [FAIL] 失败动作应该有用户可读的说明")
                fails += 1
        print()

    print("=== explain_gap 各种 kind ===\n")
    for kind, detail in (
        ("no_family", []),
        ("solve_failed", [{"predicate": "elbow_angle", "err": 1.0,
                           "detail": {"deg": 170.1, "range": [20, 90]}}]),
        ("solve_error", []),
        ("verify_failed", [{"predicate": "no_self_collision", "err": 0.8,
                            "detail": {"pairs": 3}}]),
        ("out_of_range", []),
    ):
        g = {"kind": kind, "wanted": "测试动作", "detail": detail}
        print(f"  {kind:14s} → {explain_gap(g)}")

    print()
    if fails:
        print(f"RESULT: FAIL ({fails})")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


