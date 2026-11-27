# 04 · 参数库（library）

> 对应 HANDOFF §12 第 4 份、D2
> 产出：`core/library.py` 的 schema 与检索接口 + `logs/library.jsonl` 的可重放格式
> 一句话：**库是长出来的经验，不是检索素材。** 命中率高是因为用得多，不是因为搜得准。

---

## 1. 条目结构

```json
{
  "id": "lib-0042",
  "status": "verified",
  "family": "wave",
  "intent_key": {
    "predicates": ["hand_above_head", "head_tilt"],
    "anchors": ["head_top", "hand.R"],
    "verb": "wave"
  },
  "params": {
    "P": {"lean": 2.1, "side_bend": -0.4, "head_nod": -3.2, "head_tilt": 14.0,
          "head_yaw": 0.0, "arm_swing": 18.5, "arm_lift": 62.0,
          "elbow_bend": 35.0, "hand_bias": 4.0, "tail_curl": 8.0},
    "G": {"duty": 0.5, "period": 1.0, "stride": 0.0, "foot_lift": 0.02,
          "stance_width": 0.16, "landing_height": 0.0, "ankle_bias": 0,
          "knee_bias": 0, "turn_rate": 0, "phase_offset": 0.5, "footfall": "alternate"}
  },
  "dur_s": 1.2,
  "metrics": {
    "evals": 6210, "calc_ms": 118.4, "wall_ms": 412.0,
    "max_deg_per_frame": 8.2, "total_err": 0.0
  },
  "evidence": {
    "trace_id": "t-20261123-233001-0007",
    "gate": {"pass": 3, "total": 3},
    "verify": null
  },
  "parent": "lib-0011",
  "created_at": "2026-11-23T23:30:01+08:00",
  "ttl": null
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | `lib-NNNN`，递增，不复用 |
| `status` | ✅ | `provisional` / `verified` / `rejected` / `failed` |
| `family` | ✅ | 对应 `bounds.json` 的族名 |
| `intent_key` | ✅ | 检索用，见 §2 |
| `params` | ✅ | P + G；**不含情绪偏置**（情绪是运行时叠加的） |
| `dur_s` | ✅ | 实际时长（可能被 clamp 过） |
| `metrics` | ✅ | 求解时的实测值 |
| `evidence` | ✅ | 指向日志，能回溯到当时发生了什么 |
| `parent` | ❌ | 从哪条演化来的（谱系） |
| `ttl` | ❌ | 仅 `failed` 用；到期/条件变化后可重解 |

**`params` 不含情绪**：否则「同一个挥手在开心时」和「难过时」会存成两条，库指数膨胀。情绪在播放时叠加（第 10 份）。

**`history`（可选字段）**：`put` 判断为「同一动作的又一解」时，旧 `params` 推入：

```json
"history": [ { "params": {...}, "metrics": {...}, "replaced_at": "2026-11-24T01:00:00+08:00" } ]
```

只留最近 5 条。它的用途不是回滚，是**看同一个意图的解法在收敛还是在漂**——如果 5 条 `params` 差得很远，说明这个族的搜索不稳定。

---

## 2. `intent_key` 怎么构造（检索的全部依据）

不用文本 embedding（30 ms 检索 + 无模型依赖）。用**结构化的三部分**：

```python
intent_key = {
    "predicates": sorted({p.name for p in predicates}),           # 谓词名集合
    "anchors":    sorted({a for p in predicates for a in p.anchors}),  # 涉及的锚点
    "verb":       act.intent,                                      # 动词标签（可空）
    "numeric":    {p.name: p.value for p in predicates if p.value is not None},  # 数值参数
}
```

**距离 = 加权和，取值 [0,1]，<0.25 视为命中**：

```
d = 0.50 × (1 - Jaccard(predicates_a, predicates_b))
  + 0.20 × (1 - Jaccard(anchors_a, anchors_b))
  + 0.15 × (0 if verb_a == verb_b else 1)
  + 0.15 × mean_normalized_diff(numeric_a, numeric_b)
