"""tools/simulate_chat.py — 用真 API 模拟用户和她说话（不合并契约）。

怎么测：python tools/simulate_chat.py
预期现象：每轮输出她说的话 + 解析出的动作，有动作就解姿态并报告位置
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
from llm.persona import load_memory, load_mood, load_persona, turn  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]

# 模拟用户会说的话（混合：明确指令 / 闲聊 / 情绪）
LINES = [
    "挥挥手跟我打个招呼",
    "今天累死了，啥也不想干",
    "过来让我看看你",
    "伸个懒腰吧",
]


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
    parser = DeepSeek(key, temperature=0.1, max_tokens=80)
    persona = load_persona()
    memory = load_memory()
    mood = load_mood()

    print(f"人格 {len(persona)} 字，记忆 {len(memory)} 字，心情 {mood.get('mood')}")
    print("=" * 62)

    stats = {"calls": 0, "act": 0, "solved": 0, "ms": 0.0}
    for i, line in enumerate(LINES, 1):
        t0 = time.perf_counter()
        r = turn(line, llm, rt.capabilities(), persona=persona, memory=memory,
                 mood=mood, body=True, parser_llm=parser)
        ms = (time.perf_counter() - t0) * 1000
        stats["calls"] += 1
        stats["ms"] += ms
        print(f"\n[{i}] 用户：{line}")
        print(f"    她：{redact(r['say'])}")
        if r.get("intent"):
            stats["act"] += 1
            print(f"    动作：{r['intent']}  mood_delta={r.get('mood_delta')}")
            res = rt.handle_intent({"intent": r["intent"]}, mood=mood)
            if res["entry"]:
                stats["solved"] += 1
                M = rt.pose_for(res["entry"])
                h = M["rightHand"][0:3, 3]
                print(f"    姿态：source={res['source']}  右手 z={h[2]:.3f}  x={h[0]:.3f}")
            else:
                gap = res.get("capability_gap") or {}
                print(f"    姿态：失败 ({gap.get('kind')}) {str(gap.get('suggested'))[:60]}")
        else:
            print("    动作：（没给，纯说话）")
        print(f"    [{ms:.0f}ms]")

    print("\n" + "=" * 62)
    print(f"轮数 {stats['calls']}  给动作 {stats['act']}  解出 {stats['solved']}  "
          f"总耗时 {stats['ms']:.0f}ms  平均 {stats['ms']/stats['calls']:.0f}ms/轮")
    print(f"库：{len(rt.library.entries)} 条  "
          f"状态 {set(e.status for e in rt.library.entries.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


