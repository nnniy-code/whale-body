# 01 · 参数边界与族（bounds）

> 对应 HANDOFF §12 第 1 份、§4.5
> 产出：`config/bounds.json`
> 关键：**不是裸的 min/max，是「族」——G 默认值 + P 搜索范围 + 谓词阈值档**三元组。

---

## 1. 为什么按族组织

同一组参数范围对不同动作是错的：

- `walk` 的 `lean` 允许 −10..+10（可能是后仰着散步）
- `run` 的 `lean` 必须 ≥ 5（跑步不前倾就是倒退跑）
- `run` 的 `foot_grounded` 阈值在腾空段要**跳过**，`walk` 不能跳过

如果只写一套全局 min/max，`run` 和 `walk` 会共用一个不该共用的范围，搜索要么被卡死要么搜出怪东西。所以：**族 = 一个动作类型的完整约束包**。

---

## 2. 参数清单

### 2.1 P — 上半身姿态（10 维，CMA-ES 主搜索）

| 维 | 单位 | 全局范围 `[经验]` | 说明 |
|---|---|---|---|
| `lean` | 度 | −35 … 35 | 负 = 后仰 |
| `side_bend` | 度 | −25 … 25 | 正 = 向左侧弯 |
| `head_nod` | 度 | −30 … 30 | 正 = 低头 |
| `head_tilt` | 度 | −30 … 30 | 歪头 |
| `head_yaw` | 度 | −45 … 45 | 转头 |
| `arm_swing` | 度 | 0 … 60 | 幅度；相位来自 G |
| `arm_lift` | 度 | **−20 … 180** | 正 = 上举（伸展要 140） |
| `elbow_bend` | 度 | 0 … 140 | 屈肘 |
| `hand_bias` | 度 | −30 … 30 | 腕偏置 |
| `tail_curl` | 度 | −40 … 40 | 尾巴卷曲 |

> **坐标约定（2026-11-24 实测，踩过一次）**：以上角度都是**骨骼局部坐标系**里的旋转，
> 不是世界角度。这具骨架的局部轴与世界轴不一致——例如 `spine` 的局部 X 轴指向世界 **−Y**（侧向），
> 所以「前倾 20°」不能直接往 `basis` 里塞绕世界 X 的旋转，要先转局部：
> `R_local = R_rest.T @ R_world @ R_rest`（rest 正交，`inv = transpose`）。
> 实现细节：`tests/test_proprio.py` 的 `world_rot()`。**所有「语义角度 → basis」的地方都要过这一步。**

### 2.2 G — 下肢与步态（11 维，轨迹基上可搜）

| 维 | 单位 | 全局范围 `[经验]` | 说明 |
|---|---|---|---|
| `duty` | — | **0.30 … 1.00** | <0.5 自动进腾空相；1.0 = 始终支撑（坐/跪/站） |
| `period` | 秒 | 0.40 … 1.50 | 一个完整步态周期 |
| `stride` | 米 | **−0.90 … 0.90** | **负 = 倒退**（跑步 0.85 见 seed_families） |
| `foot_lift` | 米 | 0 … 0.18 | |
| `stance_width` | 米 | 0.05 … 0.35 | |
| `landing_height` | 米 | −0.03 … 0.03 | 落脚高度偏移 |
| `ankle_bias` | 度 | −30 … 30 | 正 = 踮脚 |
| `knee_bias` | 度 | **−10 … 120** | 屈膝是单向的：坐 60°、跪 95° 都在这范围内 |
| `turn_rate` | 度/秒 | −180 … 180 | |
| `phase_offset` | — | 0.30 … 0.70 | 左右相位差，正常 0.5 |
| `footfall` | 枚举 | `alternate` / `same_side` / `fixed` | 落脚序列 |

**`footfall` 是唯一的半离散量**。它不进 CMA-ES 的连续搜索——由族指定，或由 LLM 在 K 里给。

---

## 3. `bounds.json` schema

