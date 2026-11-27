"""llm/world.py — 把一轮对话写回**沙箱**世界线（v1.9）。

**只写 `sandbox/`**，永远不碰正式项目（带 Live2D / 语音那一套）的 world 目录。
所有写入都过 `llm.paths.assert_writable()`，越界直接抛。

写三样：

| 文件 | 写什么 | 为什么 |
|---|---|---|
| `chat.jsonl` | 每轮 `{ts, role, text}` 两行 | 原始日志，以后重新提炼记忆的唯一依据 |
| `mood.json` | `energy` / `mood` / `note` / `updated` | 下一轮 `load_mood()` 读到的就是它 |
| `memory.md` | **可选**，一行一句 | 默认不写（见下） |

**为什么不自动写 memory.md**：一段闲聊里模型会自己编出「主人昨天说……」这种
不存在的事实，写进记忆就会被下一轮当真。记忆要么人工确认，要么由单独的
提炼步骤（读 chat.jsonl → 生成候选 → 人工/规则过滤）产出。
`allow_memory=True` 时只写**双方都说过**的短句，不生成新事实。
"""

from __future__ import annotations

import json
import os
import time

from llm.paths import assert_writable

ENERGY_MIN, ENERGY_MAX = 0.0, 100.0


def _now_iso() -> str:
    # 到秒：分钟精度下「同一分钟内连说两句」根本看不出 mood 更新过
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def append_chat(sandbox: str, user_text: str, say: str,
                intent: str | None = None) -> int:
    """追加两行（user / bot）。返回写了几行。"""
    p = assert_writable(os.path.join(sandbox, "chat.jsonl"))
    ts = time.time()
    rows = [
        {"ts": ts, "role": "user", "text": user_text},
        {"ts": ts, "role": "bot", "text": say},
    ]
    with open(p, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def update_mood(sandbox: str, mood_delta: dict | None = None,
                mood_name: str | None = None, note: str | None = None) -> dict:
    """把情绪增量并进 mood.json。返回写后的内容。"""
    p = assert_writable(os.path.join(sandbox, "mood.json"))
    cur = {}
    if os.path.exists(p):
        try:
            cur = json.load(open(p, encoding="utf-8"))
        except Exception:
            cur = {}
    d = dict(mood_delta or {})
    if "energy" in d:
        e = float(cur.get("energy", 50)) + float(d["energy"])
        cur["energy"] = int(max(ENERGY_MIN, min(ENERGY_MAX, round(e))))
    if "mood" in d and not mood_name:
        # mood_delta 里如果是数值，不覆盖文字心境；文字才覆盖
        mv = d["mood"]
        if isinstance(mv, str) and mv.strip():
            cur["mood"] = mv.strip()[:20]
    if mood_name:
        cur["mood"] = str(mood_name)[:20]
    if note:
        cur["note"] = str(note)[:200]
    cur["updated"] = _now_iso()
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=1)
    return cur


def append_memory(sandbox: str, line: str) -> str:
    """追加一行记忆。**会去重**（同一句不重复写）。"""
    line = (line or "").strip()
    if not line:
        return ""
    p = assert_writable(os.path.join(sandbox, "memory.md"))
    if os.path.exists(p):
        old = open(p, encoding="utf-8", errors="ignore").read()
        if line in old:
            return ""
    with open(p, "a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")
    return line


def persist_turn(sandbox: str, user_text: str, say: str,
                 intent: str | None = None,
                 mood_delta: dict | None = None,
                 mood_name: str | None = None,
                 memory_line: str | None = None,
                 world: str | None = None) -> dict:
    """一轮的全量写回。返回回执。

    `sandbox` 是**人格/记忆**那一半；`world` 是**情绪/日志**那一半。
    正版把这两半拆开，所以两个参数分开传；省略 world 就是同一个目录。
    """
    w = world or sandbox
    n = append_chat(w, user_text, say, intent)
    mood = update_mood(w, mood_delta, mood_name,
                       note=(say or "").replace("\n", " ")[:80])
    mem = append_memory(sandbox, memory_line) if memory_line else ""
    return {"chat_lines": n, "mood": mood, "memory": mem}
