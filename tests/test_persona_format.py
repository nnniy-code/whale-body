"""tests/test_persona_format.py — 人格 prompt 的拼装（v1.9）。

两个真实踩到的坑：

  1. `persona.md` 里**没有**「只发说出口的话、不写括号神态」这条，
     它在 `persona.json` 的 `speech.medium` 里。以前只读 `.md`，
     于是她真的会输出「（抬起手）哟」这种旁白。
  2. 修好 1 之后她仍然写括号——因为 `build_chat_system` 自己结尾又加了一句
     **「（就是跟他说话。别的不用管。）」**，既示范了括号，又说「别的不用管」，
     把上面的格式规则顶掉了。

怎么测：python tests/test_persona_format.py
预期现象：[persona_format] ALL PASS
调参位置：llm/persona.py load_persona / build_chat_system
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.persona import build_chat_system, build_act_system, load_persona  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX = os.path.join(ROOT, "sandbox")
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    pj = json.load(open(os.path.join(SANDBOX, "persona.json"), encoding="utf-8"))
    medium = (pj.get("speech") or {}).get("medium", "")

    print("[1] 人格文本要包含 persona.json 的格式硬要求")
    text = load_persona()
    check("persona.json 里确实有 medium", bool(medium))
    check("merged 人格包含 medium 首句", medium.strip()[:12] in text,
          medium.strip()[:12])
    check("包含「不写动作」", "不写动作" in text)
    check("包含口头禅", "口头禅" in text)
    check("包含 md 自己的内容（脾气）", "脾气" in text)
    check("人格不是空字符串", len(text) > 500, f"{len(text)} 字")

    print("[2] 对话 system 的结尾不能自相矛盾")
    sysmsg = build_chat_system(text, "【记忆】略", {"energy": 50})
    tail = sysmsg[-40:]
    check("结尾不含「别的不用管」", "别的不用管" not in sysmsg, tail)
    check("结尾没有括号示范", "（" not in tail, tail)
    check("仍然包含格式硬要求", "不写动作" in sysmsg)
    check("记忆被拼进去了", "【记忆】" in sysmsg)

    print("[3] 动作解析的 system 不带人格（第二次调用是纯功能）")
    caps = {"families": ["wave", "walk"], "predicates": ["hand_above_head"],
            "anchors": ["head_top"]}
    act_sys = build_act_system(caps)
    check("解析 prompt 不含人格口头禅", "口头禅" not in act_sys)
    check("解析 prompt 要求 JSON", "JSON" in act_sys.upper())

    print("[4] 输出侧兜底：舞台描写必须被剔掉（prompt 不可靠）")
    from llm.persona import strip_stage_directions as strip
    cases = [
        ("（抬手，冲屏幕这边晃了晃）这儿呢。", "这儿呢。"),
        ("{action: wave} 喏，挥好了 🐋", "喏，挥好了 🐋"),
        ("【歪头】你说呢", "你说呢"),
        ("嗯 在呢\n\n（你这时候才想起我啊）", "嗯 在呢"),
        ("（看着你）", "看着你"),
    ]
    for raw, want in cases:
        got = strip(raw)
        check(f"剔除 -> {got!r}", got == want, f"raw={raw!r}")
    check("正常句子不动", strip("我困了，先睡。") == "我困了，先睡。")
    check("空串不炸", strip("") == "")

    print("[5] 沙箱是唯一来源（不读正式项目）")
    check("SANDBOX 在项目内", SANDBOX.startswith(ROOT + os.sep), SANDBOX)

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
