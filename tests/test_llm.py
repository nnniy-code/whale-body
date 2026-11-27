"""tests/test_llm.py — LLM 接入验收（stub，不需要 API key）。

怎么测：python tests/test_llm.py
预期现象：[llm] ALL PASS
调参位置：llm/interpret.py 的 prompt 与校验
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.interpret import (  # noqa: E402
    build_system_prompt, extract_json, interpret, validate_act,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    anch = json.load(open(os.path.join(ROOT, "config", "anchors.json"), encoding="utf-8"))
    caps = {
        "families": sorted(fams.keys()),
        "predicates": ["hand_above_head", "head_tilt", "hand_near_anchor"],
        "anchors": sorted(anch.get("points", {}).keys()),
        "ranges": {},
    }

    print("[1] prompt 内容")
    sp = build_system_prompt(caps)
    check("含族名", "wave" in sp and "walk" in sp)
    check("含谓词", "hand_above_head" in sp)
    check("含锚点", "mouth" in sp)
    check("含硬规则", "不要给肩" in sp or "角度" in sp)

    print("[2] JSON 抽取")
    check("纯 JSON", extract_json('{"a":1}') == {"a": 1})
    check("```json 包裹", extract_json('```json\n{"a":2}\n```') == {"a": 2})
    check("前后废话", extract_json('好的，如下：{"a":3} 完毕') == {"a": 3})

    print("[3] 合法输出")
    good = json.dumps({"say": "好呀", "act": {
        "intent": "wave", "predicates": [
            {"name": "hand_above_head", "args": {"side": "right"}}]}})
    r = interpret("挥手", lambda s, u: good, caps)
    check("ok", r["ok"], str(r["error"]))
    check("say 提取", r["say"] == "好呀")
    check("一次通过", r["attempts"] == 1)

    print("[4] 未知族")
    bad = json.dumps({"say": "x", "act": {"intent": "backflip", "predicates": [
        {"name": "hand_above_head", "args": {"side": "right"}}]}})
    r = interpret("翻跟头", lambda s, u: bad, caps, retries=0)
    check("拒绝", not r["ok"])
    check("原因提到族", "未知族" in (r["error"] or ""), str(r["error"]))

    print("[5] 未知谓词")
    bad2 = json.dumps({"say": "x", "act": {"intent": "wave", "predicates": [
        {"name": "fly_to_moon", "args": {}}]}})
    r = interpret("飞", lambda s, u: bad2, caps, retries=0)
    check("拒绝", not r["ok"])
    check("原因提到谓词", "未知谓词" in (r["error"] or ""), str(r["error"]))

    print("[6] 多余参数")
    bad3 = json.dumps({"say": "x", "act": {"intent": "wave", "predicates": [
        {"name": "hand_above_head", "args": {"side": "right", "extra": 1}}]}})
    r = interpret("x", lambda s, u: bad3, caps, retries=0)
    check("拒绝多余参数", not r["ok"], str(r["error"]))

    print("[7] 做不到 → act: null")
    none_obj = json.dumps({"say": "这个我做不到", "act": None})
    r = interpret("翻跟头", lambda s, u: none_obj, caps)
    check("ok（允许做不到）", r["ok"])
    check("有 say", r["say"] == "这个我做不到")

    print("[8] 坏 JSON → 重试")
    calls = {"n": 0}

    def flaky(system, user):
        calls["n"] += 1
        return "这不是 JSON" if calls["n"] == 1 else good

    r = interpret("挥手", flaky, caps, retries=2)
    check("重试后成功", r["ok"], str(r["error"]))
    check("用了 2 次", r["attempts"] == 2, str(r["attempts"]))

    print("[9] 一直坏 → 降级")
    r = interpret("x", lambda s, u: "不是 JSON", caps, retries=1)
    check("失败", not r["ok"])
    check("有错误原因", bool(r["error"]))

    print("[10] 调用异常")
    def boom(system, user):
        raise RuntimeError("网络断了")
    r = interpret("x", boom, caps, retries=1)
    check("不崩", not r["ok"])
    check("原因含调用失败", "调用失败" in (r["error"] or ""), str(r["error"]))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
