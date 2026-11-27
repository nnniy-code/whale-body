"""tools/simulate_two_stage.py — 用真 API 跑两阶段（她自由说话 + 解析器判动作）。

怎么测：python tools/simulate_two_stage.py
预期现象：每轮显示 ①她说了什么 ②解析器判了什么 ③姿态解出没有
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
from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402
from llm.client import DeepSeek, find_api_key, redact  # noqa: E402
from llm.persona import load_memory, load_mood, load_persona, turn_two_stage  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]
LINES = ["挥挥手跟我打个招呼", "今天累死了，啥也不想干", "过来让我看看你", "伸个懒腰吧"]


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
    key = find_api_key()
    llm = DeepSeek(key, temperature=0.85, max_tokens=350)
    # 解析器：同一个模型，但输出只要几十个 token，temperature 也低（只做分类）
    parser = DeepSeek(key, temperature=0.1, max_tokens=80)
    persona = load_persona()
    memory = load_memory()
    mood = load_mood()
    c = rt.capabilities()

    print(f"人格 {len(persona)} 字 / 记忆 {len(memory)} 字 / 心情 {mood.get('mood')}")
    stats = {"calls": 0, "intent": 0, "solved": 0, "ms": 0.0}

    for i, line in enumerate(LINES, 1):
        t0 = time.perf_counter()
        r = turn_two_stage(line, llm, c, persona, memory, mood, parser_llm=parser)
        ms = (time.perf_counter() - t0) * 1000
        stats["calls"] += 2
        stats["ms"] += ms
        print(f"\n[{i}] 用户：{line}")
        print(f"    她说：{redact(r['say'])}")
        print(f"    解析：intent={r['intent']}  mood_delta={r['mood_delta']}")
        if r["intent"]:
            stats["intent"] += 1
            res = rt.handle_intent({"intent": r["intent"]}, mood=mood)
            if res["entry"]:
                stats["solved"] += 1
                M = rf.compute(pose_gen.build(res["play_P"], rf, bm,
                                              active_side=_side_of(res)))
                h = M["rightHand"][0:3, 3]
                print(f"    姿态：{res['source']}  右手 z={h[2]:.3f}  x={h[0]:.3f}  "
                      f"bias={res['emotion_bias'].get('changes')}")
            else:
                gap = res.get("capability_gap") or {}
                print(f"    姿态：失败 {gap.get('kind')}")
        print(f"    [{ms:.0f}ms]")

    print("\n" + "=" * 60)
    print(f"{len(LINES)} 轮  {stats['calls']} 次调用  给动作 {stats['intent']}  解出 {stats['solved']}  "
          f"总 {stats['ms']:.0f}ms  平均 {stats['ms']/len(LINES):.0f}ms/轮")
    return 0


def _side_of(res):
    side = ((res.get("entry").metrics or {}).get("side") if res.get("entry") else None)
    return side


if __name__ == "__main__":
    raise SystemExit(main())

