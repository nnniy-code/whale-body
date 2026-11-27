"""tests/test_lift_leg.py — 「抬腿」动作单元：左右各一条（v1.9）。

设计依据：
  1. 用户实测「让她抬腿，她不做」——模型里根本没有抬腿单元（P 只管上半身）
  2. 修好后又实测「换个腿」——她说右腿抬了，身体还是左腿
     → 原因是 `lift_leg` 的左右**写死在 G.lift 里**，解析器只能选族、不能带参数

现在拆成两条族：`lift_leg_left` / `lift_leg_right`，各带自己的 G.lift 和中文标签。

怎么测：python tests/test_lift_leg.py
预期现象：[lift_leg] ALL PASS
调参位置：config/seed_families.json 的 lift_leg_*.G.lift
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

    def feet(rt, fam):
        P = dict(fams[fam]["P_default"])
        b, diag = rt.build_full(P, fam, 0.0)
        M = rig.compute(b)
        return (float(M["leftFoot"][2, 3]), float(M["rightFoot"][2, 3]), diag)

    print("[1] 两条族都在，标签分左右")
    caps_info = mk().capabilities()
    check("有 lift_leg_left", "lift_leg_left" in caps_info["families"])
    check("有 lift_leg_right", "lift_leg_right" in caps_info["families"])
    check("旧的 lift_leg 已移除", "lift_leg" not in caps_info["families"])
    check("标签：抬左腿", caps_info["family_labels"].get("lift_leg_left") == "抬左腿",
          caps_info["family_labels"].get("lift_leg_left"))
    check("标签：抬右腿", caps_info["family_labels"].get("lift_leg_right") == "抬右腿",
          caps_info["family_labels"].get("lift_leg_right"))

    print("[2] 左：左脚离地")
    lz, rz, diag = feet(mk(), "lift_leg_left")
    check("左 > 右 +15cm", lz - rz > 0.15, f"left {lz:.3f} right {rz:.3f}")
    check("右脚在地面", abs(rz - 0.08) < 0.03, f"{rz:.3f}")
    check("IK 误差 < 1mm",
          max(diag["err_mm"].values()) < 1.0 if diag.get("err_mm") else True,
          str(diag.get("err_mm")))

    print("[3] 右：右脚离地（这就是「换个腿」要的）")
    lz2, rz2, diag2 = feet(mk(), "lift_leg_right")
    check("右 > 左 +15cm", rz2 - lz2 > 0.15, f"left {lz2:.3f} right {rz2:.3f}")
    check("左脚在地面", abs(lz2 - 0.08) < 0.03, f"{lz2:.3f}")
    check("IK 误差 < 1mm",
          max(diag2["err_mm"].values()) < 1.0 if diag2.get("err_mm") else True)

    print("[4] 先左后右：边真的换了")
    rt = mk()
    a_l, a_r, _ = feet(rt, "lift_leg_left")
    b_l, b_r, _ = feet(rt, "lift_leg_right")
    check("第一次左高", a_l > a_r)
    check("第二次右高", b_r > b_l)
    check("第二次左腿落回", abs(b_l - 0.08) < 0.03, f"{b_l:.3f}")

    print("[5] 不用这两族时脚是平的")
    lz3, rz3, _ = feet(mk(), "idle")
    check("idle 双脚等高", abs(lz3 - rz3) < 0.005, f"{abs(lz3-rz3)*1000:.1f}mm")

    print("[6] 只有这三族声明了 lift")
    owners = sorted(k for k, v in fams.items() if (v.get("G") or {}).get("lift"))
    # tiptoe 也是靠 lift 实现的（双脚各抬 4cm），不是单脚抬
    check("只有 lift_leg_left/right + tiptoe",
          owners == ["lift_leg_left", "lift_leg_right", "tiptoe"], str(owners))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
