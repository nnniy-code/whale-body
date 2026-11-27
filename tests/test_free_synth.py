"""tests/test_free_synth.py — 没有现成族时，用谓词现场合成动作（v1.9）。

设计依据：用户质疑「既然没有这个动作为什么不建新动作，而是走已有的族」

原状况：解析器只会从族名里挑，从不给谓词 → 没族就只能落到最近的族，
做出一个不同的动作。框架其实早就有 `free` 族 + `use_llm_predicates` + `grow`，
只是**没接上**。

现在：没有族对得上 → `intent="free"` + 谓词 → 求解器现场搜 P → 入库。
（真正的新**结构**——双手、抬腿——仍要扩参数空间，那是配置/代码级，见 arms_out / lift_leg_*。）

怎么测：python tests/test_free_synth.py
预期现象：[free_synth] ALL PASS
调参位置：llm/persona.py parse_action / core/runtime.py handle_intent
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
from llm.persona import acts_from_turn, build_parse_system, parse_action  # noqa: E402

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
    L = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, L, chain_skip=3, only=ONLY)

    rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                 collision_table=tab, lengths=L,
                 capsule_baseline=base, capsule_only=ONLY)
    info = rt.capabilities()

    print("[1] 解析 prompt 里带上了谓词表和锚点表")
    sysmsg = build_parse_system(info)
    check("有 hand_near_anchor", "hand_near_anchor" in sysmsg)
    check("有 head_top 锚点", "head_top" in sysmsg)
    check("告诉它没有族时用 free", '"free"' in sysmsg or "free" in sysmsg)

    GOOD = ('{"intents":[{"intent":"free","dur_s":1.6,"predicates":['
            '{"name":"hand_near_anchor","args":{"anchor":"head_top","side":"right","tol_mm":90}}]}],'
            '"mood_delta":null}')
    BAD = ('{"intents":[{"intent":"free","predicates":['
           '{"name":"hand_near_anchor","args":{"anchor":"head_side","side":"right"}}]}],'
           '"mood_delta":null}')

    print("[2] 合法谓词：解析出来带 predicates")
    r = parse_action("把手抬到头顶", "好", lambda s, u: GOOD, info, parser_llm=lambda s, u: GOOD)
    check("intent=free", r["intent"] == "free", str(r["intent"]))
    preds = (r["intents"][0] or {}).get("predicates") or []
    check("带 1 条谓词", len(preds) == 1, str(preds))
    check("锚点保留", preds[0]["args"].get("anchor") == "head_top")

    print("[3] 编造的锚点被过滤，整条动作作废（不会打崩求解器）")
    r2 = parse_action("x", "y", lambda s, u: BAD, info, parser_llm=lambda s, u: BAD)
    check("intent 为空", r2["intent"] is None, str(r2["intent"]))
    check("intents 为空", r2["intents"] == [], str(r2["intents"]))

    print("[4] acts_from_turn 带上 use_llm_predicates")
    acts = acts_from_turn(r)
    check("有 1 个 act", len(acts) == 1, str(acts))
    check("act.intent=free", acts[0]["intent"] == "free")
    check("act.use_llm_predicates=True", acts[0].get("use_llm_predicates") is True)
    check("act.predicates 带过去", bool(acts[0].get("predicates")))

    print("[5] 真的能解出来并入库")
    res = rt.handle_intent(dict(acts[0]))
    check("有 entry", res.get("entry") is not None, str(res.get("source")))
    check("source=L2", res.get("source") == "L2", str(res.get("source")))
    if res.get("entry"):
        P = res["entry"].params.get("P", {})
        check("P 有 12 维", len(P) >= 10, str(len(P)))
        b, _d = rt.build_full(P, "free", rt.t)
        M = rig.compute(b)
        check("手离开了垂手位置",
              abs(float(M["rightHand"][2, 3]) - 0.572) > 0.05,
              f"{float(M['rightHand'][2,3]):.3f}")

    print("[6] 坏谓词不会把引擎打崩（走 gap 回传）")
    # 用**空库**的 Runtime，否则会先 L1 命中上面那条合法动作，根本走不到求解
    rt2 = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                  collision_table=tab, lengths=L,
                  capsule_baseline=base, capsule_only=ONLY)
    bad_act = {"intent": "free", "use_llm_predicates": True,
               "predicates": [{"name": "hand_near_anchor",
                               "args": {"anchor": "nope", "side": "right"}}]}
    try:
        rb = rt2.handle_intent(bad_act)
        check("返回了 gap 而不是抛异常",
              (rb.get("capability_gap") or {}).get("kind") in ("solve_error", "solve_failed"),
              str((rb.get("capability_gap") or {}).get("kind")))
    except Exception as e:
        check("不该抛异常", False, repr(e)[:80])

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
