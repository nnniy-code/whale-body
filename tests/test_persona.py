"""tests/test_persona.py — 人格资源与 act_from_say 验收。

注意：`turn()` 在有身体时已改为 **combined 一次调用**（见 test_combined.py）。
本文件只测：沙箱资源加载、身体检测、act_from_say 的独立行为。

怎么测：python tests/test_persona.py
预期现象：[persona] ALL PASS
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.persona import (  # noqa: E402
    act_from_say, body_connected, build_act_system, chat_once, load_memory,
    load_mood, load_persona,
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
        "families": ["wave", "walk", "pat_head", "idle"],
        "predicates": ["hand_above_head", "head_tilt", "hand_near_anchor"],
        "anchors": ["mouth", "head_top", "hand.R"],
    }

    print("[1] 沙箱资源（真数据）")
    persona = load_persona(SANDBOX)
    memory = load_memory(SANDBOX)
    mood = load_mood(SANDBOX)
    check("人格卡非空", len(persona) > 500, f"{len(persona)} 字")
    check("含名字", "大肥鱼" in persona)
    check("含脾气", "脾气" in persona)
    check("记忆非空", len(memory) > 50, f"{len(memory)} 字")
    check("心情可读", bool(mood), str(list(mood)[:4]))

    print("[2] 身体连接检测")
    check("不存在的目录 → False", body_connected(os.path.join(ROOT, "nope")) is False)

    print("[3] chat_once（纯说话）")
    r = chat_once("今天下雨了", lambda s, u: "嗯，那我不出门了。", persona, memory, mood)
    check("有 say", "不出门" in r["say"])
    check("raw 保留", r["raw"] == r["say"])

    print("[4] act_from_say：带上下文")
    seen = {}

    def llm(system, user):
        seen["system"] = system
        seen["user"] = user
        return json.dumps({"act": {"intent": "wave", "predicates": [
            {"name": "hand_above_head", "args": {"side": "right"}}]}})

    act = act_from_say("好呀挥挥手", llm, caps, user_text="挥挥手",
                       mood={"mood": 0.6}, scene="桌面上")
    check("解出 act", act is not None and act["intent"] == "wave")
    check("user 含用户原话", "用户说" in seen["user"] and "挥挥手" in seen["user"])
    check("user 含心情", "心情" in seen["user"])
    check("user 含场景", "场景" in seen["user"])

    print("[5] act_from_say：编造族 → 丢弃")
    act = act_from_say("翻跟头", lambda s, u: json.dumps(
        {"act": {"intent": "backflip", "predicates": [{"name": "hand_above_head", "args": {}}]}}),
        caps)
    check("丢弃", act is None)

    print("[6] act_from_say：崩了 → None")
    def boom(s, u):
        raise RuntimeError("超时")
    check("不崩", act_from_say("x", boom, caps) is None)

    print("[7] act_from_say：坏 JSON → None")
    check("不崩", act_from_say("x", lambda s, u: "不是JSON", caps) is None)

    print("[8] act prompt 白名单")
    sp = build_act_system(caps)
    check("含族", "wave" in sp)
    check("含谓词", "hand_above_head" in sp)
    check("含锚点", "mouth" in sp)
    check("有「宁可不做」", "拿不准" in sp)

    print("[9] act 为 null 的正常路径")
    check("None", act_from_say("嗯", lambda s, u: '{"act": null}', caps) is None)

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
