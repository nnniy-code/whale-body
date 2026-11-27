"""tools/bench_solve.py — 对比「带/不带胶囊复检」的求解耗时。

怎么测：python tools/bench_solve.py
预期现象：delta 约 3–5 ms（胶囊只在最优解上测一次）
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.solver import MAIN21, fast_rig, solve  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    rf = fast_rig(rig)
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    anch = json.load(open(os.path.join(ROOT, "config", "anchors.json"), encoding="utf-8"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    caps = json.load(open(os.path.join(ROOT, "config", "capsules.json"), encoding="utf-8"))["bones"]
    tab = load_table()
    bmj = json.load(open(os.path.join(ROOT, "config", "bones_map.json"), encoding="utf-8"))["bones"]
    lengths = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, lengths, chain_skip=3, only=ONLY)

    preds = [{"name": "hand_above_head", "args": {"side": "right"}}]

    t = time.perf_counter()
    r1 = solve(rf, bm, fams["wave"], preds, anch, budget=3000, seed=2)
    t1 = (time.perf_counter() - t) * 1000

    t = time.perf_counter()
    r2 = solve(rf, bm, fams["wave"], preds, anch, budget=3000, seed=2,
               capsules=caps, collision_table=tab, lengths=lengths,
               capsule_baseline=base, capsule_only=ONLY)
    t2 = (time.perf_counter() - t) * 1000

    print(f"without caps  {t1:7.1f} ms  converged={r1['converged']}  evals={r1['evals']}")
    print(f"with caps     {t2:7.1f} ms  converged={r2['converged']}  caps_viol={len(r2['capsules'] or [])}")
    print(f"delta         {t2 - t1:7.1f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
