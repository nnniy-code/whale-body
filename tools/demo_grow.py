"""tools/demo_grow.py — 演示「族自动生长」：free 族做新动作 → 反复出现 → 长成新族。

怎么测：python tools/demo_grow.py
预期现象：
  1. free 族能解出「新的」动作（解析器没见过的）
  2. 同样请求做 3 次 → propose() 提议一个新族
  3. apply_proposals() 把它写进族表
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
from core.grow import apply_proposals, make_name, propose  # noqa: E402
from core.library import Library, intent_key  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]

# 「靠近一点」这种动作，没有专门族 —— 用 free + 两条谓词
NEAR_PREDS = [
    # 容差要小，否则 lean=0 也算合格（|0-8|=8 ≤ tol），求解器就不动了
    # 负 lean = 前倾（和 stretch 模板的 deg=-5 一致）
    {"name": "torso_lean", "args": {"deg": -8, "tol_deg": 2}},
    {"name": "head_nod", "args": {"deg": 6, "tol_deg": 2}},
]


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    rf = rig.subset(ONLY)
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    anch = json.load(open(os.path.join(ROOT, "config", "anchors.json"), encoding="utf-8"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    caps = json.load(open(os.path.join(ROOT, "config", "capsules.json"), encoding="utf-8"))["bones"]
    tab = load_table()
    bmj = json.load(open(os.path.join(ROOT, "config", "bones_map.json"), encoding="utf-8"))["bones"]
    lengths = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, lengths, chain_skip=3, only=ONLY)

    rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                 collision_table=tab, lengths=lengths,
                 capsule_baseline=base, capsule_only=ONLY)

    print("[1] free 族：做新动作（没有专门族）")
    for i in range(3):
        r = rt.handle_intent({"intent": "free", "use_llm_predicates": True,
                              "predicates": [dict(p) for p in NEAR_PREDS]})
        if r["entry"]:
            P = r["entry"].params["P"]
            print(f"    第 {i+1} 次：✅ {r['entry'].id}  lean={P.get('lean', 0):.1f}  "
                  f"head_nod={P.get('head_nod', 0):.1f}")
        else:
            gap = r.get("capability_gap") or {}
            print(f"    第 {i+1} 次：❌ {gap.get('kind')} {str(gap.get('suggested'))[:60]}")

    print(f"\n    库：{len(rt.library.entries)} 条，"
          f"free 族 {sum(1 for e in rt.library.entries.values() if e.family == 'free')} 条")

    print("\n[2] propose：从库里找反复出现的谓词组合")
    props = propose(rt.library, min_count=3, only_family="free")
    if not props:
        print("    （没有达到 min_count=3 的组合）")
    for p in props:
        print(f"    → 提议族 {p['name']!r}  出现 {p['count']} 次  "
              f"P_range {len(p['P_range'])} 个维度")

    print("\n[3] apply_proposals：写进族表")
    new_fams, added = apply_proposals(fams, props, min_count=3)
    print(f"    族数 {len(fams)} → {len(new_fams)}")
    for a in added:
        print(f"    + {a['name']}（由 {a['from']} 沉淀，{a['count']} 条证据）")
        f = new_fams[a["name"]]
        print(f"      模板：{[x['name'] for x in f['predicates_template']]}")
        print(f"      P_range：{{{', '.join(f'{k}: {v}' for k, v in list(f['P_range'].items())[:3])}}}")

    print("\n[4] 新族能不能直接用")
    if added:
        rt2 = Runtime(rig, bm, anch, new_fams, Library(), capsules=caps,
                      collision_table=tab, lengths=lengths,
                      capsule_baseline=base, capsule_only=ONLY)
        name = added[0]["name"]
        r = rt2.handle_intent({"intent": name})
        if r["entry"]:
            print(f"    {name} → ✅ {r['source']} {r['entry'].id}")
        else:
            gap = r.get("capability_gap") or {}
            print(f"    {name} → ❌ {gap.get('kind')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


