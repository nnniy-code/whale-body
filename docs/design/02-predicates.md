# 02 · 谓词表（predicates）

> 对应 HANDOFF §12 第 2 份
> 产出：`core/predicates.py` 的谓词定义 + `config/weights.json` 的默认权重
> 核心约束：**每条谓词返回连续误差 ∈ [0,1]，不是布尔。** 0 = 完全满足。

---

## 1. 契约

### 1.1 返回结构

```python
PredicateResult(
    name: str,              # "hand_near_mouth"
    err: float,             # [0,1]，0 = 通过
    err_unit: str,          # "normalized"
    time_sem: str,          # "at_end" | "at_any" | "throughout" | "peak"
    weight: float,          # 默认 1.0
    detail: dict,           # 必填：够定位到「差多少、差在哪」
)
```

**`detail` 是硬要求**。上一代的护栏只报 `ok:true`，出问题查不到是哪根骨哪一帧（缺陷 #7）。每条谓词的 `detail` 必须能回答「差多少、差在哪」。

### 1.2 误差归一化

统一形式：

```
err = max(0, (超出容差的部分) / scale)
```

`scale` 默认 = **2 × tol**。含义：超出容差一倍时记满分 1。

**为什么不用「是否通过」**：CMA-ES 需要梯度信号。布尔判据在边界处没有信息，搜索会退化成随机。

### 1.3 时间语义

| 值 | 含义 | 合成方式 |
|---|---|---|
| `at_end` | 只判最后一帧 | 直接取末帧 err |
| `at_any` | 整段中任一时刻满足即可 | `min` 整段 err（越小越好） |
| `throughout` | 整段每帧都满足 | **log-sum-exp 软最大值**，不要硬 max |
| `peak` | 取整段最优帧的 err（用于「过程中某一刻」） | 同 `at_any`，但报告最优帧号 |

**`throughout` 必须用软最大值**：硬 max 会让 CMA-ES 只看到最差的那一帧，其余帧完全没有梯度信号——动作会「为了救一帧把其他地方搞坏」。

```python
def soft_max(errs, beta=10.0):
    e = [beta * x for x in errs]
    m = max(e)
    return (m + math.log(sum(math.exp(v - m) for v in e))) / beta
```

`beta=10` 是默认值（`[经验]`）；`beta→∞` 退化成硬 max，`beta→0` 退化成均值。

---

## 2. 谓词表

### A. 末端位置类

| 名称 | 参数 | 误差公式 | 时间语义 | 默认权重 |
|---|---|---|---|---|
| `hand_near_anchor` | `side`, `anchor`, `tol_mm=40` | `err = max(0, (d - tol)/scale)`，`d = ‖palm - anchor‖` | `at_any` | 1.5 |
| `hand_at_point` | `side`, `target_m`, `tol_mm=30` | 同上，target 为世界坐标 | `at_end` | 1.5 |
| `hand_above_head` | `side`, `margin_mm=0` | `err = max(0, ((head_top.z - palm.z) + margin)/scale)` | `at_any` | 1.0 |
| `hand_below_waist` | `side`, `margin_mm=0` | `err = max(0, ((palm.z - hips.z) + margin)/scale)` | `throughout` | 0.8 |
| `foot_at_point` | `side`, `target_m`, `tol_mm=25` | 同 `hand_at_point` | `at_end` | 1.2 |
| `tail_tip_at_anchor` | `anchor`, `tol_mm=60` | 同 `hand_near_anchor` | `at_any` | 0.5（表达力，非硬判据） |

**`hand_near_anchor` 是 D1 的旗舰谓词**：`hand_near_anchor(side="right", anchor="mouth", tol_mm=40)` 就是「手到嘴边」。上一代替 LLM 写角度偏 92 mm，写这个 targets 偏 0.5 mm（184 倍）——所以它必须存在且必须准。

### B. 朝向类

| 名称 | 参数 | 误差公式 | 时间语义 | 默认权重 |
|---|---|---|---|---|
| `head_tilt` | `deg`, `tol_deg=6` | `err = max(0, (|angle - deg| - tol)/scale)`，angle 为头绕前后轴倾角 | `at_end` | 0.8 |
| `head_yaw` | `deg`, `tol_deg=8` | 同上，绕竖直轴 | `at_end` | 0.8 |
| `head_nod` | `deg`, `tol_deg=6` | 同上，绕左右轴 | `at_end` | 0.8 |
| `gaze_toward` | `anchor`, `cone_deg=20` | `err = max(0, (angle_to_anchor - cone)/scale)` | `at_any` | 1.0 |
| `torso_lean` | `deg`, `tol_deg=5` | 躯干前倾角与目标差 | `at_end` | 1.0 |

**角度差的定义**：`|angle - deg|` 里 `angle` 是从 `base_rest` 起算的**绝对角度**（D10：每帧重建，不是增量）。

### C. 支撑与接触类

| 名称 | 参数 | 误差公式 | 时间语义 | 默认权重 |
|---|---|---|---|---|
| `foot_grounded` | `side`, `tol_mm=35` | `err = max(0, (sole_z - ground_z - tol)/scale)` | `throughout` | 2.0 |
| `foot_lifted` | `side`, `min_mm=40` | `err = max(0, ((min_mm - (sole_z - ground_z)))/scale)` | `at_any` | 1.0 |
| `dual_support` | `min_sides=2` | 同时着地的脚数不足时按差 1 只记 err | `throughout` | 1.5 |
| `no_ground_penetration` | `side`, `tol_mm=3` | `err = max(0, ((ground_z - sole_z) - tol)/scale)` | `throughout` | 2.5 |

