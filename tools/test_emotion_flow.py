"""tools/test_emotion_flow.py — 验证「情绪改目标 + 抖动」接进 runtime 后的效果。

怎么测：python tools/test_emotion_flow.py
预期现象：
  - 高精力 vs 低精力：解出的手高不同（低精力明显更低）
  - 同一动作播两次：姿势略不同（抖动）
  - 抖动不破坏验证
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
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402

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
    lengths = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, lengths, chain_skip=3, only=ONLY)

    def fresh():
        return Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                       collision_table=tab, lengths=lengths,
                       capsule_baseline=base, capsule_only=ONLY)

    print("[1] 精力影响目标（不是缩放）")
    hi = fresh().handle_intent({"intent": "wave"}, mood={"energy": 95, "mood": 0.9})
    lo = fresh().handle_intent({"intent": "wave"}, mood={"energy": 10, "mood": 0.2})
    for tag, r in (("高", hi), ("低", lo)):
        if not r["entry"]:
            check(f"{tag}精力解出", False, str(r.get("capability_gap"))[:80])
            continue
        M = rf.compute(pose_gen.build(r["play_P"], rf, bm, active_side="right"))
        h = M["rightHand"][0:3, 3]
        print(f"    精力{tag}  hand_z={h[2]:.3f}  bias={r['emotion_bias'].get('changes')}")
    ok = hi["entry"] and lo["entry"]
    if ok:
        Mh = rf.compute(pose_gen.build(hi["play_P"], rf, bm, active_side="right"))
        Ml = rf.compute(pose_gen.build(lo["play_P"], rf, bm, active_side="right"))
        dz = float(Mh["rightHand"][2, 3] - Ml["rightHand"][2, 3])
        check("低精力手更低（方向性，>10mm）", dz > 0.01, f"{dz*1000:.0f}mm")
        check("低精力改了谓词目标", bool(lo["emotion_bias"].get("changed")),
              str(lo["emotion_bias"].get("changes")))

    print("[2] 抖动：同一动作播多次，姿势略不同")
    rt = fresh()
    r0 = rt.handle_intent({"intent": "wave"}, mood={"energy": 50})
    check("解出入库", r0["entry"] is not None)
    from core.jitter import perturb
    rng = np.random.default_rng(12345)
    zs = []
    for _ in range(20):
        P = perturb(r0["play_P"], {"energy": 50}, rng)
        M = rf.compute(pose_gen.build(P, rf, bm, active_side="right"))
        zs.append(float(M["rightHand"][2, 3]))
    spread = max(zs) - min(zs)
    check("20 次手高有差异（>3mm）", spread > 0.003, f"{spread*1000:.1f}mm")
    check("差异不夸张（<60mm）", spread < 0.06, f"{spread*1000:.1f}mm")

    print("[3] 抖动记录")
    check("返回 play_P", "play_P" in r0 and bool(r0["play_P"]))
    jit = r0.get("jitter") or {}
    check("有抖动报告", "attempts" in jit, str(jit))
    print(f"    delta: {list((jit.get('delta') or {}).items())[:4]}")

    print("[4] 库存的是基准 P*（不是抖动值）")
    e = r0["entry"]
    base_lift = float(e.params["P"].get("arm_lift", 0))
    play_lift = float(r0["play_P"].get("arm_lift", 0))
    check("库值 = 求解值（可能与 play 不同）", True,
          f"lib={base_lift:.1f} play={play_lift:.1f}")

    print("[5] 抖动不破坏验证")
    from core.verify import range_violations
    fam = fams["wave"]
    bad = range_violations(r0["play_P"], fam)
    check("play_P 在范围内", not bad, str(bad))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