```json
{
  "v": 1,
  "_global": {
    "G": { "duty": 0.5, "period": 1.0, "stride": 0.0, "foot_lift": 0.02,
           "stance_width": 0.16, "landing_height": 0.0,
           "ankle_bias": 0, "knee_bias": 0, "turn_rate": 0,
           "phase_offset": 0.5, "footfall": "alternate" }
  },
  "families": {
    "wave": {
      "G": { "...": "站桩，同 _global" },
      "P_range": {
        "lean": [-8, 8], "side_bend": [-10, 10],
        "head_nod": [-10, 20], "head_tilt": [-25, 25], "head_yaw": [-30, 30],
        "arm_swing": [0, 35], "arm_lift": [30, 100],
        "elbow_bend": [20, 100], "hand_bias": [-20, 20], "tail_curl": [-20, 30]
      },
      "thresholds": { "foot_grounded_tol_mm": 35 },
      "predicates_hint": ["hand_above_head", "no_big_step"]
    },
    "walk": {
      "G": { "duty": 0.62, "period": 1.1, "stride": 0.55, "foot_lift": 0.09,
             "stance_width": 0.17, "landing_height": 0.0,
             "ankle_bias": 0, "knee_bias": 0, "turn_rate": 0,
             "phase_offset": 0.5, "footfall": "alternate" },
      "P_range": {
        "lean": [-6, 12], "...": "其余同上，略"
      },
      "thresholds": { "foot_grounded_tol_mm": 35, "skip_airborne_check": false }
    },
    "run": {
      "G": { "duty": 0.35, "period": 0.62, "stride": 0.85, "foot_lift": 0.12,
             "stance_width": 0.14, "turn_rate": 0, "phase_offset": 0.5,
             "footfall": "alternate" },
      "P_range": { "lean": [5, 25], "arm_swing": [20, 60], "...": "…" },
      "thresholds": { "foot_grounded_tol_mm": 35, "skip_airborne_check": true }
    }
  }
}
```

| 字段 | 说明 |
|---|---|
| `_global.G` | 族不存在时的兜底（HANDOFF §4.5「族不存在时的 G 默认」） |
| `families.<name>.G` | 该族的 G 初值；CMA-ES 从它出发搜 |
| `P_range` | 该族允许的 P 搜索范围，**必须是全局面板的子集** |
| `thresholds` | 谓词阈值档（`foot_grounded_tol_mm`、`skip_airborne_check`） |
| `predicates_hint` | 该族常用的谓词（LLM 没给全时补默认） |

---

## 4. 族从哪来（种子库）

P4 的库不能从零长——否则第一次「她活了」要等很久。种子来源：**旧 14 个动词 → 14 个族**。

映射关系（完整表是第 16 份设计，批 3）：

| 旧动词 | 族 | G | 说明 |
|---|---|---|---|
| `walk` | `walk` | duty 0.62 | 直线走 |
| `run` | `run` | duty 0.35 | 腾空 |
| `sit` | `sit` | duty 1.0 + squat | 腿固定 |
| `kneel` | `kneel` | duty 1.0 + squat 更深 | |
| `wave` | `wave` | 站桩 | P 为主 |
| `point` | `point` | 站桩 | P + K 目标 |
| `spin` | `spin` | turn_rate 驱动 | |
| `talk` | `talk` | 站桩 + 小幅度 | |
| … | … | … | 其余见第 16 份 |

**这张表的设计原则**：旧动词的「参数语义」直接继承，但实现全部重写（§6 说搬语义不搬实现）。

---

## 5. 族与谓词的耦合（这一条最容易漏）

`foot_grounded` 的容差随族变，且 `run` 要跳过腾空段。所以 gate 求值时**必须拿到族名**：

```python
verdict = gate.verdict(traj, predicates, family=meta["family"])
```

`trajectory.meta` 必须带 `family` 和 `duty`。这是 `trajectory` 的强制字段（见 03-gate §2）。

---

## 6. 验收

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 每个族的 `P_range` ⊂ 全局范围 | 程序断言全过 | `bounds.json` |
| `run` 族搜出的 `lean` 全部 ≥5 | 达（否则族定义漏了） | `run.P_range.lean` |
| `walk` 播放时 `foot_grounded` 用 35 mm | 达 | `thresholds` |
| `run` 腾空段不判 `foot_grounded` | 达 | `skip_airborne_check` |
| 新族（库里没有） | 能起步（用 `_global`） | `_global.G` |

---

## 7. 风险

1. **范围给太宽**：CMA-ES 会在无意义的区域浪费预算。宽范围只在「确实可能有极端值」的维度上给（如 `stride` 允许负）。
2. **`P_range` 与 `thresholds` 不一致**：比如 `run` 的 `lean` 允许 5–25，但 `torso_upright` 阈值 35°——不冲突，但要注意别把两个数设成互相矛盾。
3. **族爆炸**：如果每个新意图都建一个族，库会碎。原则：**族是动作类型，不是意图**。「拍拍头」和「摸摸头」是同一个族 + 不同参数。


