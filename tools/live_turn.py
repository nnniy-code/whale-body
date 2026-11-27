"""tools/live_turn.py — 真 API 跑一轮：她说话 → 解析动作 → 求解 → 姿态。

用的是**沙箱人格**（`sandbox/`）+ 正式项目的 **只读 API key**。
不会写任何 world 文件，也不碰 `dsh-qq` 的状态。

跑法（纯 Python，只打印）：

    python tools/live_turn.py

跑法（在 Blender 里，把人摆出来并出图）：

    blender_rt_headless(file=<blend>, script_file=tools/live_turn.py,
                        args="--apply 1", shots=[...])

参数（`--` 之后）：
    --text  "..."     只跑这一句（默认跑内置两句）
    --apply 1|0       有 bpy 时是否写进骨骼（默认 1）

判据：
  - 两阶段：say 有内容、intent 是合法族名（或 null）
  - 求解：entry 非空，source=L2 或 L1
  - 姿态：手/头有实际位移，IK 误差 0
"""

import json
import os
import sys
import time

ROOT = r"D:\DSproject\body-v2"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    import bpy  # noqa: F401
    HAS_BPY = True
except Exception:
    HAS_BPY = False

import numpy as np  # noqa: E402

from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402
from llm.client import DeepSeek, find_api_key, redact  # noqa: E402
from llm.persona import load_memory, load_mood, load_persona, turn  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]
LINES = ["挥挥手跟我打个招呼", "今天有点累，伸个懒腰吧"]


def arg(name, default):
    if "--" not in sys.argv:
        return default
    a = sys.argv[sys.argv.index("--") + 1:]
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default


def build_runtime():
    rigd = json.load(open(os.path.join(ROOT, "tests/fixtures/rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    rf = rig.subset(ONLY)
    bm = BoneMap.load(os.path.join(ROOT, "config/bones_map.json"))
    anch = json.load(open(os.path.join(ROOT, "config/anchors.json"), encoding="utf-8"))
    fams = json.load(open(os.path.join(ROOT, "config/seed_families.json"), encoding="utf-8"))["families"]
    caps = json.load(open(os.path.join(ROOT, "config/capsules.json"), encoding="utf-8"))["bones"]
    tab = load_table()
    bmj = json.load(open(os.path.join(ROOT, "config/bones_map.json"), encoding="utf-8"))["bones"]
    L = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, L, chain_skip=3, only=ONLY)
    rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps, collision_table=tab,
                 lengths=L, capsule_baseline=base, capsule_only=ONLY)
    return rt, rig


def main():
    key = find_api_key()
    if not key:
        print("HEADLESS " + json.dumps({"ok": False, "error": "没有可用的 API key"}, ensure_ascii=False))
        return

    chat = DeepSeek(key, temperature=1.15, max_tokens=400, timeout_s=60)
    parser = DeepSeek(key, temperature=0.1, max_tokens=300, timeout_s=60)

    rt, rig = build_runtime()
    caps = rt.capabilities()
    persona = load_persona()
    memory = load_memory()
    mood = load_mood()

    text_arg = arg("--text", "")
    lines = [text_arg] if text_arg else LINES
    apply_ = arg("--apply", "1") == "1" and HAS_BPY
    arm = None
    if apply_:
        arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
        arm = max(arms, key=lambda o: len(o.pose.bones)) if arms else None

    out = {"ok": True, "has_bpy": HAS_BPY, "turns": []}
    for text in lines:
        t0 = time.perf_counter()
        try:
            r = turn(text, chat, caps, persona, memory, mood, body=True, parser_llm=parser)
        except Exception as e:
            out["ok"] = False
            out["error"] = redact(str(e))[:300]
            break
        ms = round((time.perf_counter() - t0) * 1000, 0)
        rec = {"ask": text, "say": r.get("say"), "intent": r.get("intent"),
               "intents": r.get("intents") or [],
               "mood_delta": r.get("mood_delta"), "calls": r.get("calls"), "ms": ms}

        intent = r.get("intent")
        from llm.persona import acts_from_turn
        acts = acts_from_turn(r)
        if acts:
            act = dict(acts[0])
            act["say"] = r.get("say")
            act["mood"] = r.get("mood_delta") or mood
            res = rt.handle_intent(act)
            rec["source"] = res.get("source")
            rec["gap"] = (res.get("capability_gap") or {}).get("kind")
            if res.get("entry"):
                e = res["entry"]
                side = (e.metrics or {}).get("side")
                P = (res.get("play_P") or e.params.get("P", {}))
                basis, diag = rt.build_full(P, e.family, rt.t, active_side=side)
                M = rig.compute(basis)
                rec["hand_z"] = round(float(M["rightHand"][2, 3]), 3)
                rec["head_z"] = round(float(M["head"][2, 3]), 3)
                rec["ik_err_mm"] = (max(diag["err_mm"].values())
                                    if diag.get("err_mm") else None)
                if apply_ and arm is not None:
                    from core.binder import apply_basis
                    apply_basis(arm, basis)
                    bpy.context.view_layer.update()
                    rec["applied"] = True
        out["turns"].append(rec)
        print(f"[turn] {text} -> say={rec['say']!r} intent={intent} "
              f"source={rec.get('source')} {ms}ms", flush=True)

    out["api_calls"] = chat.calls + parser.calls
    print("HEADLESS " + json.dumps(out, ensure_ascii=False))


main()
