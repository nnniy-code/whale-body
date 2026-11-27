"""tools/simulate_grow.py — 真实对话 + 能力缺口统计 + 族生长检查。

怎么测：python tools/simulate_grow.py
预期现象：每轮显示她说了什么/解析结果；最后统计哪些动作有族、哪些是缺口
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
from core.grow import propose  # noqa: E402
from core.library import Library  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402
from llm.client import DeepSeek, find_api_key, redact  # noqa: E402
from llm.persona import load_memory, load_mood, load_persona, turn  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]

LINES = [
    "在吗",
    "今天被老板当众骂了",
    "靠过来一点嘛",
    "挥挥手给我看看",
    "你说我是不是挺没用的",
    "算了，伸个懒腰吧",
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
    mood0 = load_mood()
    energy = float(mood0.get("energy", 50) or 50)
    mv = mood0.get("mood")
    mood_num = float(mv) if isinstance(mv, (int, float)) else 0.5

    history = []
    gaps = []
    print(f"初始 energy={energy:.0f} mood={mood_num:.2f}")
    print("=" * 66)

    for i, line in enumerate(LINES, 1):
        t0 = time.perf_counter()
        r = turn(line, llm, rt.capabilities(), persona=persona, memory=memory,
                 mood={"energy": energy, "mood": mood_num},
                 body=True, history=history, parser_llm=parser)
        ms = (time.perf_counter() - t0) * 1000
        print(f"\n[{i}] 你：{line}")
        print(f"    她：{redact(r['say'])}")

        md = r.get("mood_delta") or {}
        if md:
            energy = max(0.0, min(100.0, energy + md.get("energy", 0)))
            mood_num = max(0.0, min(1.0, mood_num + md.get("mood", 0)))
            print(f"    情绪：→ energy={energy:.0f} mood={mood_num:.2f}")

        if r.get("intent"):
            res = rt.handle_intent({"intent": r["intent"]},
                                   mood={"energy": energy, "mood": mood_num})
            if res["entry"]:
                side = (res["entry"].metrics or {}).get("side")
                M = rf.compute(pose_gen.build(res["play_P"], rf, bm, active_side=side))
                h = M["rightHand"][0:3, 3]
                print(f"    动作：{r['intent']}  →  {res['source']}  右手 z={h[2]:.3f}")
            else:
                gap = res.get("capability_gap") or {}
                print(f"    动作：{r['intent']}  →  ❌ {gap.get('kind')}")
                gaps.append({"turn": i, "intent": r["intent"],
                             "kind": gap.get("kind"),
                             "suggested": str(gap.get("suggested"))[:60]})
        else:
            # 她说的话里可能有动作，但解析器判 null（族里没有）
            guess = _guess_action(r["say"])
            if guess:
                print(f"    动作：（她说了「{guess}」但没有对应族）")
                gaps.append({"turn": i, "intent": None, "kind": "no_family",
                             "suggested": guess})
            else:
                print("    动作：（纯说话）")

        history.append({"role": "user", "text": line})
        history.append({"role": "pet", "text": r["say"]})
        print(f"    [{ms:.0f}ms]")

    print("\n" + "=" * 66)
    print(f"库：{len(rt.library.entries)} 条  "
          f"状态 {set(e.status for e in rt.library.entries.values())}")
    for e in rt.library.entries.values():
        print(f"  {e.id}  {e.family:10s}  hits={e.metrics.get('hits', 0)}  "
              f"preds={[p['name'] for p in (e.predicates or [])][:4]}")

    print(f"\n能力缺口 {len(gaps)} 条：")
    for g in gaps:
        print(f"  第{g['turn']}轮  {g['kind']:16s}  {g['suggested']}")

    print("\n族生长提议：")
    props = propose(rt.library, min_count=2, only_family="free")
    if not props:
        print("  （还没有达到 min_count=2 的 free 组合）")
    for p in props:
        print(f"  → {p['name']}  出现 {p['count']} 次")
    return 0


def _guess_action(say: str) -> str:
    """从她的话里找可能的动作（只用于统计，不用于执行）。"""
    for kw in ("靠", "挪", "凑近", "探出", "蹲", "站起", "甩尾", "垂下",
               "歪头", "点头", "摇头", "眨眼", "抱", "趴"):
        if kw in say:
            return kw
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
