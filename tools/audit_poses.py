"""tools/audit_poses.py — 全身审计：18 个族的姿势逐个查。

怎么测：python tools/audit_poses.py
查什么：
  1. 求解能不能收敛
  2. 胶囊穿模
  3. 反关节（肘/膝）
  4. 脚接地 / 穿地
  5. 重心是否在双脚附近
  6. 左右对称性（镜像差）
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import pose_gen  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, check_capsules, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.predicates import evaluate_one  # noqa: E402
from core.solver import MAIN21, make_frame, solve  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]
ROWS: list[dict] = []


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

    hdr = (f"{'族':<12} {'收敛':>4} {'胶囊':>4} {'肘反':>4} {'膝反':>4} "
           f"{'脚穿地':>6} {'重心mm':>7} {'对称mm':>7}")
    print(hdr)
    print("-" * len(hdr))

    for name, fam in fams.items():
        tpl = fam.get("predicates_template") or []
        side = "right" if any((p.get("args") or {}).get("side") == "right" for p in tpl) else None
        res = solve(rig, bm, fam, tpl, anch, budget=1200, seed=11,
                    active_side=side, capsules=caps, collision_table=tab,
                    lengths=lengths, capsule_baseline=base, capsule_only=ONLY)
        row = {"family": name, "converged": res["converged"]}
        if not res["converged"]:
            row["issue"] = "求解失败"
            ROWS.append(row)
            print(f"{name:<12} {'✗':>4}  —")
            continue

        P = dict(fam["P_default"])
        P.update(res["params"]["P"])
        M = rf.compute(pose_gen.build(P, rf, bm, active_side=side))
        f = make_frame(M, anch)

        # 胶囊
        cv = check_capsules(M, caps, rf, tab, baseline=base, lengths=lengths,
                            chain_skip=3, only=ONLY)
        # 反关节
        el_r, _ = evaluate_one("no_hyperextension", f, side="right", min_deg=20)
        el_l, _ = evaluate_one("no_hyperextension", f, side="left", min_deg=20)
        kn_r, _ = evaluate_one("knee_angle", f, side="right", min_deg=0, max_deg=160)
        kn_l, _ = evaluate_one("knee_angle", f, side="left", min_deg=0, max_deg=160)
        # 脚穿地（踝低于 0.05 算贴地，低于 0 算穿）
        lz = float(M["leftFoot"][2, 3])
        rz = float(M["rightFoot"][2, 3])
        ground_bad = min(lz, rz) < 0.055
        # 重心（髋到双脚中点）
        hips = M["hips"][0:3, 3]
        mid = (M["leftFoot"][0:3, 3] + M["rightFoot"][0:3, 3]) * 0.5
        bal = float(np.linalg.norm((hips - mid)[0:2]) * 1000)
        # 对称（左右手镜像差）
        hr = M["rightHand"][0:3, 3]
        hl = M["leftHand"][0:3, 3]
        sym = float(np.linalg.norm(np.array([-hr[0], hr[1], hr[2]]) - hl) * 1000)

        row.update({"capsules": len(cv), "elbow_rev": max(el_r, el_l) >= 1.0,
                    "knee_rev": max(kn_r, kn_l) >= 1.0, "foot_low": ground_bad,
                    "balance_mm": round(bal, 0), "sym_mm": round(sym, 0),
                    "ankle_z": [round(lz, 3), round(rz, 3)]})
        ROWS.append(row)
        flag = "✓" if not cv and not ground_bad else "!"
        print(f"{name:<12} {'✓' if res['converged'] else '✗':>4} {len(cv):>4} "
              f"{'!' if max(el_r,el_l)>=1 else '·':>4} {'!' if max(kn_r,kn_l)>=1 else '·':>4} "
              f"{'!' if ground_bad else '·':>6} {bal:>7.0f} {sym:>7.0f}  {flag}")

    print()
    bad = [r for r in ROWS if (r.get("capsules", 0) or r.get("foot_low")
                               or r.get("elbow_rev") or r.get("knee_rev"))]
    print(f"有问题：{len(bad)} / {len(ROWS)}")
    for r in bad:
        print(f"  {r['family']:12s} 胶囊={r.get('capsules',0)} "
              f"脚低={r.get('foot_low')} 肘反={r.get('elbow_rev')} 膝反={r.get('knee_rev')} "
              f"踝z={r.get('ankle_z')}")

    out = os.path.join(ROOT, "docs", "pose_audit.json")
    json.dump(ROWS, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\nWROTE", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

