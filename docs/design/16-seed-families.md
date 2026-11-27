# 16 · 种子族映射（seed_families）

> 对应 HANDOFF §12 第 16 份、01-bounds §4
> 产出：`config/seed_families.json`
> 一句话：**让 L1 第一天就有东西可查**，不然「她活了」的第一次反馈要等很久。

---

## 1. 映射原则

| 搬 | 不搬 |
|---|---|
| 旧 `verbs.py` 的**参数语义**（幅度、时长、重复次数、快慢） | 正弦摆腿的实现 |
| 动词的**名字**（用户已经习惯） | `ease:"snap"` 那套缓动 |
| 每个动词的**目标**（要表达什么） | 逐帧写快照的驱动方式 |

**族不是动词的一比一**：`gesture` 是大类，要拆；`custom` 是兜底，不建族。

---

## 2. 14 个旧动词 → 12 个族 + 1 兜底

| 旧动词 | 族 | 定位 | G 默认（duty/period/stride） | P 主通道 |
|---|---|---|---|---|
| `apose` | —（基准） | 不是族，是 `base_rest` 的参照 | — | 全 0 |
| `walk` | `walk` | 直线走 | 0.62 / 1.10 / 0.55 | lean 0..12, arm_swing 10..35 |
| `run` | `run` | 跑步 | 0.35 / 0.62 / 0.85 | lean 8..25, arm_swing 25..60 |
| `sit` | `sit` | 坐 | 1.00 / 1.0 / 0.0 | lean −5..10, arm_lift 0..40 |
| `kneel` | `kneel` | 跪 | 1.00 / 1.0 / 0.0（squat 更深） | lean 0..20 |
| `rest` | `rest` | 躺/靠 | 1.00 / 1.0 / 0.0 | lean −20..0, head_nod −10..10 |
| `idle` | `idle` | 站桩待机 | 0.50 / 1.00 / 0.0 | 全部小幅度 |
| `wave` | `wave` | 挥手 | 0.50 / 1.00 / 0.0 | arm_lift 40..100, elbow 20..90 |
| `point` | `point` | 指 | 0.50 / 1.00 / 0.0 | arm_lift 70..110, elbow 0..20 |
| `stretch` | `stretch` | 伸展 | 0.50 / 1.00 / 0.0 | arm_lift 100..140, lean −10..5 |
| `spin` | `spin` | 转圈 | 0.50 / 1.00 / 0.0 | **turn_rate 驱动**（G），P 小幅度 |
| `talk` | `talk` | 说话时的小动作 | 0.50 / 1.00 / 0.0 | head_nod/yaw 小幅, arm_swing 5..20 |
| `gesture` | `pat_head` / `wave` / `point` 三选一 | 手势大类**拆开** | 同对应族 | — |
| `custom` | —（不建族） | 走「新族」路径（01-bounds §4） | `_global` | — |

**`gesture` 必须拆**：上一代把它当一个动词，导致所有手势共用一套参数范围，搜出来的东西「既不像挥手也不像指」。拆开后每个族有自己的 `P_range`。

---

## 3. `seed_families.json` 的结构

```json
{
  "v": 1,
  "source": "embodied-body/verbs.py（参数语义） + world/body.json（动词名）",
  "families": {
    "wave": {
      "label": "挥手",
      "G": { "duty": 0.5, "period": 1.0, "stride": 0.0, "foot_lift": 0.02,
             "stance_width": 0.16, "landing_height": 0.0, "ankle_bias": 0,
             "knee_bias": 0, "turn_rate": 0, "phase_offset": 0.5,
             "footfall": "alternate" },
      "P_default": { "lean": 0, "side_bend": 0, "head_nod": 0, "head_tilt": 0,
                     "head_yaw": 0, "arm_swing": 20, "arm_lift": 70,
                     "elbow_bend": 45, "hand_bias": 0, "tail_curl": 5 },
      "P_range": { "lean": [-8, 8], "side_bend": [-10, 10],
                   "head_nod": [-10, 20], "head_tilt": [-25, 25],
                   "head_yaw": [-30, 30], "arm_swing": [0, 35],
                   "arm_lift": [40, 100], "elbow_bend": [20, 90],
                   "hand_bias": [-20, 20], "tail_curl": [-20, 30] },
      "thresholds": { "foot_grounded_tol_mm": 35, "skip_airborne_check": false },
      "predicates_hint": ["hand_above_head", "no_big_step"]
    }
  }
}
```

`P_default` 是**新字段**（01-bounds 的 schema 里没有）：族首次使用时，CMA-ES 从这里出发而不是从全局中点。

---

## 4. 怎么从旧 `verbs.py` 取语义（不要重写逻辑）

旧代码在 `D:\DSproject\embodied-body\verbs.py`（35.7 KB，只读）。要取的是**每个动词的参数清单和默认值**，具体是：

```
1. grep 出每个动词的函数/字典定义
2. 抄它的参数名（amp / dur / reps / side / ease ...）
3. 映射到 P/G：
   amp      → arm_swing / arm_lift 的幅度
   dur      → dur_s
   reps     → 不进参数（由 phase 循环决定）
   side     → 谓词的 side
   ease     → 丢弃（幂缓动禁用）
4. 默认值填进 P_default / G
```

**不要抄它的 `_ease` 实现、不要抄正弦摆腿、不要抄 CCD。**

---

## 5. 验收

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 启动后 L1 立即查 `wave` | 命中（种子条目存在） | 种子注入时机 |
| 12 个族各播一次 | 都能播，无 `big_steps` | `P_default` |
| `run` 族播出来 | 确实有腾空（duty 0.35） | G 默认 |
| `kneel` 族 | 膝盖低于髋 | squat 深度 |
| `gesture` 输入 | 被映射到 `pat_head`/`wave`/`point` 之一，不报「未知族」 | 映射规则 |

**种子条目在启动时以 `status: provisional` 注入**，不是 `verified`——它们没经过 BVH。第一次夜间复检后转正。

---

## 6. 风险

1. **`P_default` 是 `[经验]`**，不知道怎么摆的话就从 `_global` 起步让 CMA-ES 搜，但那样第一次会慢。
2. **旧动词的 `dur` 是「秒」还是「帧」**——取的时候要确认单位，差 30 倍。
3. **`gesture` 的拆分规则要固定**：按谓词里出现的锚点决定（`head_top` → `pat_head`，`point` → `point`，否则 `wave`）。不要用 LLM 的自然语言猜。
