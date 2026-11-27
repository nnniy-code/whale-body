# 06 · LLM 输出契约（llm/schema.json）

> 对应 HANDOFF §12 第 6 份、D1
> 产出：`llm/schema.json` + `llm/interpret.py` 的校验逻辑
> 一句话：**LLM 说「要什么」，不说「怎么做」。** 上一代用 184 倍差距（92 mm vs 0.5 mm）证明了这条界限。

---

## 1. 输出结构

```json
{
  "say": "好呀，我给你挥挥手～",
  "act": {
    "intent": "wave",
    "mood": { "val": 75, "aro": 55, "close": 80, "guard": 25 },
    "reuse": false,
    "dur_s": 1.2,
    "predicates": [
      { "name": "hand_above_head", "args": { "side": "right", "margin_mm": 50 } },
      { "name": "head_tilt", "args": { "deg": 12, "tol_deg": 6 } }
    ]
  }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `say` | ✅ | 要说的话，直接送 TTS，**不等动作** |
| `act.intent` | ✅ | 动词标签，**必须是 `bounds.json` 里的族名之一**（`wave` / `walk` / `run` / `pat_head` / …）。不在表里的 → 走「新族」路径（01-bounds §4），但要做一次规范化（`挥手`→`wave`、`pat`→`pat_head`） |
| `act.mood` | ❌ | 本次心情，只用于选族/偏幅，**不写回情绪轴**（第 10 份） |
| `act.reuse` | ❌ | true = 允许用库里最像的（阈值放宽到 0.45）；false = 只接受精确命中（<0.25）。默认 false |
| `act.dur_s` | ❌ | 期望时长；缺省由族默认；**可能被 clamp**（见 §5）。**周期动作（walk/run）的 `dur_s` 是「持续多久」，不是周期**——周期是 `G.period`，两回事 |
| `act.predicates` | ✅ | **至少 1 条**。这是唯一的验收标准 |

**`reuse` 的语义**：`true` 时即使 L1 命中的条目距离稍远（<0.45）也直接用，不重新求解——用于「她正忙/想让动作快点出」。默认 `false`（宁可求个更好的）。

---

## 2. 谓词 DSL（LLM 只能填 `args`，不能发明新谓词）

```json
{ "name": "<白名单里的名字>", "args": { ... } }
```

**白名单**（对应 [02-predicates](02-predicates.md)，LLM 的提示词里要给全表）：

| 类别 | 可用谓词 |
|---|---|
| 位置 | `hand_near_anchor` / `hand_at_point` / `hand_above_head` / `hand_below_waist` / `foot_at_point` / `tail_tip_at_anchor` |
| 朝向 | `head_tilt` / `head_yaw` / `head_nod` / `gaze_toward` / `torso_lean` |
| 支撑 | `foot_grounded` / `foot_lifted` / `dual_support` / `no_ground_penetration` |
| 空间 | `no_self_collision` / `inside_workspace` / `torso_upright` |
| 动态 | `max_deg_per_frame` / `no_big_step` / `no_micro_jitter` |
| 轨迹 | `foot_slip` / `jerk_rms` / `duty_match` |

**`args` 的键必须在谓词的 schema 里**，多一个键就拒。

---

## 3. 什么能说、什么不能说

### ✅ 允许：空间目标

```json
{ "name": "hand_near_anchor", "args": { "side": "right", "anchor": "mouth", "tol_mm": 40 } }
```

**这是 D1 的核心形式**：写「手到嘴边」，实测偏差 0.5 mm；写关节角，偏差 92 mm。

### ✅ 允许：语义角度（仅限躯干/头，单骨）

`head_tilt` / `head_nod` / `head_yaw` / `torso_lean` 的 `deg` 可以给。

理由：这四个是**人能直接说出口的语义量**（「歪头 12 度」），且各自只对应一根骨，LLM 的估计误差不会放大。

### ❌ 禁止：四肢关节角

```json
{ "name": "rightUpperArm_rotation", "args": { "deg": 45 } }        // 拒绝
{ "name": "elbow_bend", "args": { "deg": 90 } }                    // 拒绝
{ "name": "hand_at_point", "args": { "target_m": [0.3, -0.4, 0.9] } } // 允许，但见下
```

**为什么禁四肢角**：肩膀、肘、腕的协调关系是 3 自由度耦合的，LLM 给的角度几乎必错（这就是 92 mm 的来源）。要指定手在哪，用 `hand_at_point` 给**世界坐标**或 `hand_near_anchor` 给**锚点**。

`hand_at_point` 的 `target_m` 允许——那是位置不是角度。但 LLM 应该**优先用锚点**（`hand_near_anchor`），因为坐标要它自己估空间位置，也容易错。

---

## 4. 好 / 坏示例

### ✅ 好

```json
{
  "say": "来，拍拍你的头～",
  "act": {
    "intent": "pat_head",
    "dur_s": 1.0,
    "predicates": [
      { "name": "hand_near_anchor", "args": { "side": "right", "anchor": "head_top", "tol_mm": 50 } },
      { "name": "torso_lean", "args": { "deg": 6, "tol_deg": 5 } }
    ]
  }
}
```

### ❌ 坏（四条不同原因）

```json
{
  "act": {
    "intent": "wave",
    "predicates": [
      { "name": "rightUpperArm.rotation", "args": { "x": 45 } },      // ① 发明谓词
      { "name": "elbow_bend", "args": { "deg": 90 } },                // ② 禁四肢角
      { "name": "hand_near_anchor", "args": { "side": "right",
          "anchor": "mouth", "tol_mm": 40, "extra": 1 } }             // ③ 多余键
    ]
  }
}                                                                     // ④ 没有 say
```

---

## 5. 校验与降级

```
1. JSON 可解析？
   └ 否 → 重试（≤2 次），仍失败 → 降级：只播 say + idle 兜底
