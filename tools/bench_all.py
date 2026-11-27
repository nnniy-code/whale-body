"""tools/bench_all.py — 全链路性能测试。

怎么测：python tools/bench_all.py
产物：docs/PERF.md
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
from core.blend import blend_basis  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, capsule_gaps, check_capsules, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library, intent_key  # noqa: E402
from core.overlay import merge_layers  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21, fast_rig, solve  # noqa: E402
from core.verify import verify_pose  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]
ROWS: list[tuple[str, float, str]] = []


def bench(name: str, fn, n: int = 50, note: str = "") -> float:
    fn()                                    # 预热
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    ms = (time.perf_counter() - t0) / n * 1000
    ROWS.append((name, ms, note))
    print(f"  {name:36s} {ms:8.3f} ms   {note}")
    return ms


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
    base_gaps = baseline_gaps(rf.compute(zero), caps, rf, lengths, chain_skip=3, only=ONLY)

    P_wave = dict(fams["wave"]["P_default"])
    P_wave["arm_lift"] = 95
    P_wave["elbow_bend"] = 80
    basis_wave = pose_gen.build(P_wave, rig, bm, active_side="right")
    M_wave = rf.compute(basis_wave)

    print("\n=== 1. 基础 ===")
    bench("FK 209 骨", lambda: rig.compute(zero), 20, "全骨")
    bench("FK 21 骨（fast）", lambda: rf.compute(zero), 100, "求解用")
    bench("pose_gen（12 维 P）", lambda: pose_gen.build(P_wave, rig, bm, active_side="right"), 100)

    print("\n=== 2. 碰撞 ===")
    bench("胶囊（21 主骨）", lambda: capsule_gaps(M_wave, caps, rf, lengths, 3, ONLY), 100)
    bench("胶囊 + 阈值判定", lambda: check_capsules(M_wave, caps, rf, tab, baseline=base_gaps,
                                                lengths=lengths, only=ONLY), 100)

    print("\n=== 3. 复合 ===")
    bench("动作混合（slerp）", lambda: blend_basis(basis_wave, basis_wave, 0.5), 100)
    bench("两层叠加", lambda: merge_layers(basis_wave, [(basis_wave, ["arm.right"])]), 100)
    bench("三层叠加", lambda: merge_layers(basis_wave, [
        (basis_wave, ["arm.right"], 0.9),
        (basis_wave, ["head"], 0.5)]), 100)

    print("\n=== 4. 求解 ===")
    tpl = fams["wave"]["predicates_template"]
    bench("求解 wave（5 谓词）", lambda: solve(rig, bm, fams["wave"], tpl, anch, budget=3000,
                                            seed=3, active_side="right"), 10,
          "预算 3000，实际约 46")

    print("\n=== 5. 验证 ===")
    bench("verify_pose（范围+谓词+胶囊）", lambda: verify_pose(P_wave, fams["wave"], tpl, M_wave,
                                                          rf, anch, caps, tab, lengths,
                                                          base_gaps, side="right"), 50)

    print("\n=== 6. 库 ===")
    lib = Library()
    for i in range(100):
        lib.put("wave", intent_key([{"name": "hand_above_head", "args": {"side": "right"}}], f"f{i}"),
                {"P": dict(P_wave)}, dur_s=1.0, status="verified")
    bench("查库命中", lambda: lib.best(intent_key([{"name": "hand_above_head",
                                                "args": {"side": "right"}}], "wave")), 200,
          "100 条")
    p = os.path.join(ROOT, "docs", "_bench_lib.json")
    bench("存库（100 条）", lambda: lib.save(p), 20)
    bench("读库（100 条）", lambda: Library.open(p), 20)
    if os.path.exists(p):
        os.remove(p)

    print("\n=== 7. 端到端 ===")
    def fresh_runtime():
        return Runtime(rig, bm, anch, fams, Library(), capsules=caps, collision_table=tab,
                       lengths=lengths, capsule_baseline=base_gaps, capsule_only=ONLY)

    # L2：每次都用空库的新 Runtime，否则第二次就命中 L1 了
    def one_l2():
        fresh_runtime().handle_intent({"intent": "wave"})

    bench("handle_intent（L2 求解+验证+入库）", one_l2, 5, "空库，每次重新解")

    rt = fresh_runtime()
    rt.handle_intent({"intent": "wave"})          # 先灌一条
    bench("handle_intent（L1 查库命中）",
          lambda: rt.handle_intent({"intent": "wave"}), 100, "命中已有")
    bench("pose_for（库条目 → 姿态）",
          lambda: rt.pose_for(list(rt.library.entries.values())[0]), 50)

    # ---- 汇总 ----
    print("\n" + "=" * 58)
    total_frame = [r for r in ROWS if "FK 21" in r[0]][0][1] + \
                  [r for r in ROWS if "pose_gen" in r[0]][0][1] + \
                  [r for r in ROWS if "动作混合" in r[0]][0][1]
    print(f"单帧预算（FK21 + pose_gen + 混合）：{total_frame:.3f} ms → 理论 {1000/total_frame:.0f} FPS")
    l1 = [r for r in ROWS if "L1" in r[0]][0][1]
    print(f"L1 响应：{l1:.3f} ms")
    l2 = [r for r in ROWS if "L2" in r[0]][0][1]
    print(f"L2 响应：{l2:.1f} ms")

    out = os.path.join(ROOT, "docs", "PERF.md")
    lines = ["# 性能实测（`tools/bench_all.py`）", "",
             "| 环节 | ms | 备注 |", "|---|---|---|"]
    for name, ms, note in ROWS:
        lines.append(f"| {name} | {ms:.3f} | {note} |")
    lines += ["", f"**单帧预算**：{total_frame:.3f} ms（{1000/total_frame:.0f} FPS）",
              f"**L1 响应**：{l1:.3f} ms  ",
              f"**L2 响应**：{l2:.1f} ms  "]
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\nWROTE", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

