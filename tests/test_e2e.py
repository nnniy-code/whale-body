"""tests/test_e2e.py — 全链路：一句话 → LLM → 求解 → 入库 → 姿态。

怎么测：python tests/test_e2e.py
预期现象：[e2e] ALL PASS
调参位置：llm/interpret.py、core/runtime.py
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
from llm.interpret import interpret  # noqa: E402

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
    caps_info = rt.capabilities()

    print("[1] stub LLM → interpret")
    reply = json.dumps({"say": "好呀，挥挥手～", "act": {
        "intent": "wave",
        "predicates": [{"name": "hand_above_head", "args": {"side": "right"}}],
        "dur_s": 1.2}})
    r = interpret("挥挥手", lambda s, u: reply, caps_info)
    check("解析成功", r["ok"], str(r["error"]))
    check("有 say", "挥" in r["say"])

    print("[2] interpret → runtime 求解入库")
    act = dict(r["obj"]["act"])
    act["say"] = r["say"]
    t0 = time.perf_counter()
    res = rt.handle_intent(act)
    e2e_ms = (time.perf_counter() - t0) * 1000
    check("source=L2", res["source"] == "L2", res["source"])
    check("入库", res["entry"] is not None)
    check("总耗时 < 2s", e2e_ms < 2000, f"{e2e_ms:.0f}ms")
    print(f"    e2e={e2e_ms:.1f}ms  arm_lift={res['entry'].params['P']['arm_lift']:.1f}")

    print("[3] 姿态可算")
    M = rt.pose_for(res["entry"])
    check("21 骨", all(b in M for b in MAIN21))
    check("右手举过头顶", M["rightHand"][2, 3] > M["head"][2, 3],
          f"{M['rightHand'][2,3]:.3f} vs {M['head'][2,3]:.3f}")
    check("左手没动", abs(M["leftHand"][2, 3] - rf.rest["leftHand"][2, 3]) < 0.05,
          f"{M['leftHand'][2,3]:.3f}")

    print("[4] 同一句话第二次 → L1")
    res2 = rt.handle_intent(act)
    check("source=L1", res2["source"] == "L1", res2["source"])
    check("0.1ms 级", res2["lookup_ms"] < 1.0, f"{res2['lookup_ms']}ms")

    print("[5] LLM 说做不到 → 不崩")
    r2 = interpret("翻个跟头", lambda s, u: json.dumps({"say": "这个我做不到", "act": None}), caps_info)
    check("ok（允许做不到）", r2["ok"])
    check("有回话", bool(r2["say"]))

    print("[6] LLM 编造族 → 被拒 → 能力缺口路径")
    r3 = interpret("翻跟头", lambda s, u: json.dumps(
        {"say": "x", "act": {"intent": "backflip", "predicates": [
            {"name": "hand_above_head", "args": {"side": "left"}}]}}), caps_info, retries=0)
    check("被拒", not r3["ok"])
    res3 = rt.handle_intent({"intent": "backflip", "predicates": [
        {"name": "hand_above_head", "args": {"side": "left"}}]})
    check("runtime 给 family_missing",
          res3["capability_gap"]["kind"] == "family_missing",
          str(res3["capability_gap"]["kind"]))

    print("[7] 库最终状态")
    check("2 条以上", len(rt.library.entries) >= 1, str(len(rt.library.entries)))
    check("入库即 verified（立即验证，不设 provisional）",
          all(e.status == "verified" for e in rt.library.entries.values()),
          str({e.status for e in rt.library.entries.values()}))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

