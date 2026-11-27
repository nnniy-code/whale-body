"""tests/test_seed_families.py — 种子族校验。

怎么测：python tests/test_seed_families.py
预期现象：[seed] ALL PASS（12 族，范围自洽）
调参位置：config/seed_families.json
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "config", "seed_families.json")

P_GLOBAL = {
    "lean": (-35, 35), "side_bend": (-25, 25),
    "head_nod": (-30, 30), "head_tilt": (-30, 30), "head_yaw": (-45, 45),
    "arm_swing": (-60, 60), "arm_lift": (-20, 180),
    "arm_roll": (-90, 90), "wrist_bend": (-45, 45),
    "elbow_bend": (0, 140),
    "hand_bias": (-40, 40), "tail_curl": (-40, 40),
}
G_GLOBAL = {
    "duty": (0.30, 1.00), "period": (0.40, 1.50), "stride": (-0.90, 0.90),
    "foot_lift": (0.0, 0.18), "stance_width": (0.05, 0.35),
    "landing_height": (-0.03, 0.03), "ankle_bias": (-30, 30),
    "knee_bias": (-10, 120), "turn_rate": (-180, 180), "phase_offset": (0.30, 0.70),
}
FOOTFALL = {"alternate", "same_side", "fixed"}

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    with open(SRC, "r", encoding="utf-8") as f:
        d = json.load(f)
    fams = d["families"]

    print("[1] 结构")
    check("至少 12 个族", len(fams) >= 12, str(len(fams)))
    check("有 _global.G", "G" in d["_global"])

    print("[2] 每族字段与范围")
    bad_range, bad_default, bad_g, bad_footfall = [], [], [], []
    for name, f in fams.items():
        if name == "free":      # free 是全开族，P_range 故意超出普通族的 _global
            continue
        for need in ("label", "G", "P_default", "P_range", "thresholds", "predicates_hint"):
            if need not in f:
                bad_range.append(f"{name}.{need} 缺失")
        for p, (lo, hi) in P_GLOBAL.items():
            if p not in f["P_range"]:
                bad_range.append(f"{name}.P_range.{p} 缺失")
                continue
            r = f["P_range"][p]
            if r[0] < lo or r[1] > hi:
                bad_range.append(f"{name}.P_range.{p}={r} 超出全局 ({lo},{hi})")
            v = f["P_default"][p]
            if not (r[0] - 1e-9 <= v <= r[1] + 1e-9):
                bad_default.append(f"{name}.P_default.{p}={v} 不在 range {r}")
        for g, (lo, hi) in G_GLOBAL.items():
            v = f["G"].get(g)
            if v is None:
                bad_g.append(f"{name}.G.{g} 缺失")
            elif not (lo - 1e-9 <= v <= hi + 1e-9):
                bad_g.append(f"{name}.G.{g}={v} 超出 ({lo},{hi})")
        if f["G"].get("footfall") not in FOOTFALL:
            bad_footfall.append(f"{name}.footfall={f['G'].get('footfall')}")

    check("P_range 合法且 ⊂ 全局", bad_range == [], str(bad_range[:3]))
    check("P_default ∈ P_range", bad_default == [], str(bad_default[:3]))
    check("G 字段齐全且在范围内", bad_g == [], str(bad_g[:3]))
    check("footfall 枚举合法", bad_footfall == [], str(bad_footfall[:3]))

    print("[3] 步态语义")
    check("walk duty = 0.62", abs(fams["walk"]["G"]["duty"] - 0.62) < 1e-9)
    check("run duty = 0.35（<0.5 才有腾空）", fams["run"]["G"]["duty"] < 0.5)
    check("run 跳过腾空检查", fams["run"]["thresholds"].get("skip_airborne_check") is True)
    check("walk 不跳过", fams["walk"]["thresholds"].get("skip_airborne_check") is False)
    check("sit/kneel/rest duty = 1.0", all(
        abs(fams[k]["G"]["duty"] - 1.0) < 1e-9 for k in ("sit", "kneel", "rest")))
    check("kneel 屈膝 95°", abs(fams["kneel"]["G"]["knee_bias"] - 95) < 1e-9)
    check("spin 有 turn_rate", abs(fams["spin"]["G"]["turn_rate"]) > 0)

    print("[4] 与非种子族的兼容")
    check("_global 可作兜底", all(p in d["_global"]["G"] for p in G_GLOBAL))

    print("[5] 每族至少一条提示谓词")
    empty = [n for n, f in fams.items() if not f["predicates_hint"]]
    check("无空 hint", empty == [], str(empty))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print(f"RESULT: PASS ({len(fams)} families)")
    return 0


if __name__ == "__main__":
    sys.exit(main())





