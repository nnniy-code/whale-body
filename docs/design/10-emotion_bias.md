# 10 · 情绪 → 参数偏置（emotion_bias）

> 对应 HANDOFF §12 第 10 份、D6、P1.5
> 产出：`config/emotion_bias.json` + `core/emotion.py` 的低通
> 一句话：**情绪不改动作，只改同一个动作的「怎么动」。**

---

## 1. 输入

唯一来源：`world/emotion.json`

```json
{"axes": {"val": 72, "aro": 40, "close": 80, "guard": 20}, "last_label": "开心"}
```

| 轴 | 含义 | 0 | 100 |
|---|---|---|---|
| `val` | 愉悦度 | 很低落 | 很高兴 |
| `aro` | 唤醒度 | 慵懒 | 兴奋 |
| `close` | 亲近感 | 疏远 | 亲昵 |
| `guard` | 戒备 | 放松 | 紧张 |

**LLM 在 `act.mood` 里给的值不写回这里**（双源会让「她到底开不开心」不可解释）。`act.mood` 只用于「这次选哪个族/调多大幅度」。

---

## 2. 偏置公式（统一形式，不按参数分家）

```python
def bias(param_base, axis_value, k, lo, hi):
    n = clamp((axis_value - 50) / 50.0, -1.0, 1.0)   # [-1, 1]
    span = (hi - lo) / 2.0
    offset = clamp(k, -0.15, 0.15) * n * span        # 幅度封顶 ±15%
    return clamp(param_base + offset, lo, hi)
```

**为什么用「按范围比例」而不是「乘性」**：`lean` / `side_bend` 可以为负，乘性在跨零时符号翻转（负的 `lean` 会被「开心」推成更负）。按范围比例对所有参数都成立，且天然有单位。

**幅度封顶 ±15%** 是硬规则：超过 15% 就不是「同一个动作的不同情绪」，而是另一个动作了，A/B 对照会失效（HANDOFF P1.5）。

> **封顶是对「总偏移」做的，不是对单个 `k`**（2026-11-24 踩到）。
> `val` 和 `aro` 同时作用于 `arm_swing`（0.08 + 0.15），各轴单独截断后相加能到
> **30%**——已经不是同一个动作了。正确做法：先把各轴贡献**求和**，再对总和截断到 ±15%。
> 实现见 `core/emotion.py::apply_bias`。

---

## 3. 映射表（`emotion_bias.json`）

```json
{
  "v": 1,
  "global_k_max": 0.15,
  "axes": {
    "val": {
      "lean":        0.12,
      "head_nod":    0.10,
      "arm_swing":   0.08,
      "tail_curl":   0.15,
      "period":     -0.06
    },
    "aro": {
      "arm_swing":   0.15,
      "head_nod":    0.10,
      "foot_lift":   0.10,
      "period":     -0.12,
      "breath_rate": 0.15
    },
    "close": {
      "head_yaw":    0.12,
      "arm_lift":   -0.08,
      "stance_width": -0.06
    },
    "guard": {
      "side_bend":   0.10,
      "arm_lift":    0.12,
      "lean":       -0.08,
      "stance_width": 0.08,
      "turn_rate":   0.10
    }
  },
  "forbidden": ["K.targets", "anchors", "duty", "stride"]
}
```

| 字段 | 说明 |
|---|---|
| `axes.<轴>.<参数>` | k 系数，符号决定方向 |
| `k_max` | 单个 k 的上限，超了截断 |
| `forbidden` | **绝对不许被情绪改的东西** |

`forbidden` 里最重要的是 `K.targets`——情绪推漂目标点，D1 的旗舰证据（手到嘴 0.5 mm）就失效了。这条要写成启动时断言（见 §6）。

**`duty` / `stride` 也禁**：情绪不该改变「走得多快多远」的步态结构，只改「怎么摆」。这条是我的判断，`[经验]`，如果实测觉得「低落时步子该变小」，再放开 `period` 的系数（它已在表里）。

---

## 4. 低通（1 秒时间常数）

```python
alpha = 1 - math.exp(-dt / tau)      # tau = 1.0 s
axis_smoothed = axis_smoothed + alpha * (axis_raw - axis_smoothed)
```

- 读文件用 **stat mtime**，变了才 `json.load`（不要每帧读）
- 低通**必须用真实 dt** 还是固定步长？——用真实 dt + 指数形式（`1-exp(-dt/tau)`）本身就是 dt 无关的，不需要固定子步。这一点和弹簧不同（弹簧有刚度项，必须固定步长；一阶低通没有）

**为什么必须低通**：`emotion.json` 跳变（开心→难过）时，参数直接跳 = 又一个 `big_step`。1 秒是 `[经验]`。

---

## 5. 作用位置（顺序不能错）

```
pose = base_rest ⊕ G ⊕ P ⊕ emotion_bias ⊕ K(IK)
                              ↑
                    偏置在这里，只改 P/G 的连续通道
```

- 必须在 `K` **之前**：IK 目标优先级最高，情绪不许覆盖
- 必须在 `G`/`P` **之后**：偏置是对已有姿态的修饰

---

## 6. 验收（P1.5）

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| `val` 从 20 → 90 | 1 s 内姿态变化，**无跳变** | `tau` |
| 同一意图 × 两种情绪，测 `hand_near_mouth` | 目标点偏差 **<1 mm** | `forbidden` 是否被绕过 |
| 连续改 10 次 `emotion.json` | `big_steps` 保持 0 | 低通 + 封顶 |
| `k` 全部置 0 | 姿态与无情绪完全一致（逐位） | 公式是否真有旁路 |
| 启动断言 | `forbidden` 里的键不出现在任何 `axes` 表里 | 配置校验 |

第 4 条是「情绪链路不污染基准」的判据——**没有它，你永远分不清「动作变了」是参数改了还是情绪偏了**。

---

## 7. 风险

1. **k 全凭感觉**，`[经验]`。调法：固定动作，把某轴从 0 拉到 100，看差异「像不像」——这是主观的，写进 `review.jsonl`。
2. **四轴之间会耦合**（高兴通常也兴奋）。不要为每个轴单独调，先调 `val` + `aro` 这一对，它们贡献最大。
3. **没有「情绪 → 选族」**：本文件只管「同一个动作怎么偏」，至于「开心时更倾向挥手而不是站着」，那是 LLM 的 `act.mood` 决定的，不在此处。
