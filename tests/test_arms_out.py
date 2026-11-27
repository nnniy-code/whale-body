"""tests/test_arms_out.py — 「双手平举」（v1.9 新增）。

设计依据：用户实测「让双手平举，变成一只手挥手」。两个原因：
  1. 动作表里没有「双手平举」这个单元 → 落到单手族的 wave/point
  2. `_infer_side` 对带 side 的谓词默认取 right → `active_side="right"` 只驱动右手

修法：新增族 `arms_out`（标签「双手平举」）+ `two_handed` 标记强制两侧。

**一个实测坑**：`arm_lift` 不是「从垂手起算的角度」。
  88 时手 z=0.911（肩 0.783，举过头），**60 才是水平**（z=0.791，横向 0.324 最远）。

怎么测：python tests/test_arms_out.py
预期现象：[arms_out] ALL PASS
调参位置：config/seed_families.json 的 arms_out.P_default.arm_lift
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


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
    L = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, L, chain_skip=3, only=ONLY)

    def mk():
        return Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                       collision_table=tab, lengths=L,
                       capsule_baseline=base, capsule_only=ONLY)

    print("[1] 族存在 + 标签 + 双手标记")
    info = mk().capabilities()
    check("capabilities 含 arms_out", "arms_out" in info["families"])
    check("标签：双手平举", info["family_labels"].get("arms_out") == "双手平举",
          info["family_labels"].get("arms_out"))
    check("配置 two_handed=True", fams["arms_out"].get("two_handed") is True)

    print("[2] 两只手都抬起来，而且到肩高（不是举过头）")
    rt = mk()
    P = dict(fams["arms_out"]["P_default"])
    b, diag = rt.build_full(P, "arms_out", 0.0)
    M = rig.compute(b)
    lz = float(M["leftHand"][2, 3])
    rz = float(M["rightHand"][2, 3])
    sh = float(M["leftShoulder"][2, 3])
    hd = float(M["head"][2, 3])
    check("左右手都抬高（> 垂手 0.572）", lz > 0.70 and rz > 0.70, f"L {lz:.3f} R {rz:.3f}")
    check("接近肩高（±6cm）", abs(lz - sh) < 0.06, f"手 {lz:.3f} / 肩 {sh:.3f}")
    check("没有举过头顶", lz < hd - 0.02, f"手 {lz:.3f} / 头 {hd:.3f}")
    check("左右对称", abs(lz - rz) < 0.02, f"{lz:.3f} vs {rz:.3f}")
    lx = abs(float(M["leftHand"][0, 3]))
    check("往两侧伸（|x| > 0.29）", lx > 0.29, f"{lx:.3f}")

    print("[3] two_handed 会把「只驱动右手」顶掉")
    act = {"intent": "arms_out",
           "predicates": [{"name": "hand_above_shoulder", "args": {"side": "right"}}],
           "use_llm_predicates": True}
    res = rt.handle_intent(act)
    if res.get("entry"):
        side = (res["entry"].metrics or {}).get("side")
        check("entry.metrics.side 被强制为 None", side is None, str(side))
        b2, _ = rt.build_full(res["entry"].params.get("P", {}), "arms_out", 0.0)
        M2 = rig.compute(b2)
        check("两只手都在肩以上",
              float(M2["leftHand"][2, 3]) > 0.70 and float(M2["rightHand"][2, 3]) > 0.70,
              f"L {float(M2['leftHand'][2,3]):.3f} R {float(M2['rightHand'][2,3]):.3f}")
    else:
        check("能解出来（先不追究侧别）", False, str(res.get("source")))

    print("[4] 和单手族区分清楚")
    check("wave 仍然单手（有模板）", bool(fams["wave"].get("predicates_template")))
    check("point 没有 two_handed", not fams["point"].get("two_handed"))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
