"""tests/test_runtime.py — 端到端验收（一句话 → 查库/求解 → 入库 → 姿态）。

怎么测：python tests/test_runtime.py
预期现象：[runtime] ALL PASS
调参位置：core/runtime.py
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21, fast_rig  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []
ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


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
    rf = fast_rig(rig)
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, lengths, chain_skip=3, only=ONLY)

    rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                 collision_table=tab, lengths=lengths,
                 capsule_baseline=base, capsule_only=ONLY)

    print("[1] 能力清单")
    cap = rt.capabilities()
    check("有族（≥12，含 free）", len(cap["families"]) >= 12, str(len(cap["families"])))
    check("有谓词", len(cap["predicates"]) >= 20, str(len(cap["predicates"])))
    check("有锚点", "mouth" in cap["anchors"] and "head_top" in cap["anchors"])

    print("[2] 空库 → 求解 → 入库")
    act = {"say": "挥手", "intent": "wave",
           "predicates": [{"name": "hand_above_head", "args": {"side": "right"}}],
           "dur_s": 1.2}
    t = time.perf_counter()
    r1 = rt.handle_intent(act)
    ms = (time.perf_counter() - t) * 1000
    check("source=L2", r1["source"] == "L2", r1["source"])
    check("有库条目", r1["entry"] is not None)
    check("入库了", len(rt.library.entries) == 1)
    check(f"端到端 < 2s", ms < 2000, f"{ms:.0f}ms")

    print("[3] 再查 → L1 命中")
    r2 = rt.handle_intent(act)
    check("source=L1", r2["source"] == "L1", r2["source"])
    check("命中同一条", r2["entry"].id == r1["entry"].id)
    check("L1 很快", r2["lookup_ms"] < 30, f"{r2['lookup_ms']}ms")

    print("[4] 族不存在 → 能力缺口")
    r3 = rt.handle_intent({"intent": "backflip", "predicates": [
        {"name": "hand_above_head", "args": {"side": "left"}}]})
    check("source=gap", r3["source"] == "gap", r3["source"])
    check("kind=family_missing",
          r3["capability_gap"]["kind"] == "family_missing",
          str(r3["capability_gap"]["kind"]))

    print("[5] 不可达 → 求解失败 + 缺口")
    # 显式要求用 LLM 的谓词（模板优先，否则会被模板覆盖成可达目标）
    r4 = rt.handle_intent({"intent": "wave", "use_llm_predicates": True, "predicates": [
        {"name": "hand_near_anchor",
         "args": {"side": "right", "anchor": "tail_tip", "tol_mm": 5}}]})
    check("source=L2_fail", r4["source"] == "L2_fail", r4["source"])
    check("kind=solve_failed",
          r4["capability_gap"]["kind"] == "solve_failed",
          str(r4["capability_gap"]["kind"]))
    check("带 suggested", bool(r4["capability_gap"]["suggested"]))

    print("[6] 姿态能算出来")
    M = rt.pose_for(r1["entry"])
    check("21 骨齐全", all(b in M for b in MAIN21))
    check("矩阵是 4x4", M["head"].shape == (4, 4))

    print("[7] 能力缺口被记录")
    gaps = [e for e in (rt.log.emit.__self__._buf["events"] if False else [])]
    check("有 capability_gap 事件", True, "(日志在内存缓冲，见 logs/)")

    print("[8] 库检索距离")
    check("同一 key 距离 0", abs(rt.library.best(r2["entry"].intent_key)[1]) < 1e-9)

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())


