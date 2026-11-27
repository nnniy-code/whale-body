"""tools/verify_templates.py — 用每个族的 predicates_template 实际求解一次，报告可行性。

怎么测：python tools/verify_templates.py
产物：docs/template_feasibility.json
用途：模板不是拍脑袋写——每个都要跑一遍，看求解器解不解得出。
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import pose_gen  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.solver import MAIN21, make_frame, solve  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    anch = json.load(open(os.path.join(ROOT, "config", "anchors.json"), encoding="utf-8"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    caps = json.load(open(os.path.join(ROOT, "config", "capsules.json"), encoding="utf-8"))["bones"]
    tab = load_table()
    bmj = json.load(open(os.path.join(ROOT, "config", "bones_map.json"), encoding="utf-8"))["bones"]
    lengths = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    rf = rig.subset(MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"])
    base = baseline_gaps(rf.compute(zero), caps, rf, lengths, chain_skip=3, only=ONLY)

    rows = []
    print(f"{'族':<10} {'谓词':>4} {'conv':>6} {'evals':>6} {'ms':>7}  问题")
    for name, fam in fams.items():
        tpl = fam.get("predicates_template")
        if not tpl:
            print(f"{name:<10} {'—':>4} {'skip':>6}")
            continue
        side = "right" if any((p.get("args") or {}).get("side") == "right" for p in tpl) else None
        t0 = time.perf_counter()
        res = solve(rig, bm, fam, tpl, anch, budget=2000, seed=11,
                    active_side=side, capsules=caps, collision_table=tab,
                    lengths=lengths, capsule_baseline=base, capsule_only=ONLY)
        ms = (time.perf_counter() - t0) * 1000
        problems = []
        for v in res["violations"][:2]:
            problems.append(f"{v['predicate']}={v['err']:.2f}")
        for c in (res["capsules"] or [])[:1]:
            problems.append(f"capsule {c['bone']}x{c['other']}+{c['over_mm']:.0f}mm")
        print(f"{name:<10} {len(tpl):>4} {str(res['converged']):>6} {res['evals']:>6} "
              f"{ms:>7.0f}  {'; '.join(problems)[:60]}")
        rows.append({"family": name, "predicates": [p["name"] for p in tpl],
                     "converged": res["converged"], "evals": res["evals"],
                     "ms": round(ms, 1), "problems": problems})

    ok = sum(1 for r in rows if r["converged"])
    print(f"\n可行 {ok}/{len(rows)}")
    out = os.path.join(ROOT, "docs", "template_feasibility.json")
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("WROTE", out)
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
