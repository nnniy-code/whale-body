"""tests/test_world.py — 写回沙箱世界线（v1.9）。

设计依据：`sandbox/README.md` 隔离规则、`llm/paths.assert_writable`

**只写 sandbox/**，写正式项目直接抛 PermissionError。

怎么测：python tests/test_world.py
预期现象：[world] ALL PASS
调参位置：llm/world.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm import paths  # noqa: E402
from llm.persona import load_memory  # noqa: E402
from llm.world import (append_chat, append_memory, persist_turn,  # noqa: E402
                       update_mood)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    tmp = os.path.join(paths.SANDBOX, "_tmp_world_test")
    if os.path.exists(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp)

    try:
        print("[1] 聊天日志追加两行")
        n = append_chat(tmp, "在吗", "在。")
        check("写 2 行", n == 2, str(n))
        lines = open(os.path.join(tmp, "chat.jsonl"), encoding="utf-8").read().splitlines()
        check("落盘 2 行", len(lines) == 2, str(len(lines)))
        rec = json.loads(lines[0])
        check("字段齐", set(rec) == {"ts", "role", "text"}, str(sorted(rec)))
        check("第一行是 user", rec["role"] == "user", rec["role"])

        print("[2] 情绪增量并进 mood.json 且封顶")
        m = update_mood(tmp, {"energy": -8}, note="她累了")
        check("energy 从 50 降", m["energy"] == 42, str(m["energy"]))
        check("note 写入", m["note"] == "她累了", m.get("note"))
        update_mood(tmp, {"energy": -999})
        m2 = json.load(open(os.path.join(tmp, "mood.json"), encoding="utf-8"))
        check("下限 0", m2["energy"] == 0, str(m2["energy"]))
        update_mood(tmp, {"energy": 999})
        m3 = json.load(open(os.path.join(tmp, "mood.json"), encoding="utf-8"))
        check("上限 100", m3["energy"] == 100, str(m3["energy"]))

        print("[3] 记忆追加 + 去重")
        a = append_memory(tmp, "主人喜欢在深夜聊天")
        b = append_memory(tmp, "主人喜欢在深夜聊天")
        check("第一次写入", a != "", a)
        check("第二次去重", b == "", repr(b))
        txt = open(os.path.join(tmp, "memory.md"), encoding="utf-8").read()
        check("只出现一次", txt.count("深夜聊天") == 1, str(txt.count("深夜聊天")))

        print("[4] persist_turn 一次写全")
        rep = persist_turn(tmp, "晚安", "嗯，睡吧。", intent="rest",
                           mood_delta={"energy": -5}, memory_line="他会说晚安")
        check("回执有 chat_lines", rep["chat_lines"] == 2, str(rep["chat_lines"]))
        check("回执有 mood", "mood" in rep)
        check("回执有 memory", rep["memory"] != "")
        check("chat 变 4 行",
              len(open(os.path.join(tmp, "chat.jsonl"), encoding="utf-8").read().splitlines()) == 4)

        print("[5] 记忆读的是尾部（新写的能被读到）")
        mem = load_memory(tmp)
        check("能读到最新一条", "他会说晚安" in mem, mem[-40:].replace("\n", " "))

        print("[6] 结构化记忆 memory.json（正版的主体）")
        items = [{"text": "弱的一条", "strength": 0.1, "hits": 1},
                 {"text": "强的一条", "strength": 0.9, "hits": 5},
                 {"text": "中的一条", "strength": 0.5, "hits": 2}]
        with open(os.path.join(tmp, "memory.json"), "w", encoding="utf-8") as f:
            json.dump({"items": items}, f, ensure_ascii=False)
        mem2 = load_memory(tmp, world=tmp)
        check("读到了 items", "强的一条" in mem2, mem2[-60:].replace("\n", " "))
        check("强的排前面", mem2.index("强的一条") < mem2.index("弱的一条"))
        check("仍有 memory.md 的内容", "他会说晚安" in mem2)

        print("[7] 越界写入被拒")
        for bad in (os.path.join(paths.PRODUCTION_WORLD, "mood.json"),
                    os.path.join(ROOT, "core", "mood.json")):
            try:
                update_mood(os.path.dirname(bad), {"energy": 1})
                check(f"拒绝 {bad}", False, "没抛")
            except PermissionError:
                check("拒绝越界写入", True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
