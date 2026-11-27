# 沙箱：角色数据

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
