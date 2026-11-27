"""tools/make_sandbox.py — 从 dsh-qq 提取「大肥鱼」的人格/记忆，做成只读测试沙箱。

怎么测：python tools/make_sandbox.py
产物：sandbox/ 下 人格 + 记忆 + 日记 + 状态快照
**不复制**：voice/（wav）、stickers/（图片）、.runtime/（渲染图）、.env（key）
**不修改**任何源文件。
"""

from __future__ import annotations

import json
import os
import shutil

SRC = r"D:\DSproject\dsh-qq"
W = os.path.join(SRC, "world")
DST = r"D:\DSproject\body-v2\sandbox"

# 直接复制的状态文件（小、纯文本/JSON）
WORLD_FILES = [
    "persona.json", "memory.json", "self.md", "mood.json", "emotion.json",
    "purpose.json", "plan.json", "presence.json", "flags.json", "thread.json",
    "stats.json", "counters.json", "mem-state.json", "backup.json",
    "body.json", "body_lib.json", "receipt.json", "pose.json",
]
ROOT_FILES = ["persona.md", "memory.md"]

# 复制但裁剪的（大文件）
TRIM = {
    "chat.jsonl": 200,          # 只留最后 200 行
    "outbox.jsonl": 100,
    "emotion_log.jsonl": 100,
}

# 目录复制（只留 md，且限制个数）
DIRS = {"diary": 12, "self": 10}

# 明确不复制
SKIP_NOTE = ["voice/（音频）", "stickers/（图片）", ".runtime/（渲染）", ".env（API key）",
             "body_log.jsonl（1MB 日志）", "memory-full.jsonl"]


def main() -> int:
    if os.path.exists(DST):
        shutil.rmtree(DST)
    os.makedirs(DST, exist_ok=True)

    copied = []

    for f in WORLD_FILES:
        p = os.path.join(W, f)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(DST, f))
            copied.append((f, os.path.getsize(p)))

    for f in ROOT_FILES:
        p = os.path.join(SRC, f)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(DST, f))
            copied.append((f, os.path.getsize(p)))

    for f, keep in TRIM.items():
        p = os.path.join(W, f)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()[-keep:]
        with open(os.path.join(DST, f), "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        copied.append((f + f"（末 {keep} 行）", sum(len(x) for x in lines)))

    for d, limit in DIRS.items():
        sp = os.path.join(W, d)
        if not os.path.isdir(sp):
            continue
        dp = os.path.join(DST, d)
        os.makedirs(dp, exist_ok=True)
        files = sorted(f for f in os.listdir(sp) if f.endswith(".md"))[-limit:]
        for f in files:
            shutil.copy2(os.path.join(sp, f), os.path.join(dp, f))
        copied.append((d + "/", sum(os.path.getsize(os.path.join(dp, f)) for f in files)))

    # 人格速览
    persona_txt = ""
    pj = os.path.join(W, "persona.json")
    if os.path.exists(pj):
        try:
            obj = json.load(open(pj, encoding="utf-8"))
            persona_txt = json.dumps(obj, ensure_ascii=False, indent=2)[:1200]
        except Exception:
            pass

    readme = f"""# 大肥鱼 · 测试沙箱

从 `D:\\DSproject\\dsh-qq` 提取的**只读副本**，用于 body-v2 联调。

## 这是什么

「她」= `dsh-qq` 的世界线系统：
- 人格：`world/persona.json` + `persona.md`
- 记忆：`world/memory.json` + `memory.md`
- 自我：`world/self.md` + `self/`
- 日记：`world/diary/`
- 情绪：`world/emotion.json` / `mood.json`
- 身体接口：`world/body*.json` / `pose.json` / `receipt.json`

## 不包含（故意）

{chr(10).join('- ' + x for x in SKIP_NOTE)}

## 隔离规则（重要）

1. 测试**只读写 `sandbox/`**，绝不碰 `D:\\DSproject\\dsh-qq\\world\\` 的任何文件
2. 生产世界线的 `memory.json` / `self.md` / `diary/` 先手动备份
3. 联调完成后，用这个目录的差异人工判断要不要合并回生产

## 重新生成

```bash
python tools/make_sandbox.py
```

## 人格速览（persona.json 前 1200 字）

```json
{persona_txt}
```
"""
    open(os.path.join(DST, "README.md"), "w", encoding="utf-8").write(readme)

    print("SANDBOX", DST)
    for name, size in copied:
        print(f"  {name:32s} {size:8d}")
    print()
    print("total files:", len(os.listdir(DST)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
