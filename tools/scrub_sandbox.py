"""tools/scrub_sandbox.py — 清空沙箱里的私人内容，保留文件结构和格式。

怎么测：python tools/scrub_sandbox.py
产物：sandbox/ 下的 diary/self/memory 变成空模板（带格式说明）
"""

from __future__ import annotations

import json
import os

ROOT = r"D:\DSproject\body-v2\sandbox"


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"  cleared {os.path.relpath(path, ROOT)}")


def main() -> int:
    # ---- memory.json：保留结构，清空内容 ----
    write(os.path.join(ROOT, "memory.json"), json.dumps({
        "summary": "",
        "notes": [],
        "chats": [],
        "mood": 0.5,
        "_schema": {
            "summary": "长期印象（一段话，≤300 字）",
            "notes": ["零散要点，每条一行字符串"],
            "chats": [{"role": "user|pet", "text": "内容", "at": "毫秒时间戳"}],
            "mood": "0.0 ~ 1.0",
        },
    }, ensure_ascii=False, indent=2) + "\n")

    # ---- memory.md：保留标题和格式 ----
    write(os.path.join(ROOT, "memory.md"), """# 记忆

> 这个文件是角色的**长期记忆**。格式示例，内容请自己填。
> 清空后它就是一个空记忆——程序会从头开始积累。

## 我对主人的印象

（一段话，写到 300 字以内。没有就留空。）

## 我记住的事

- （一条一行，记具体的：喜好、称呼、约定、重要的事）

## 最近的对话

| 时间 | 谁 | 说了什么 |
|---|---|---|
| — | — | （对话记录，程序会自动追加） |
""")

    # ---- diary/：只留模板 ----
    ddir = os.path.join(ROOT, "diary")
    if os.path.isdir(ddir):
        for f in os.listdir(ddir):
            if f.endswith(".md"):
                os.remove(os.path.join(ddir, f))
    write(os.path.join(ROOT, "diary", "_TEMPLATE.md"), """# YYYY-MM-DD

> 这是日记模板。每天一个文件，命名 `YYYY-MM-DD.md`。
> 内容由角色自己写，不需要固定格式。

## 今天发生了什么

## 我在想什么

## 明天
""")

    # ---- self/：只留模板 ----
    sdir = os.path.join(ROOT, "self")
    if os.path.isdir(sdir):
        for f in os.listdir(sdir):
            if f.endswith(".md"):
                os.remove(os.path.join(sdir, f))
    write(os.path.join(ROOT, "self", "_TEMPLATE.md"), """# YYYY-MM-DD

> 这是「自我记录」模板。和日记不同，这里写的是**她对自己的认识**。
> 每天一个文件，命名 `YYYY-MM-DD.md`。

## 我今天觉得自己是什么样的人

## 我改了什么想法
""")

    # ---- prod_sim/world/：清记忆和对话 ----
    pw = os.path.join(ROOT, "prod_sim", "world")
    if os.path.isdir(pw):
        write(os.path.join(pw, "memory.json"), json.dumps({
            "summary": "", "notes": [], "chats": [], "mood": 0.5,
            "_note": "生产世界线的记忆副本，已清空。格式同 sandbox/memory.json",
        }, ensure_ascii=False, indent=2) + "\n")

    # ---- prod_sim 的 memory.md ----
    pmm = os.path.join(ROOT, "prod_sim", "memory.md")
    if os.path.exists(pmm):
        write(pmm, "# 记忆（生产副本）\n\n（已清空，格式同 sandbox/memory.md）\n")

    # ---- sandbox/README.md：说明每个文件干什么 ----
    write(os.path.join(ROOT, "README.md"), """# 沙箱：角色数据

这里放**角色的数据**，用于开发和测试。所有文件都是**空模板**（除人格卡）。

## 文件说明

| 文件 | 是什么 | 格式 |
|---|---|---|
| `persona.md` | **人格卡**（角色的性格/说话方式） | Markdown，自由格式 |
| `persona.json` | 人格的结构化版本 | JSON |
| `memory.md` | 长期记忆（给 LLM 看的自然语言） | Markdown，见文件内模板 |
| `memory.json` | 记忆的结构化版本 | `{summary, notes, chats, mood}` |
| `self.md` | 自我认知（她觉得自己是谁） | Markdown |
| `self/*.md` | 每日自我记录 | `YYYY-MM-DD.md` |
| `diary/*.md` | 每日日记 | `YYYY-MM-DD.md` |
| `mood.json` | 当前心情 | JSON |
| `emotion.json` | 情绪维度 | JSON |
| `purpose.json` / `plan.json` | 目的 / 计划 | JSON |
| `presence.json` | 在不在（是否被看着） | JSON |
| `flags.json` | 功能开关 | JSON |
| `body.json` / `pose.json` / `receipt.json` | 身体接口 | JSON |
| `body_lib.json` | 身体动作库 | JSON |

## 怎么用

1. 改 `persona.md` 定义你的角色
2. 其余文件保持空模板即可，程序会自己填
3. `README.md`（自带 schema）说明每个字段的含义

## 隐私

**这个目录里不该放真实角色的私人内容**（日记、真实对话、记忆）。
如果要公开仓库，保持空模板。
""")

    print("\nSCRUBBED. 剩下的都是空模板 + 人格卡。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
