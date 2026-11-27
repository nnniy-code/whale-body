"""tools/test_template_runtime.py — 验证 runtime 用族的模板就能解（LLM 不用写谓词）。

怎么测：python tools/test_template_runtime.py
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

    print(f"{'族':<10} {'source':<10} {'entry':<10} {'P 关键值'}")
    ok = 0
    for name in fams:
        if not fams[name].get("predicates_template"):
            continue
        r = rt.handle_intent({"intent": name})          # ← 只给族名，不给谓词
        eid = r["entry"].id if r["entry"] else "—"
        src = r["source"]
        extra = ""
        if r["entry"]:
            P = r["entry"].params["P"]
            extra = f"lift={P['arm_lift']:.0f} elbow={P['elbow_bend']:.0f}"
            ok += 1
        print(f"{name:<10} {src:<10} {eid:<10} {extra}")
    lib_path = os.path.join(ROOT, "config", "library.json")
    rt.library.save(lib_path)
    print(f"\n库已写入 {lib_path}（{len(rt.library.entries)} 条）")
    total = sum(1 for f in fams.values() if f.get("predicates_template"))
    print(f"\n只用族名解出 {ok}/{total}")
    return 0 if ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

