"""tests/test_two_stage.py — 两阶段（她自由说话 + 解析器判动作）。

怎么测：python tests/test_two_stage.py
预期现象：[two-stage] ALL PASS
调参位置：llm/persona.py 的 PARSE_SYSTEM
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.persona import (  # noqa: E402
    build_parse_system, load_memory, load_mood, load_persona, parse_action,
    turn_two_stage,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX = os.path.join(ROOT, "sandbox")
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    caps = {
        "families": ["wave", "walk", "idle", "stretch", "pat_head"],
        "predicates": ["hand_above_head"],
        "anchors": ["mouth"],
    }
    persona = load_persona(SANDBOX)
    memory = load_memory(SANDBOX)
    mood = load_mood(SANDBOX)

    print("[1] 说话阶段没有任何格式要求")
    sys_prompt = None

    def llm(system, user):
        nonlocal sys_prompt
        if sys_prompt is None:
            sys_prompt = system
            return "来了来了。"
        return json.dumps({"intent": "wave", "mood_delta": {"mood": 0.1}})

    r = turn_two_stage("挥挥手", llm, caps, persona, memory, mood)
    check("第一阶段 prompt 不含 <<ACT>>", "<<ACT>>" not in (sys_prompt or ""))
    check("第一阶段 prompt 不含 JSON 字样", "JSON" not in (sys_prompt or ""))
    check("第一阶段含人格", "不是客服" in (sys_prompt or "") or "大肥鱼" in (sys_prompt or ""))

    print("[2] 两阶段结果")
    check("say 有", r["say"] == "来了来了。")
    check("intent 解出", r["intent"] == "wave", str(r["intent"]))
    check("mood_delta 解出", r["mood_delta"] == {"mood": 0.1}, str(r["mood_delta"]))
    check("calls=2", r["calls"] == 2)

    print("[3] 解析器：没动作 → null")
    def llm_null(system, user):
        if "动作解析器" in system:
            return '{"intent": null, "mood_delta": {"energy": -10}}'
        return "累死了，不想动。"
    r2 = turn_two_stage("今天好累", llm_null, caps, persona, memory, mood)
    check("intent 为 None", r2["intent"] is None)
    check("energy 掉", r2["mood_delta"] == {"energy": -10}, str(r2["mood_delta"]))

    print("[4] 解析器：编造族 → 丢弃")
    def llm_bad(system, user):
        if "动作解析器" in system:
            return '{"intent": "backflip"}'
        return "翻个跟头。"
    r3 = turn_two_stage("翻跟头", llm_bad, caps, persona, memory, mood)
    check("非法族被丢", r3["intent"] is None)

    print("[5] 解析器：坏 JSON → 不影响说话")
    def llm_broken(system, user):
        if "动作解析器" in system:
            return "这不是 JSON"
        return "嗯。"
    r4 = turn_two_stage("嗯", llm_broken, caps, persona, memory, mood)
    check("say 保住", r4["say"] == "嗯。")
    check("intent None", r4["intent"] is None)
    check("有 parse_error", bool(r4["parse_error"]))

    print("[6] 解析器崩了 → 不影响说话")
    def llm_boom(system, user):
        if "动作解析器" in system:
            raise RuntimeError("超时")
        return "算了。"
    r5 = turn_two_stage("x", llm_boom, caps, persona, memory, mood)
    check("say 保住", r5["say"] == "算了。")
    check("intent None", r5["intent"] is None)

    print("[7] mood_delta 被夹住")
    def llm_huge(system, user):
        if "动作解析器" in system:
            return json.dumps({"intent": None, "mood_delta": {"energy": -999, "mood": 5.0}})
        return "x"
    r6 = turn_two_stage("x", llm_huge, caps, persona, memory, mood)
    check("energy 夹到 -20", r6["mood_delta"]["energy"] == -20.0, str(r6["mood_delta"]))
    check("mood 夹到 0.3", r6["mood_delta"]["mood"] == 0.3, str(r6["mood_delta"]))

    print("[8] 解析器 prompt 含族名")
    sp = build_parse_system(caps)
    check("含 wave", "wave" in sp)
    check("有「言行一致」说明", "言行一致" in sp)
    check("有「不要编」", "不要编" in sp)

    print("[9] 解析器不带人格")
    check("parse prompt 无人格", "不是客服" not in build_parse_system(caps))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