2. say 非空？
   └ 否 → 允许（可以只做动作不说话），但记 warn
3. act.predicates 非空 且 全部通过 schema？
   └ 否 → 回传结构化错误给 LLM，重试（≤2 次）
4. dur_s 是否够完成？
   └ 不够 → clamp 到物理下限（见 §6），回执注明
5. 全部通过 → 进 dispatcher
```

**重试上限 2 次**。第三次还坏，降级为「say + idle」，并记 `error` 事件——**不要让她因为 LLM 输出坏而僵住。**

---

## 6. `dur_s` 的物理下限（clamp 规则）

```python
def clamp_dur(dur_s, family) -> float:
    # 该动作需要的最大关节角变化，除以每帧角速度预算（12°/帧 @30fps）
    required = max_deg_change(family) / (12.0 * 30.0)
    return max(dur_s, required, 0.25)
```

- `dur_s` 太短 → 拉长到 `required`，**回执里写 `dur_clamped: 0.3 → 0.9`**
- **不静默**：回执要进库（`dur_s` 存实际值），也要让 LLM 下一轮知道（可以拼进对话的 system 提示）

**为什么不直接拒绝**：LLM 不知道角速度预算，0.3 s 的请求是正常表达的「我要快一点」，不是错误。拒绝会让大多数请求失败（HANDOFF 已定这条）。

**什么情况才拒绝**：拉到下限后仍然搜不出解 → `family_infeasible`，那时才回传 LLM。

---

## 7. 提示词里必须给 LLM 的东西

`llm/interpret.py` 拼 prompt 时必须包含（缺一样 LLM 就会瞎猜）：

1. **谓词白名单 + 每个谓词的 args schema**（从 `schema.json` 自动生成，不要手抄）
2. **可用锚点名**（`mouth` / `head_top` / `chest` / `hand.R` / …，从 `anchors.json` 读）
3. **可用族名**（`wave` / `walk` / `run` / `pat_head` / …，从 `bounds.json` 读）
4. **一句硬规则**：「不要给肩/肘/腕的角度；位置用锚点；至少一条谓词」
5. **当前情绪轴的值**（帮助它选 mood，但 mood 不写回）

**不要**把整个 HANDOFF 塞进 prompt。只给这 5 样。

---

## 8. 验收（P5）

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 40 条正常输入 | 解析成功率 ≥95% | prompt 里的谓词表 |
| 故意给坏 JSON | 重试 ≤2 次后降级，不崩 | §5 |
| 故意给四肢角度 | 被拒并回传原因 | §3 |
| `dur_s=0.1` | clamp 到 ≥0.25 或物理下限，回执注明 | §6 |
| 没有 `predicates` | 被拒（至少 1 条） | schema |
| 谓词名拼错 | 被拒，错误里给「你是不是想用 X」 | schema 的模糊匹配 |

---

## 9. 风险

1. **谓词表太长会稀释 LLM 注意力**。22 条已经接近上限。如果实测发现它总用错，按族给子集（`wave` 族只给位置+朝向类），而不是继续加。
2. **`hand_at_point` 的马虎使用**：LLM 会倾向给坐标（它熟悉 xyz），但那比锚点更容易错。prompt 里要把锚点标成「首选」。
3. **`reuse` 的语义容易被 LLM 误解**成「复用说话内容」。prompt 里要写清楚「复用已学的动作」。
