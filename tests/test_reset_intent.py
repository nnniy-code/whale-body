"""tests/test_reset_intent.py — 回待机解析 + 微动作不累计 验收。

怎么测：python tests/test_reset_intent.py
预期现象：[reset_intent] ALL PASS
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
from llm.persona import INSTRUCTION_ACTS, instruction_act  # noqa: E402

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
    lengths = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, lengths, chain_skip=3, only=ONLY)
    rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                 collision_table=tab, lengths=lengths,
                 capsule_baseline=base, capsule_only=ONLY)

    print("[1] 指令表里有 reset")
    resets = [k for k, v in INSTRUCTION_ACTS.items() if v.get("intent") == "reset"]
    print(f"    触发词: {resets}")
    check("至少有 4 个触发词", len(resets) >= 4, str(len(resets)))

    print("[2] instruction_act 能识别各种说法")
    for kw in ("回待机", "站起来", "站直", "正常一点", "别端着"):
        act = instruction_act(kw)
        check(f"「{kw}」→ reset", act and act.get("intent") == "reset",
              str(act and act.get("intent")))

    print("[3] reset intent 清空姿态、保留位置")
    tr = rt.plan_trajectory({"intent": "wave"}, n_frames=10)
    rt.commit_trajectory(tr)
    rt.body.x = 1.5
    rt.body.y = -2.5
    check("有保持姿态", rt.body.has_pose())
    r = rt.handle_intent({"intent": "reset"})
    check("返回 reset=True", r.get("reset") is True)
    check("姿态清空", not rt.body.has_pose())
    check("位置保留 x", abs(rt.body.x - 1.5) < 1e-9, f"x={rt.body.x}")
    check("位置保留 y", abs(rt.body.y + 2.5) < 1e-9, f"y={rt.body.y}")

    print("[4] reset 后下一个动作从 idle 出发")
    tr2 = rt.plan_trajectory({"intent": "wave"}, n_frames=10)
    check("起点是 idle",
          abs(float(tr2["frames"][0].get("arm_lift", 0))
              - float(fams["idle"]["P_default"].get("arm_lift", 0))) < 1e-6,
          str(tr2["frames"][0].get("arm_lift")))

    print("[5] 解析器 prompt 里有「微动作不累计」规则")
    from llm.persona import PARSE_SYSTEM
    check("提到微动作", "微动作不累计" in PARSE_SYSTEM)
    check("提到闲聊调味", "调味" in PARSE_SYSTEM)
    check("提到 reset", "reset" in PARSE_SYSTEM)

    print("[6] 「回待机」不会匹配成别的动作")
    check("回待机 → reset（不是 idle）",
          instruction_act("回待机")["intent"] == "reset")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