```

**数值差归一化**：对每个共同的谓词，`|v_a - v_b| / (tol 或全局范围)`，越界截断到 1。没有共同数值时该项记 0.5（中性，不奖不罚）。

**为什么权重这么分**：谓词集合决定「要什么」，锚点决定「在哪」，动词标签和数值是细调。`[经验]`，靠 P4 的命中率曲线调。

### 2.1 两个边界情况

| 情况 | 处理 |
|---|---|
| 新意图的谓词集是库里**完全没有的组合** | `Jaccard = 0` → d ≥ 0.5 → 不命中 → 进 L2。**这是正常路径，不是失败** |
| 两个不同动作有相同谓词名（如「手抬起来」） | 靠 `anchors` 区分（`head_top` vs `mouth`）+ `numeric`（高度差） |

**谓词名集合相同但方向相反**（`hand_above_head` vs `hand_below_waist`）天然不同，不需要额外处理。

---

## 3. 状态机

```
                    ┌──────────────┐
       求解成功 ───►│ provisional  │──BVH 过──► verified
                    └──────┬───────┘
                           │ BVH 不过 + 回灌 ≤5 轮
                           ▼
                    ┌──────────────┐
                    │   rejected   │  （不自动失效，需人工清）
                    └──────────────┘

       求解失败 ───► failed（带 family_infeasible，有 TTL）
```

| 状态 | 能播？ | 能检索？ | 会自动转走？ |
|---|---|---|---|
| `provisional` | ✅ | ✅ | L3 夜间 |
| `verified` | ✅ | ✅ | 否 |
| `rejected` | ❌ | ❌（跳过） | 否 |
| `failed` | ❌ | ❌（跳过） | 条件变化后重开 |

**`provisional` 能播**：L2 解出来当场就播，不等夜间。provisional 和 verified 的区别是「敢不敢长期用」，不是「能不能播」。

---

## 4. TTL 规则（第 14 份的精简版）

`failed` 在下列任一变化后**自动失效**，允许重解：

- `bounds.json` 变（族范围变了，可能就能解了）
- `weights.json` 变（权重变了，搜索目标变了）
- `capsules.json` / `collision.json` 变（约束松了）
- 骨架变（`fk_checksum` 变）

`rejected` **不自动失效**——否则一个误判会反复烧预算。需人工 `library.clear_rejected(id)`。

---

## 5. 检索接口

```python
def query(intent_key, k=5) -> list[Entry]:
    """返回按距离排序的前 k 条，已过滤 failed/rejected。"""

def best(intent_key, threshold=0.25) -> Entry | None:
    """距离 < threshold 的最近条目，没有则 None。"""

def put(params, intent_key, status="provisional", parent=None) -> Entry:
    """写库。同 intent_key 且距离 <0.05 视为重复，就地更新 params（留 history）。"""

def set_status(id, status, violations=None) -> None:
    """状态迁移。每次迁移写一条 lib_status 事件。"""
```

**`put` 的去重规则**：距离 <0.05 说明是「同一个动作的又一种解法」——不要新建条目，就地更新 `params`，把旧的推到 `history`。否则同一个动作会存几十条。

---

## 6. 可重放（`library.jsonl` 的硬要求）

从 `logs/library.jsonl` 的 `lib_put` + `lib_status` 按 `seq` 重放，**必须得到与内存完全一致的库**。

这条是 `selftest_library.py` 的第三条自检（LOGGING §6）。它保证：

- 崩溃后能重建库（不依赖内存）
- 「库为什么变成这样」可回溯（每条都有 trace_id）

---

## 7. 验收

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 库里有 10 条，查一个已有意图 | 30 ms 内返回 | 阈值 0.25 |
| 查一个谓词组合库里没有的意图 | 返回 None，进 L2（**不是报错**） | — |
| 同一意图连续查 100 次 | 全部命中同一条（确定性） | 排序 tie-break 用 `id` |
| 同一 intent_key 求解两次 | 只有一条，`history` 长度 1 | `put` 去重 0.05 |
| 重放 `library.jsonl` | 逐字段等于内存库 | — |
| 把 `weights.json` 改一个数 | 所有 `failed` 失效 | TTL 规则 |

---

## 8. 风险

1. **阈值 0.25 是 `[经验]`**：太松会命中不相关的动作（她做了个错的动作），太紧则永远进 L2（反应慢）。调法：P4 之后看「命中率 vs 用户觉得像不像」。
2. **`intent_key` 里没有「时间」**：同一个动作的「快版/慢版」靠 `dur_s` 区分，不靠 key。如果发现「快挥手」和「慢挥手」总是命中同一条，把 `dur_s` 加进 `numeric`。
3. **族爆炸**（01-bounds §7 也提了）：族是动作类型不是意图。如果 `wave` 裂成 `wave_1` / `wave_2`，说明 `put` 的去重阈值太严。