**`foot_grounded` 的阈值依赖步态模式**（run 的腾空相双脚离地是正常的）：

```
tol_mm = 35            if G.duty > 0.5     # walk 档
tol_mm = +∞（跳过）    if G.duty < 0.5 且 phase ∈ 腾空段
```

阈值档写在 `bounds.json` 的族里（第 1 份文档），**不是谓词自己写死**。

### D. 空间约束类

| 名称 | 参数 | 误差公式 | 时间语义 | 默认权重 |
|---|---|---|---|---|
| `no_self_collision` | `tolerance` | 胶囊近似：`err = max(0, (depth - allowed)/scale)`，depth 来自 `capsules.json` | `throughout` | 2.5 |
| `inside_workspace` | `box_m` | 任一末端出界时按越界距离记 err | `throughout` | 1.5 |
| `torso_upright` | `max_deg=35` | 躯干偏离竖直超过 max 时记 err | `throughout` | 1.0 |

**`no_self_collision` 用胶囊，不用 BVH**——BVH 是 L3 的事（0.5–2 s），求解循环里用不起。

**覆盖范围只有 21 主骨之间**。二次骨（hair 60 / skirt 64 / tail 4）不进求解循环（HANDOFF §8.5 硬约束 1），所以「头发插进腿」「裙摆穿过手臂」这类要靠 L3 的 BVH 抓（第 13 份）。**这是有意的分工，不是遗漏**——它决定了「求解阶段能过、L3 阶段被拒」是正常路径。

### E. 动态类（单帧与帧间）

| 名称 | 参数 | 误差公式 | 时间语义 | 默认权重 |
|---|---|---|---|---|
| `max_deg_per_frame` | `budget=12` | `err = max(0, (Δθ_max - budget)/scale)` | `throughout` | 3.0 |
| `no_big_step` | `budget_mm=50` | 任一末端单帧位移超预算 | `throughout` | 3.0 |
| `no_micro_jitter` | `min_deg=0.05` | 末帧之间几乎不动（僵住）时记 err | `at_end`（反向） | 0.5 |

`max_deg_per_frame` 和 `no_big_step` 权重最高——**它们直接对应旧项目最大的两个症状**（176 mm 瞬移、29.44°/帧）。

### F. 轨迹类（整段动作的质量）

| 名称 | 参数 | 误差公式 | 时间语义 | 默认权重 |
|---|---|---|---|---|
| `foot_slip` | `tol_mm=3` | 支撑相内脚底世界位移超过 tol | `throughout` | 2.5 |
| `jerk_rms` | `budget` | 三阶差分均方根超预算 | `throughout` | 0.5（自然度，非硬判据） |
| `duty_match` | `target_duty`, `tol=0.08` | `|实测 duty - target|` 超容差 | `throughout` | 1.5 |

**`jerk_rms` 是「自然度」的唯一可量化代理**（HANDOFF 说别走 VLM）。它不设通过线，只记录和趋势。

---

## 3. 依赖关系

```
predicates 依赖：
  ├─ fk.py            全部位置类
  ├─ anchors.json     全部 *_anchor / gaze_toward
  ├─ capsules.json    no_self_collision
  ├─ contact.py       foot_grounded / foot_lifted / foot_slip
  └─ bounds.json 的族阈值档   foot_grounded 的 tol 随 duty 变
```

**谓词不许调 bpy**。全部走 `fk.py` 的纯计算结果。这条是 D9 的延伸——调 bpy 的谓词会让 L2 求值退化成 1.6 ms/次。

---

## 4. 合成与判决

谓词列表 → 两个输出：

```python
# 用于搜索引导（有梯度）
total_err = Σ(weight_i × err_i) / Σ(weight_i)

# 用于判决（严格）
passed = all(err_i == 0 for i in predicates)
```

**两者必须分开**。用 `total_err` 当通过判据会导致「几乎都满足就算过」；用 `passed` 当搜索目标会导致梯度全 0（布尔没梯度）。

---

## 5. 验收

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 故意造穿地 5 mm | `no_ground_penetration.err` > 0 | `scale` |
| 故意造双脚离地 60 mm（walk 档） | `foot_grounded.err` = 1 | `tol_mm` |
| 单一帧挪 30° | `max_deg_per_frame.err` = 1 | `budget` |
| 造一个「慢动作」 | `jerk_rms` 低，但 `no_micro_jitter` 可能触发 | `min_deg` |
| 两帧间几乎不动 | `no_micro_jitter.err` > 0 | `min_deg` |
| `hand_near_anchor` 用手贴嘴 | `err == 0`，`detail.d_mm < 5` | 锚点 + `tol` |

**坑 #3「判据先自检」的要求**：这 6 条必须写成 `tests/test_predicates.py`，在**任何参数改动后**重跑。谓词自己不先证伪，「全部通过」就没有意义。

---

## 6. 风险

1. **`throughout` 的 beta 太大会退化成硬 max**——表现为「动作为了救一帧而整体变形」。调 beta 时同时看 `jerk_rms`。
2. **`no_self_collision` 的胶囊是保守近似**：假阳性会浪费求解预算。失败样本攒够再上「每骨两半径」。
3. **权重没有科学依据**，全是 `[经验·待校准]`。调权重的唯一依据是「总 err 下降但通过率下降」——说明权重把搜索带偏了。
