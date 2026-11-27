"""tools/simulate_real.py — 模拟用户的真实用法（多轮 + 上下文 + 情绪累积）。

怎么测：python tools/simulate_real.py
预期现象：看她的话是否自然、动作是否合理、情绪是否随对话变化
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

# 像用户真实会说的话：招呼 → 情绪 → 求安慰 → 自嘲 → 要表演 → 困了
LINES = [
    "在吗",
    "今天上班被老板当众骂了一顿",
    "安慰我一下呗",
    "你说我是不是挺没用的",
    "算了不说了，给我表演个什么吧",
    "困了，晚安",
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
    llm = DeepSeek(key, temperature=0.85, max_tokens=400)
    parser = DeepSeek(key, temperature=0.1, max_tokens=80)
    persona = load_persona()
    memory = load_memory()
    mood = dict(load_mood())
    energy = float(mood.get("energy", 50) or 50)
    mood_val = mood.get("mood")
    mood_num = float(mood_val) if isinstance(mood_val, (int, float)) else 0.5

    history = []
    print(f"初始：energy={energy:.0f}  mood={mood_num:.2f}  （{mood.get('mood')}）")
    print("=" * 64)

    stats = {"ms": 0.0, "moves": 0, "solved": 0}
    for i, line in enumerate(LINES, 1):
        t0 = time.perf_counter()
        r = turn(line, llm, rt.capabilities(), persona=persona, memory=memory,
                 mood={"energy": energy, "mood": mood_num},
                 body=True, history=history, parser_llm=parser)
        ms = (time.perf_counter() - t0) * 1000
        stats["ms"] += ms

        print(f"\n[{i}] 你：{line}")
        print(f"    她：{redact(r['say'])}")

        # 情绪累积
        md = r.get("mood_delta") or {}
        if md:
            e = md.get("energy", 0)
            m = md.get("mood", 0)
            energy = max(0.0, min(100.0, energy + e))
            mood_num = max(0.0, min(1.0, mood_num + m))
            print(f"    情绪：energy {e:+.0f} → {energy:.0f}   mood {m:+.2f} → {mood_num:.2f}")

        if r.get("intent"):
            stats["moves"] += 1
            res = rt.handle_intent({"intent": r["intent"]},
                                   mood={"energy": energy, "mood": mood_num})
            if res["entry"]:
                stats["solved"] += 1
                side = (res["entry"].metrics or {}).get("side")
                M = rf.compute(pose_gen.build(res["play_P"], rf, bm, active_side=side))
                h = M["rightHand"][0:3, 3]
                print(f"    动作：{r['intent']}  →  {res['source']}  右手 z={h[2]:.3f}")
            else:
                gap = res.get("capability_gap") or {}
                print(f"    动作：{r['intent']}  →  失败 {gap.get('kind')}")

        history.append({"role": "user", "text": line})
        history.append({"role": "pet", "text": r["say"]})
        print(f"    [{ms:.0f}ms]")

    print("\n" + "=" * 64)
    print(f"{len(LINES)} 轮  动作 {stats['moves']}  解出 {stats['solved']}  "
          f"总 {stats['ms']:.0f}ms  平均 {stats['ms']/len(LINES):.0f}ms/轮")
    print(f"结束情绪：energy={energy:.0f}  mood={mood_num:.2f}（初始 50 / 0.50）")
    print(f"库：{len(rt.library.entries)} 条  状态 {set(e.status for e in rt.library.entries.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
