# 身体 v2 · 设计文档索引

> 配套：[HANDOFF.md](../../HANDOFF.md) · [WORKFLOW.md](../WORKFLOW.md) · [LOGGING.md](../LOGGING.md)
> **批 1**（第 1、2、3、9、10、11、13 份）= 地基，必须一起审。
> **批 2**（第 4、5、6 份）= 能反应，对应 P4/P5。

## 阅读顺序（也是依赖顺序）

```
── 批 1 地基 ──
09 anchors      ← 语义点从哪来（所有谓词底座）
    │
02 predicates   ← 判据怎么写（连续误差 + 时间语义）
    │
03 gate         ← 怎么求值、怎么定位违规
    │
01 bounds       ← 族：G 默认 + P 范围 + 阈值档
    │
10 emotion_bias ← 情绪往哪些通道上偏、偏多少
11 capsules     ← 穿模的快速近似
13 collision    ← 穿模的精确判定 + 归因

── 批 2 能反应 ──
06 llm-schema   ← LLM 说什么（判据从哪来）
    │
04 library      ← 查得到吗（intent_key + 状态机）
    │
05 dispatcher   ← 什么时候播、兜底、打断
```

## 文件清单

### 批 1 · 地基

| 文件 | 对应 §12 | 产出 | 状态 |
|---|---|---|---|
| [09-anchors.md](09-anchors.md) | 第 9 份 | `config/anchors.json` + 标定脚本 | ✅ |
| [02-predicates.md](02-predicates.md) | 第 2 份 | `core/predicates.py` 的谓词表 | ✅ |
| [03-gate.md](03-gate.md) | 第 3 份 | `core/gate.py` 的接口与输出格式 | ✅ |
| [01-bounds.md](01-bounds.md) | 第 1 份 | `config/bounds.json` 的族定义 | ✅ |
| [10-emotion_bias.md](10-emotion_bias.md) | 第 10 份 | `config/emotion_bias.json` | ✅ |
| [11-capsules.md](11-capsules.md) | 第 11 份 | `config/capsules.json` + 生成脚本 | ✅ |
| [13-collision.md](13-collision.md) | 第 13 份 | `config/collision.json` + 归因算法 | ✅ |

### 批 2 · 能反应

| 文件 | 对应 §12 | 产出 | 状态 |
|---|---|---|---|
| [06-llm-schema.md](06-llm-schema.md) | 第 6 份 | `llm/schema.json` + 校验/降级 | ✅ |
| [04-library.md](04-library.md) | 第 4 份 | `core/library.py` + `library.jsonl` 格式 | ✅ |
| [05-dispatcher.md](05-dispatcher.md) | 第 5 份 | `core/dispatcher.py` 状态机与时序 | ✅ |

### 待写（批 3）

| 文件 | 对应 §12 | 产出 | 状态 |
|---|---|---|---|
| [07-trajectory.md](07-trajectory.md) | 第 7 份 | Hermite + 段间速度传递 + 五次曲线 | ✅ |
| [12-fk-golden.md](12-fk-golden.md) | 第 12 份 | 10 姿态 × 全骨，6 条断言 | ✅ |
| [17-player.md](17-player.md) | 第 17 份 | phase/elapsed/暂停/变速 | ✅ |
| [16-seed-families.md](16-seed-families.md) | 第 16 份 | 旧 14 动词 → 12 族 | ✅ |
| [14-failure-ttl.md](14-failure-ttl.md) | 第 14 份 | failed + stale 状态机 | ✅ |
| [15-log-selftest.md](15-log-selftest.md) | 第 15 份 | 3 条日志自检 | ✅ |
| [08-acceptance.md](08-acceptance.md) | 第 8 份 | 全阶段验收脚本清单 | ✅ |

**17 份全部完成。** 建议审阅顺序：批 1（09→02→03→01）→ 批 2（06→04→05）→ 批 3（07→12→17→16→14→15→08）。

## 统一约定（所有设计文档共用）

1. **每个数值必须带来源标注**：`[实测 seq432]` / `[经验·待校准]` / `[待定 Px]`
2. **每个模块必须有「怎么测 / 预期现象 / 调参位置」三件套**
3. **拒绝实现细节**：本轮只定接口、公式、阈值、schema；函数体不写
4. **不引入新依赖**：numpy 可用（已在 DSH 环境），pycma 可用；其他现装
5. **单位一律写在字段名里**：`_mm` / `_deg` / `_s` / `_ms`
