"""tests/test_jitter.py — 动作抖动验收。

怎么测：python tests/test_jitter.py
预期现象：[jitter] ALL PASS
调参位置：core/jitter.py 的 SPAN / SENSITIVITY / mood_scale
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import pose_gen  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.jitter import clamp_to_range, mood_scale, perturb, safe_perturb  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    P0 = dict(fams["wave"]["P_default"])
    P0["arm_lift"] = 95
    P0["elbow_bend"] = 80

    print("[1] 情绪 → 抖动幅度")
    s_hi, d_hi = mood_scale({"energy": 100, "mood": 0.9})
    s_lo, d_lo = mood_scale({"energy": 10, "mood": 0.1})
    check("高精力抖得大", s_hi > s_lo, f"{s_hi:.4f} vs {s_lo:.4f}")
    check("低精力整体下沉", d_lo < d_hi, f"drift {d_lo:.4f} vs {d_hi:.4f}")
    check("幅度在 1.5%~5% 内", 0.01 < s_lo and s_hi < 0.06, f"{s_lo:.4f}~{s_hi:.4f}")

    print("[2] 抖动不改变类型/键")
    P1 = perturb(P0, {"energy": 50}, np.random.default_rng(1))
    check("键集合不变", set(P1) == set(P0))
    check("全是数字", all(isinstance(v, float) for v in P1.values()))

    print("[3] 每次不同（消除机器人感）")
    vals = set()
    for i in range(20):
        P = perturb(P0, {"energy": 60}, np.random.default_rng(i))
        vals.add(round(P["arm_lift"], 6))
    check("20 次有 ≥18 个不同值", len(vals) >= 18, f"{len(vals)} 个")

    print("[4] 抖动幅度合理（不夸张）")
    lifts = [perturb(P0, {"energy": 50}, np.random.default_rng(i))["arm_lift"] for i in range(200)]
    spread = max(lifts) - min(lifts)
    check("arm_lift 波动 < 20°", spread < 20, f"{spread:.1f}°")
    check("均值接近原值（±3°）", abs(np.mean(lifts) - P0["arm_lift"]) < 3,
          f"{np.mean(lifts):.1f} vs {P0['arm_lift']}")

    print("[5] 低精力整体偏低")
    hi = np.mean([perturb(P0, {"energy": 100}, np.random.default_rng(i))["arm_lift"] for i in range(100)])
    lo = np.mean([perturb(P0, {"energy": 10}, np.random.default_rng(i))["arm_lift"] for i in range(100)])
    check("低精力 arm_lift 更低", lo < hi, f"{lo:.1f} vs {hi:.1f}")

    print("[6] clamp 回范围")
    bad = dict(P0)
    bad["arm_lift"] = 300.0
    c = clamp_to_range(bad, fams["wave"])
    lo_b, hi_b = fams["wave"]["P_range"]["arm_lift"]
    check("夹回上限", c["arm_lift"] <= hi_b, f"{c['arm_lift']} <= {hi_b}")

    print("[7] 带守卫的抖动")
    def always_ok(P):
        return True
    def never_ok(P):
        return False
    r1, rep1 = safe_perturb(P0, fams["wave"], {"energy": 50}, always_ok, np.random.default_rng(3))
    check("守卫通过 → 用抖动值", rep1["ok"] and not rep1["fallback"])
    check("有 delta 记录", bool(rep1["delta"]), str(list(rep1["delta"])[:3]))
    r2, rep2 = safe_perturb(P0, fams["wave"], {"energy": 50}, never_ok, np.random.default_rng(3))
    check("守卫拒绝 → 回退原值", rep2["fallback"])
    check("回退后完全等于原值", all(abs(r2[k] - P0[k]) < 1e-12 for k in P0))

    print("[8] 抖动后 FK 正常")
    M = rig.compute(pose_gen.build(perturb(P0, {"energy": 50}, np.random.default_rng(7)),
                                   rig, bm, active_side="right"))
    check("无 NaN", all(np.isfinite(m).all() for m in M.values()))
    h = M["rightHand"][0:3, 3]
    check("手仍举高（>0.75）", float(h[2]) > 0.75, f"z={h[2]:.3f}")

    print("[9] 抖动可复现（同 seed 同结果）")
    a = perturb(P0, {"energy": 60}, np.random.default_rng(99))
    b = perturb(P0, {"energy": 60}, np.random.default_rng(99))
    check("同 seed 完全一致", all(abs(a[k] - b[k]) < 1e-12 for k in a))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
