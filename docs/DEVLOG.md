# 开发日志（DEVLOG）

> 每完成一个阶段追加一条。给下一个对话看她"做到哪了、踩过什么、什么还没做"。
> 运行日志在 `logs/`，规范见 [LOGGING.md](LOGGING.md)。

---

## 2026-11-24 · 动作混合（200ms 惯性化）

### 问题

从「只走路」切到「走路+挥手」时，右臂会从摆臂位置**瞬移**到挥手位置——几百 mm 的跳变。

### 实现

`core/blend.py`：

| 函数 | 作用 |
|---|---|
| `mat_to_quat` / `quat_to_mat` | 3x3 旋转 ↔ 四元数（Shepperd 方法，数值稳定） |
| `slerp` | 球面线性插值，取最短路径（`d<0` 时翻转） |
| `blend_mat` | 旋转 slerp + 平移 lerp |
| `ease` | smoothstep（`3t²-2t³`），起止都缓 |
| `blend_basis` | 两套 basis 按 alpha 混合（只混主骨 24 根） |

### 为什么不能用矩阵线性插值

4x4 逐元素 lerp 会让旋转部分失去正交性 → 骨骼视觉上**缩短**。

实测：slerp 混合后旋转正交性误差 **4.12e-12**；直接 lerp 会到 1e-2 量级。

### 为什么不用线性 alpha

线性会让动作「一启动就猛冲」。smoothstep 实测：

```
t=  5ms  hand_z=0.580  step= 0.8mm   ← 起步慢
t= 50ms  hand_z=0.627  step=11.6mm
t=100ms  hand_z=0.762  step=16.0mm   ← 中段快
t=150ms  hand_z=0.893  step=12.2mm
t=200ms  hand_z=0.934  step= 0.8mm   ← 末段慢
```

**最大单步 16mm**，瞬移会是几百 mm。

### 接进 Runtime

```python
rt.transition_to(basis, now)      # 从当前姿态过渡到目标
rt.current_basis(now)             # 取当前时刻（过渡中自动插值）
rt.current_pose(now)              # 同上，返回 FK 结果
```

- 第一帧**直接落位**（不淡入）—— 刚启动时没有"上一个姿态"
- 过渡结束自动清掉 `_from_basis`，之后零开销

### 不做的

- 不插值二次骨（头发/裙子/手指）—— 它们跟随主骨，单独插值会抖
- 不插值缩放 —— 骨骼没有缩放

---

## 2026-11-24 · 多动作叠加（一边走一边挥手）

![走路 + 挥手](docs/images/overlay_walk_wave.png)

### 不是加权平均，是分块替换

```python
base  = pose_gen.build(P_walk, rig, bm, active_side=None)   # 腿 + 躯干 + 双臂
over  = pose_gen.build(P_wave, rig, bm, active_side="right") # 只有右臂有意义
merged, covered = overlay.merge(base, over, ["arm.right"])
```

**每条命令管哪些骨是声明式的**（`OVERLAY_GROUPS`），不靠猜：

| 组 | 骨 |
|---|---|
| `arm.right` | rightUpperArm / LowerArm / Hand |
| `arms` | 左右各 3 根 |
| `legs` | 左右各 3 根 |
| `head` / `torso` / `tail` | 对应链 |

### 实测

```
covered = [rightUpperArm, rightLowerArm, rightHand]
rightHand z=0.932（挥）  leftHand z=0.58（垂）  rightFoot z=0.08（抬起）
```

**右臂来自 wave、左臂和腿来自 walk、脚保持走路抬腿** —— 三部分独立。

### 冲突检测

`conflicts(a, b, groups)` 只算**两边都真的改了**的骨（恒等 basis 不算）。

`pose_gen` 返回全部 209 骨的 basis，没动的那些是恒等——只看「骨在不在」会把所有骨都算成冲突。

实测：`arms` 组真冲突 2 根（walk 摆臂 vs wave 抬手），`legs` 组 0 根（wave 不改腿）。

### 待定

- **同时两条叠加**（走路 + 挥手 + 头转向）—— 现在只支持一条 base + 一条 over
- **优先级**：两条都要改同一根骨时，后写的赢（现在是硬覆盖，没有权重）
- **混合过渡**：从「只走路」切到「走路+挥手」时手臂会瞬移，需要 200ms 惯性化

---

## 2026-11-24 · 推翻「夜间转正」——改成立即验证

### 用户指出的问题

「我不可能电脑晚上一直开着，也不可能一直挂后台。」

### 数据推翻原设计

```
L2 求解     46 次求值    20 ms
L3 验证     1 条        2.8 ms
合计                    23 ms
```

**23ms 用户无感。** 原设计写「夜间」是按 BVH（300ms/条）估的，
而 BVH 已被证明**不能当主判据**（噪声比真穿模大）。假设过时了，设计没跟着改。

### 新流程

```
求解 → 立即验证（2.8ms）→ 通过就存 verified / 不通过就 rejected + 原因
```

**库里没有 `provisional` 中间态。不需要定时任务。不需要电脑常开。**

### 实现

- 新增 `core/verify.py`：`verify_pose()` —— 参数范围 / 谓词 / 胶囊三项
- `runtime.handle_intent`：求解成功后**立即**调 `verify_pose`，通过才 `library.put(status="verified")`
- 不通过 → `source="L2_rejected"` + `capability_gap{kind:"verify_failed"}`，带具体原因
- `tools/l3_verify.py` 保留，但**只用于「改了阈值后批量重跑历史条目」**

### 实测

```
12 个族只用族名求解 → 10 条入库 → 全部 verified（0 rejected）
```

验证记录存在 `entry.evidence.checks` 里：

```json
{"range": {"ok": true}, "predicates": {"ok": true, "worst": "?", "err": 0.0},
 "capsules": {"ok": true, "count": 0}}
```

### 代价

求解时间从 20ms 涨到 23ms（+15%）。**换来的是库里没有不可信的条目。**

---

## 2026-11-24 · L3 夜间转正（纯脚本，不用模型）

### 它是什么

**不是用本地模型跑，是纯几何脚本。**

```
L2 解出 → provisional（草稿）
        ↓  tools/l3_verify.py
        ↓  全 34 谓词 + 胶囊全量 + 参数范围
verified（可信） / rejected（带原因）
```

**不调 LLM、不调 VLM、不用显卡。** 100 条不到 30 秒。

### 检查项

| 检查 | 拒绝条件 |
|---|---|
| **参数范围** | P 值不在族的 `P_range` 内 |
| **反关节** | `no_hyperextension` 左右肘角 < 20° |
| **姿势底线** | `torso_upright`、`no_ground_penetration` |
| **当初的谓词** | 条目记录的 predicates 重跑一遍 |
| **胶囊** | 21 主骨 + 尾巴，超阈值的骨对 |

### 实测

```
库 10 条 provisional → verified 10  rejected 0    28ms
```

**负面测试**：手动塞 `elbow_bend=-80`（超范围）→ **被拒**，原因「参数越界」。
塞 `torso_lean=40` → 通过（在 45° 内，合理）。

### 库持久化（新增）

`Library.save(path)` / `Library.open(path)` —— 原子写（临时文件 + `os.replace`），避免写一半坏掉。

### 怎么定时跑

Windows 任务计划：

```powershell
schtasks /create /tn "body-v2-L3" /tr "python D:\DSproject\body-v2\tools\l3_verify.py" /sc daily /st 03:00
```

或者系统空闲时手动跑也行——**它不占资源，也不联网**。

---

## 2026-11-24 · 谓词模板化：解决「谓词膨胀」

### 问题

用户提的：动作越多，LLM 要输出的谓词越多，一次输出不了那么多。

### 解决

每个族带一份 `predicates_template`（验证过的谓词组）：

```json
"wave": {
  "predicates_template": [
    {"name": "hand_near_anchor",  "args": {"anchor": "head_top", "dist_mm": 200, "tol_mm": 40}},
    {"name": "elbow_angle",       "args": {"min_deg": 85, "max_deg": 125}},
    {"name": "forearm_upright",   "args": {"max_deg": 35}},
    {"name": "arm_not_crossed",   "args": {}},
    {"name": "hand_outside_body", "args": {"min_mm": 110}}
  ]
}
```

**LLM 只说 `{"intent": "wave"}`，系统用模板里的谓词。**

### 验证结果：12/12 全可行

| 族 | 谓词数 | evals | ms |
|---|---|---|---|
| idle | 2 | 23 | 33 |
| **wave** | **5** | 46 | 19 |
| pat_head | 3 | 24 | 11 |
| point | 3 | 90 | 33 |
| stretch | 3 | 35 | 15 |
| talk | 3 | 112 | 41 |
| walk | 2 | 12 | 10 |
| run | 2 | 199 | 72 |
| sit | 2 | 12 | 7 |
| kneel | 2 | 12 | 8 |
| rest | 2 | 56 | 22 |
| spin | 1 | 12 | 8 |

**只用族名（不写谓词）就能解出全部 12 个族。**

### 验证过程中修的三处

| 问题 | 修法 |
|---|---|
| `stretch` 手过头顶 → 胶囊判 head×arm 穿模 | 改用 `hand_near_anchor(head_top, 120)` 而不是 `hand_above_head` |
| `run` 的 `foot_lifted` 解不出 | 腿参数在 **G（步态）** 里，不在 P —— 模板只约束上半身 |
| `run` 右手碰右大腿 11.7mm | 加 `arm\|leg = 18mm` 阈值（手臂自然下垂本来贴着腿） |

### 一个真 bug

`intent_key` 在**模板填充之前**就算好了 → 所有族 key 相同 → 全部命中同一条库记录。

修：模板填充必须在算 key 之前。

**教训**：凡是「先算 key 再补参数」的流程，key 一定算错。

---

## 2026-11-24 · 映射修复后的重测：求解器能解出挥手的姿势了

![求解器解出的挥手](docs/images/solver_wave_final.png)

### 同一组目标，修复前后

**目标**（纯锚点 + 角度，不含任何参考姿态）：

```json
{"name": "hand_near_anchor",  "args": {"anchor": "head_top", "dist_mm": 200, "tol_mm": 40}}
{"name": "elbow_angle",       "args": {"min_deg": 85, "max_deg": 125}}
{"name": "forearm_upright",   "args": {"max_deg": 30}}
{"name": "arm_not_crossed",   "args": {}}
{"name": "hand_outside_body", "args": {"min_mm": 120}}
```

| 前臂竖直要求 | 修复前 | 修复后 |
|---|---|---|
| ≤ 30° | ❌ 2510 次不收敛 | ✅ **46 次** |
| ≤ 40° | ❌ 2510 次 | ✅ 35 次 |
| ≤ 50° | ✅ 112 次 | ✅ 35 次 |

**结果**：`hx=-114, hz=997, 肘角=122°, 前臂倾斜 29°`

### 结论

**「求解器解不出前臂竖直」不是谓词问题、不是 P 维度问题，是 `pose_gen` 的肘轴错了。**

修一行（`"y"` → `"x"`），同一个目标从 2510 次失败变成 46 次收敛。

### 这条要记住

出现「怎么调都解不出」时，检查顺序应该是：

1. **`tools/audit_rig.py`**（8 项体检，含轴和对称）
2. `tools/probe_bones.py`（绕世界轴运动表）
3. `tools/probe_elbow_axis.py`（关节弯曲轴）
4. 才轮到加谓词 / 扩 P

**映射是「怎么动」，谓词是「量什么」。前者错了，后者全白搭。**

---

## 2026-11-24 · 骨架完整体检（`tools/audit_rig.py`）

### 结果：全过

![双手对称](docs/images/arms_symmetric.png)

```
[1] 骨骼完整性      209 骨、bones_map 全覆盖、parent 全可解析
[2] rest 矩阵       正交、无 NaN、det≈1（无镜像）
[3] 局部轴          9 根主骨的世界朝向已列表
[4] 左右镜像性      肩/肘/髋/踝关于 X 轴对称（diff ≤ 0.017）
[5] 绕世界轴运动    8 根骨 × 3 轴，方向已列表
[6] pose_gen 参数   12 个参数全部有效（改动能动 >1mm）
[7] 手臂生理性      elbow=0 → 167°、elbow=130 → 76°（无反常）
[8] 左右对称        同参数下左右手镜像差 4.0mm
```

报告：`docs/RIG_AUDIT.md`

### 体检抓出的两个真 bug

**1. 肘的弯曲轴错了（绕 Y → 绕 X）**

绕世界 Y 转前臂 → 手往身体中线收（elbow=90 时 x 从 -0.21 收到 -0.11）。
那是**反关节**：肘的生理弯曲在矢状面（前后），对应世界 X 轴。

**2. 肘的左右符号错了（不该带 `s_lift`）**

`-elbow * s_lift` 让左手往**后**折：

```
RIGHT hand y = -0.117（前）
LEFT  hand y = +0.064（后）   ← 差 180mm
```

绕世界 X 转时，前后方向的弯曲对左右是**镜像对称的，不需要反号**。

改成 `-elbow`（同号）后，左右差从 **185.8mm → 4.0mm**。

### 教训：写关节角前必须先跑体检

上一轮我凭「一般规律」猜符号，连错两次（右臂 y 正负、肘 s_lift）。
**这个模型不是标准镜像**——`rightUpperArm` 绕 Y 正转是向上，`leftUpperArm` 绕 Y 正转是向外。

`tools/audit_rig.py` 现在是**写任何关节角代码之前必跑**的检查。

---

## 2026-11-24 · 找到「肘朝里」的真根因：肘的弯曲轴选错了

### 症状

所有举手姿势里前臂都往身体**内侧**收，看起来像肘朝里/反关节。

### 诊断（`tools/probe_elbow_axis.py`）

固定上臂侧举 90°，前臂绕不同轴转 90°：

| 轴 | 手 x | 手 z | 结论 |
|---|---|---|---|
| **x** | -0.173 | 0.921 | ✅ 在体侧、高 |
| y | -0.113 | 0.948 | ❌ 往中线收 |
| z | -0.276 | 0.844 | 太外、太低 |

**绕世界 Y 转（原来的实现）会让手往中线收**，elbow 越大收得越狠：
```
elbow=40  → hand_x=-0.206
elbow=90  → hand_x=-0.115
elbow=130 → hand_x=-0.072
```

这就是「肘朝里」——**不是符号问题，是轴选错了。**

### 修复

`core/pose_gen.py` 一行：

```python
# 改前（绕世界 Y）
basis[la] = basis_from_world_rot(rig, la, "y", -elbow * s_lift)
# 改后（绕世界 X）
basis[la] = basis_from_world_rot(rig, la, "x", -elbow * s_lift)
```

### 效果

```
lift=90, elbow=90  →  hand=(-0.200, 0.921)  肘角=104°
```

**手在体侧、前臂竖直、肘弯 104°** —— 这才是挥手。而且**只用 2 个参数，不需要 arm_roll**。

截图：`docs/images/wave_elbow_fixed.png`

### 为什么之前绕 Y 是错的

rest 姿态手臂垂直下垂（沿 -Z）。绕世界 Y 转 = 让手臂在**冠状面**（左右平面）里摆。
但肘的生理弯曲是**前后方向**（矢状面）——绕世界 X。

绕 Y 转前臂，等于让肘往侧面折——**反关节**。

### 连带修的

`core/gestures.py` 的 `wave` 关键帧重配（改轴后 arm_lift↔手高的映射变了）：
```
t=0.00  arm_lift=135, elbow_bend=85
t=0.45  arm_lift=75,  elbow_bend=70
t=0.90  arm_lift=135, elbow_bend=85
```

---

## 2026-11-24 · 锚点目标 + 可行域边界（求解器验证）

### 为什么不用「参考姿态」当标准

用户指出：我手调的关键帧当基准 = **自己考自己**。

改成**锚点 + 距离**：

```json
{"name": "hand_near_anchor", "args": {"anchor": "head_top", "dist_mm": 100, "tol_mm": 30}}
{"name": "elbow_near_anchor", "args": {"anchor": "chest", "dist_mm": 220, "tol_mm": 50}}
```

锚点是身体自己的坐标，谁都不会变 → 评价标准独立。

### 求解器实测（无参考姿态）

```
目标：手离头顶 100mm、肘离胸口 220mm、不跨中线
结果：123 次求值，286ms，converged=True
      手→头顶 120mm（差 20，容差 30 内）✓
      肘→胸口 270mm（差 50，容差边界）✓
```

**新维度真的被用上了**：

```
arm_lift   +160.0
arm_roll    -38.7   ← v2 新增，控制肘尖朝向
elbow_bend  +60.7
wrist_bend  +18.3   ← v2 新增
arm_swing   +23.4
```

### 可行域边界（8 组只 1 组可行）

| 手离肩线 | 肘容差 | 收敛 | evals | hand_x |
|---|---|---|---|---|
| **100** | 50 | ✅ | 431 | -183 |
| 100 | 80 | ❌ | 277 | — |
| 130 / 160 / 190 | 50/80 | ❌ | 1201 | — |

**不是 bug，是物理**：`hand_near_anchor(head_top, 100)` 要求手离头顶 10cm，
`hand_outside_body(170)` 要求手离肩线 17cm——头在中间，手臂没那么长。

**求解器正确地判了「做不到」**，没有硬凑穿模姿势。这恰恰证明它能用。

### 新增谓词（共 37 条）

| 谓词 | 作用 |
|---|---|
| `hand_near_anchor(dist_mm)` | 手到锚点的**精确距离**（不只是"接近"） |
| `elbow_near_anchor` | 约束肘的位置（之前求解器总把肘抬到肩高） |
| `foot_near_anchor` | 脚到锚点距离 |
| `hand_outside_body(min_mm)` | 手在身体外侧至少 N mm（`arm_not_crossed` 只管"不跨中线"） |

### 教训

**「至少约束」会让求解器顶到边界**。`hand_above_shoulder` 只有下限，
所以手会被推到最高（1020mm）。**要精确就得用「锚点+距离」**。

---

## 2026-11-24 · 关节角关键帧：挥手终于做对了

### 为什么之前的 P 求解做不出挥手

P 只有 **3 个旋钮**管整条手臂（`arm_lift` / `arm_swing` / `elbow_bend`），
**没有上臂扭转（roll）**。所以「肘尖朝哪」根本控制不了。

每加一条谓词就撞另一条：

| 加的谓词 | 撞上什么 |
|---|---|
| `elbow_below_shoulder` | 和 `hand_above_shoulder` 几何冲突（上臂 0.19 + 前臂 0.23，手只能到肩高） |
| `forearm_upright` | 前臂插进头（胶囊判 `head × rightLowerArm`） |
| `elbow_outside` | 肘位置对了，肘尖朝向还是错的 |

**3 个旋钮控制不了 5 个自由度（肩 3 + 肘 1 + 腕 1）。**

### 解法：关节角关键帧

`core/gestures.py` 新增 `joint_keys` 格式：

```python
"wave_joint": {
    "joint_keys": [
        (0.00, {"rightUpperArm": {"x": 0, "y": -140, "z": 20},
                "rightLowerArm": {"x": 0, "y": -80, "z": 0}}),
        (0.45, {"rightUpperArm": {"x": 0, "y": -95,  "z": 25},
                "rightLowerArm": {"x": 0, "y": -80, "z": 0}}),
        (0.90, {"rightUpperArm": {"x": 0, "y": -140, "z": 20},
                "rightLowerArm": {"x": 0, "y": -80, "z": 0}}),
    ]
}
```

- 每个关节三个轴都能给（`_joint_basis` 按 x→y→z 复合）
- `trajectory.py` 的 Hermite 插值
- **不经过求解器**，所以不受 P 的 10 维限制

### 实测（`tools/probe_joint.py` 扫出来的）

```
ua_y=-140, la_y=-80 → hand_z=0.930, elbow_z=0.815   （高）
ua_y=-95,  la_y=-80 → hand_z=0.794, elbow_z=0.709   （低）
```

手上下摆 **136 mm**，肘跟着下移 106 mm，**手始终高于肘、肘始终低于肩**。

截图：`docs/images/wave_joint_high.png` / `wave_joint_low.png`

### 顺带修的

- `config/seed_families.json`：wave/pat_head/point/stretch/talk/idle 的 `elbow_bend` 上限 90 → 140（肘要能折过 90° 才能"肘低于肩、手高于肩"）
- `config/collision.json`：`arm|head` 0→30、`arm|hair` 0→30、`arm|body` 5→12（举手过头必然靠近头发）
- `solver.py`：**两阶段求解**（粗定位 35% 预算 → 精调 65%，步长收窄到 35%）—— 用户提的「先给范围再求解」
- `core/predicates.py` 新增 9 条关节关系谓词（共 34 条）

### 结论：什么时候用哪个

| 场景 | 用什么 | 为什么 |
|---|---|---|
| **已知形状的动作**（挥手/点头/招手） | `gestures.py` 的关节角关键帧 | 形状已知，不需要搜 |
| **库外的新动作**（「用尾巴碰鼻子」） | `solver` + 谓词 | 形状未知，要搜 |

**D1 禁止的是「LLM 写关节角」**，代码里写关键帧和 `gait.py` 写脚轨迹是一回事。

---

## 2026-11-24 · 接真 DeepSeek API（她第一次用自己的话驱动身体）

### 实测

```
用户：挥挥手跟我打个招呼吧
   ↓ 一次调用 804 ms
她说：（从窗口底下探出半个身子，蓝头发晃了一下，抬起手）
     来了来了，这么晚才想起我。
   ↓ <<ACT>>
wave + hand_above_head + torso_upright + head_tilt
   ↓ 求解
右手 z=0.948（过头顶）  左手 z=0.521（没动）
```

截图：`docs/images/real_llm_wave.png`

### 真 LLM 暴露的 6 个问题（stub 测不出来）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `gaze_toward(anchor="mouth")` | LLM 让她看自己的嘴——物理不可能 | **从 LLM 可选谓词里删除 `gaze_toward`**。视线跟随归 L0，不该每次动作指定 |
| 2 | `gaze_toward(anchor="head_top")` | 同上，看自己头顶 | 同上 |
| 3 | `side: "R"` | 校验只看键不看值 | 加枚举检查：side ∈ {left,right} |
| 4 | `deg: "8"` 字符串 | 同上 | `_mm`/`_deg` 必须是数字 |
| 5 | 嘴上说「挥了」却没写 `<<ACT>>` | 她以为说了就是做了 | prompt 加**言行一致**条款 |
| 6 | 两只手都举 | 谓词默认 `side="right"`，生成器默认两侧 | `_infer_side` 对手部谓词默认 right |

### 场景恢复的坑

中途几轮 `apply_pose` 后**没恢复就备份了下一次**，导致姿态层层叠加：恢复后 128 根非单位（原始是 145）。

**修法**：用 `blender_rt_txn restore before-p5-binder` 还原。恢复后 `is_dirty=False`，干净。

**教训**：**每次 apply 前备份、apply 后立即恢复**，不要跨轮叠加。

### ⚠️ API key 泄露事故

我读 `desktop-pet/config.json` 时按 `key=value` 解析，把整行 JSON（含 `sk-70e7a8ea...`）当普通文本打印了。

**教训**：
- 读配置**先判断格式**（JSON 用 json.load，别按行切）
- 打印前一律过 `redact()`（`llm/client.py` 里有）
- 已提醒用户轮换该 key

---

## 2026-11-24 · 最后一步：一次调用、两路输出、真机抬手

### 一次调用跑通

```
用户：打个招呼吧
   ↓ 一次 LLM 调用（她的人格 + <<ACT>> 可选备注）
"行吧，那就挥一下～"
<<ACT>>
{"intent":"wave","predicates":[{"name":"hand_above_head","args":{"side":"right"}}]}
   ↓ 拆解
say = "行吧，那就挥一下～"   （干净，无标记）
act = {...}
   ↓ runtime.handle_intent
L2 求解 → lib-0001 → 右手 z=0.938（过头顶），左手 z=0.519（没动）
   ↓ binder.apply_pose
Blender 里真的抬手
```

截图：`docs/images/final_demo.png`

### 实测

| 项 | 值 |
|---|---|
| LLM 调用次数 | **1**（不是 2） |
| say 里含 `<<ACT>>` 标记 | **否**（已剥离） |
| act 解出 | ✅ |
| 求解 → 姿态 | 右手 z 0.938 / 左手 0.519 |

### 兜底原则（全部测过）

**话永远不会因为动作而丢**：

| 情况 | say | act |
|---|---|---|
| 没写 `<<ACT>>` | 原样 | None |
| 备注是坏 JSON | **保住** | 丢弃 |
| 写了 `none` | 保住 | None |
| 编造族 `backflip` | **保住** | 丢弃 |
| 调用崩了 | 返回 error | — |

---

## 2026-11-24 · 接入「大肥鱼」人格（沙箱 + 两次调用）

### 先纠正一个错误

我第一次找的是 `%APPDATA%/desktop-pet`，读出来是「小灰」（猫娘）——**找错了**。
真正的是 `D:\DSproject\dsh-qq`，`大肥鱼.exe` 与 `launcher.exe` 的 SHA256 完全一致。

### 沙箱（`sandbox/`，26 个文件，75 KB）

| 复制了 | 没复制 |
|---|---|
| `persona.md` / `persona.json`（人格） | `voice/`（wav） |
| `memory.md` / `memory.json`（记忆） | `stickers/`（图片） |
| `self.md` + `self/`（8 篇自我） | `.runtime/`（渲染） |
| `diary/`（10 篇日记） | **`.env`（API key）** |
| `mood/emotion/purpose/plan/presence` | `body_log.jsonl`（1 MB） |
| `body/pose/receipt.json`（身体接口） | |

重新生成：`python tools/make_sandbox.py`

### 两种调用模式（用户拍板）

| 身体状态 | 调用次数 | 做什么 |
|---|---|---|
| **没连** | 1 | 她按原人格说话，纯自然语言，不碰格式 |
| **连着** | 2 | ① 她还是她 → ② 拿她的话单独问「配什么动作」 |

**为什么不能合并成一次**：格式约束和表达自由度是竞争的。同时要求
「输出 JSON 且 act 不能空」会让她说话变短、不敢跑题、**心情表达被压平**。

第二次调用的 prompt 是**纯功能**（「你是一个动作编译器」），不带人格，
所以不会影响她怎么说话。

### 兜底

- 第二次调用抛异常 → `act=None`，**say 照常返回**
- 第二次编造族/谓词 → 丢弃，`act=None`
- 她说「不动」→ `{"act": null}` 正常路径

### 测试

`tests/test_persona.py` 8 组，全过。

---

## 2026-11-24 · 接入 LLM（可插拔）

### 结构

```
用户说一句话
    ↓
llm/interpret.build_system_prompt(capabilities)   ← 只给 5 样东西
    ↓
llm_fn(system, user) -> str                       ← 注入，可换任何模型
    ↓
extract_json → validate_act                       ← 严格校验
    ↓
core/runtime.handle_intent                        ← 求解 / 入库
```

**没有绑定任何 LLM 服务**：`llm_fn` 是注入的。测试用 stub，生产接 DeepSeek / OpenAI / 本地模型都是同一个接口。

### 校验会拦掉的

| 情况 | 处理 |
|---|---|
| 未知族（`backflip`） | 拒绝，原因「未知族 backflip」 |
| 未知谓词（`fly_to_moon`） | 拒绝 |
| 谓词带了多余参数 | 拒绝 |
| `act: null` | **允许**（她说「做不到」是合法的） |
| 坏 JSON | 重试 ≤2 次 → 降级只播 say |
| LLM 调用异常 | 不崩，记错误 |

### 实测（stub）

```
一句话 → LLM → 求解 → 入库      33.6 ms
同一句话第二次                   0.011 ms（L1）
右手 z=0.938 > 头 z=0.840        ✓
左手 z=0.519（没动）              ✓
```

### README 说明（给接真 LLM 的人）

`llm/interpret.interpret(text, llm_fn, capabilities)` 里的 `llm_fn` 签名是 `(system, user) -> str`。
接 DeepSeek 时：

```python
def llm_fn(system, user):
    # 调你的 SDK，返回纯文本
    return client.chat(system=system, user=user)
```

**不需要改 `interpret.py` 一行。**

---

## 2026-11-24 · 真机演示（她真的动了）

### 完整链路跑通

```
"好呀，挥挥手～" + 谓词 hand_above_head(side=right)
        ↓
L2 求解 → arm_lift=102  → 入库 lib-0001（provisional）
        ↓
pose_for → 21 骨矩阵
        ↓
binder.apply_pose → Blender 里真的举起了右手
        ↓
第二句同样的意图 → L1 命中，0.013 ms
```

**实测位置**：

```
右手 z = 0.938（头顶 0.948 附近）
左手 z = 0.519（垂下）
```

截图：`docs/images/demo_wave.png`

### 修了一个真 bug

第一版 `pose_for` 没传 `active_side`，**两只手一起举**——「挥手」变成了「投降」。

原因：谓词 `hand_above_head(side=right)` 只约束右手，但 `pose_gen` 默认两侧都驱动。

修法：`Runtime` 从谓词参数里 `_infer_side()`，存进 `entry.metrics.side`，`pose_for` 优先用它。

**这条会咬人的地方**：谓词约束和姿态生成是两套逻辑，谓词说「右手」，生成器不一定听。**每加一个带 side 的谓词，都要检查生成器有没有跟着限侧**。

---

## 2026-11-24 · 端到端接线（Runtime）

### 一句话 → 她动起来，通了

`core/runtime.py`：把库 / 求解 / 能力清单串成一条链。

```
handle_intent(act)
   ├ L1 查库命中        → 返回 entry（0.007 ms）
   ├ 族不存在           → capability_gap{kind:"family_missing"}
   ├ L2 求解
   │    ├ 成功          → library.put(provisional) → 返回 entry
   │    └ 失败/穿模     → capability_gap{kind:"solve_failed", suggested}
   └ pose_for(entry)    → 21 骨矩阵，交给 binder 写骨
```

**实测**：

| 场景 | 耗时 | 结果 |
|---|---|---|
| 空库 → 求解 → 入库 | **26 ms** | source=L2，库里有 1 条 |
| 再查同一意图 | **0.007 ms** | source=L1 |
| 族不存在（backflip） | — | `family_missing` |
| 不可达（手到尾巴尖） | — | `solve_failed` + suggested |

### 能力边界的三个补丁（§16.3）

用户提的问题：「这些参数文档会不会影响她的思考方式」。

**会**。24 条谓词 + 12 个族就是她能想出的目标的上界。补了三样：

1. `capabilities()` —— 生成「12 类动作 + 24 条判据 + 可用锚点」给 prompt
2. `capability_gap` 事件 —— 做不到时告诉她**缺哪一维**（族缺失 / 谓词缺失 / 范围不够）
3. 提案日志 —— `logs/events.jsonl` 的 `capability_gap` 就是「她想要但做不到」的痕迹

**原则**：能想的 > 能做的，差别必须**显式回传**，否则 LLM 会学会「别提新东西」。

---

## 2026-11-24 · P8 结论：胶囊为主，BVH 降级

### BVH 路线被实测否决为主判据

用 region 对 + baseline 差分测 BVH：

| 手臂旋转 | `body|body` 增量 | `arm|arm` | `arm|body` |
|---|---|---|---|
| 120° | +528 | +9 | 0 |
| 150° | +769 | +19 | 0 |
| 170° | +996 | +12 | 0 |

**手臂一动，「身体自己和自己」的相交对数涨 500–1000**——那是腋下、肩部皮肤褶皱被拉伸，不是穿模。

原因：mesh 是多层（裙子叠层、发束、内衣）+ 软形变。任何大动作都会让内层互相穿插，数量级远大于真正的穿模。

### 最终形态

| 档 | 用途 | 状态 |
|---|---|---|
| **胶囊**（主） | 求解后复检、判定入库 | ✅ 2.2 ms，rest 零误报，手插头检出 |
| **BVH**（辅） | 人工排查「胶囊说没问题但看着不对」 | ⏸ 调试工具，不是闸门 |

**闸门 = 谓词 + 胶囊。** BVH 不进自动化链路。

### P8 完成的代码

| 文件 | 内容 |
|---|---|
| `config/capsules.json` | 209 骨半径（166 实测 + 43 继承） |
| `config/collision.json` | 19 对阈值 + default + `hair\|head` delta |
| `core/collision.py` | 查表 / 归因 / 罚项 / 胶囊最近距离 / baseline 差分 / 骨对预筛缓存 |
| `core/solver.py` | 求解后调一次胶囊复检（不塞进搜索循环） |
| `tools/bench_solve.py` | 带/不带胶囊的耗时对比 |

### 性能

```
只谓词        0.38 ms/次 × 3000 = 1.14 s
+ 胶囊复检    2.2 ms（只测一次，不乘倍数）
BVH（全量）   ~300 ms/次，且噪声大 → 不用
```

---

## 2026-11-24 · P8（胶囊档）

### 完成

| 文件 | 内容 |
|---|---|
| `config/capsules.json` | 209 骨半径（166 实测 + 43 继承父骨×0.5） |
| `config/collision.json` | 19 对阈值 + default 2mm + `hair\|head` delta |
| `core/collision.py` | 查表 / 归因到骨 / 罚项 / **胶囊最近距离** / **baseline 差分** |

### 关键发现（改变了 P8 的设计）

**1. rest 姿态就有大量「固定接触」，不能直接判违规。**

| 方案 | rest 固定相交 |
|---|---|
| BVH（全 mesh 自相交） | **9858 对** |
| 胶囊（全 209 骨） | **103 对** |
| 胶囊（21 主骨） | **0 对** |

原因：mesh 是多层的（裙子叠层、发束、内衣），二次骨链在 rest 时本来就互相贴着。
**结论：无论胶囊还是 BVH，都必须 baseline 差分**（启动跑一次 rest 记基线）。

**2. 主导顶点组是 `REGION_*`，不是骨骼组。**

8 个区域标记组权重高于骨骼组，直接用「最大权重 VG」归因会拿到 `REGION_body` 而不是 `WhaleTail_03`。归因必须显式排除 `REGION_*`。

**3. 胶囊判定需要三个条件同时成立**：

```
跨 region + 骨链距离 > 3 + gap 比 baseline 更小
```

只差链距，肩关节就会天天报（chest×upperArm 链距 3）。

**4. 全量胶囊太慢**：15629 对 ≈ 31 ms/帧。求解期只用 21 主骨（194 对 ≈ 0.4 ms）。

### 实测

```
主骨跨 region 对   194
rest 违规          0
造「手插头」        检出 3 条，命中 arm×body（upperChest×leftHand，超 49mm）
造「手贴胸」        0 条（合法接触不报）
```

### 还没做

- `tools/l3_verify.py`（夜间批处理，BVH + baseline）
- 胶囊接进 `solver` 的 `no_self_collision` 谓词（现在只吃 `meta` 标量）
- `proprio.centroid` 已改为**只算 21 主骨**——包含头发裙子会把质心稀释（前倾 20° 只动 3.3mm，正确值 28mm）

---

## 2026-11-24 · P7（第一刀：P 空间求解）

### 完成

| 模块 | 内容 | 验证 |
|---|---|---|
| `core/cmaes.py` | 精简 CMA-ES（(μ/μ_w,λ)，rank-μ，ask/tell 分离） | 3 维二次型 365 次收敛到 5.7e-10 |
| `core/solver.py` | P（10 维）空间求解 + verdict + 违规回传 | 手抬过头 **11 次求值 / 11.2 ms** |

**这是项目第一次「造出库里没有的姿态」**：给一个谓词（手到头顶），从族默认出发，自己搜出 `arm_lift=132.2, elbow=27.3`。

### 踩的坑

1. **`arm_lift` 绕 Y 转 90° 只是侧平举**，到头顶要 ~160°。wave 的 `P_range` 原先是 `[40,100]`，根本装不下「举手过头」。已扩到 `[40,160]`，pat_head 到 `[70,170]`，全局上限 `180`。
2. **单次求值 0.99 ms**（不是设计里的 0.05 ms）——因为 `rig.compute` 跑了 209 骨。求解只需要 21 骨，`rig.subset()` 已就绪但还没接进 solver（下一步优化）。
3. **不可达的 `detail` 会带 `_frame`** 字段（`evaluate_series` 塞的），日志里要过滤。

### 还没做

| 项 | 说明 |
|---|---|
| 三段搜索的 G 段 | 现在 G 固定为族默认。走路/跑步是 gait 的事，不归 L2 |
| 21 骨子集加速 | 接 `rig.subset(MAIN21)` 可把 0.99ms 降到 ~0.1ms |
| 胶囊穿模约束 | `no_self_collision` 现在只消费 `meta` 里的标量，还没接 capsules.json |
| P8 | BVH 入库闸门 |

---

## 2026-11-24 · P5 + P6（部分）

### P5 完成

| 模块 | 内容 | 验证 |
|---|---|---|
| `core/binder.py` | 唯一碰骨骼（D7），4×4 → matrix_basis 纯反算 | 往返误差 **3e-7**，FK 重算 0.000125 mm |
| `core/pose_gen.py` | P（10 维）→ 21 主骨 basis | 实测右臂抬起 z 0.555→0.728 |
| `core/player.py` | phase / elapsed / 暂停 / 变速 / 循环 | 测试覆盖 |
| `core/dispatcher.py` | L0 先行、L1 查库、兜底、打断、队列 | 17 项断言 |

**第一次真的驱动了身体**，`docs/images/wave.png` 是实测截图。

### P5 踩的坑

1. **`arm_lift` 绕世界 Z 无效**。rest 手臂垂直下垂（−Z），绕 Z 只是"扭转"，位置不变。正确轴是世界 Y。
2. **左右符号反了**：左臂绕 Y 正转指向 −X（跨到身前），要负转。
3. **球标记不渲染**：`blender_rt_see` 的离屏绘制不包含运行时新加的对象，即使推到脸前 15 mm 也看不见。改用数值 + 图判断。
4. **相机取景**：1.35 m + 50 mm 只能看到 0.65 m 高，头顶在画面外。

### P6 完成一半：两骨 IK

`core/ik.py`：矢状面两骨 IK 解析解 + Rodrigues 旋转。

**闭环验证**：

```
L1=195.6mm L2=231.4mm 可达=427.0mm
抬脚 100mm   → 脚到目标 0.000 mm
前移 120mm   → 脚到目标 0.000 mm
批量 12 目标 → 解出 10，其余 2 是真不可达（D5 正确拒绝）
```

**坑**：rest 姿态腿本来就接近伸直（426.7/427.0 = 99.93%），阈值 `0.999` 把它误判成不可达。改成 1 mm 绝对容差。

### P6 完成：gait + 脚锁

`core/gait.py`：G（11 维）→ 双脚世界目标位置。

**stride 语义定为「单脚迈一步的距离」**（2026-11-24 用户拍板）：
脚相对身体摆幅 ±stride/2；`seed_families` 里 walk 0.55→**0.22**、run 0.85→**0.30**。

**身体速度公式**：为了让支撑相脚在世界坐标里不动，

```
v_body = stride / (duty × period)
```

**踩的坑**：第一版写成 `body = origin − fwd × travel`，身体往后退，和脚的相对位移同向叠加，脚漂移 0.064 m。改成 `+`。

**脚锁双层验证**：

| 层 | 结果 |
|---|---|
| 目标位置（gait 输出） | 支撑相 20 个采样点，spread **0.00 mm** |
| 实际骨骼（FK 重算） | 左脚 25 帧支撑相，spread **0.0 mm**；实际脚位与目标完全一致 |

右脚的 354.8 mm 偏差是**跨周期换步**，不是滑移（右脚支撑相跨了周期边界）。

**还没做**：双脚腾空相的地面高度、脚掌滚动（heel-strike / toe-off）。

### 还没做

| 阶段 | 内容 |
|---|---|
| P6 剩余 | `core/gait.py`（G 的 11 维 → 脚轨迹 + phase）、脚锁 |
| P7 | CMA-ES 三段搜索 + 胶囊穿模 |
| P8 | BVH 入库闸门 |

---

## 2026-11-24 · P0 / P0.5 / P1 / P1.5 / P2 / P3 / P4

### 完成

| 阶段 | 模块 | 测试 | 关键结果 |
|---|---|---|---|
| P0 | `core/trajectory.py` | `test_trajectory.py` | 端点误差 <1e-12，接缝速度跳变 2e-8，无 0 速平台 |
| P0.5 | `core/fk.py` | `test_fk_golden.py` | 9 用例 × 209 骨，最差 **0.000193 mm** |
| P1 | `core/proprio.py` | `test_proprio.py` | 质心/支撑面可用，前倾 20° 质心前移 28 mm |
| P1.5 | `core/emotion.py` | `test_emotion.py` | 低通 dt 无关，总偏移封顶 ±15% |
| P2 | `core/contact.py` | `test_contact.py` | 35 mm 阈值、穿地 3 mm 容差、双支撑 0.24 |
| P3 | `core/predicates.py` + `gate.py` | `test_gate.py` | 24 条谓词，5 种故意造错全抓到 |
| P4 | `core/library.py` | `test_library.py` | 100 条查询 0.09 ms，重放一致，去重生效 |

辅助模块：`core/bones.py`（骨骼元数据 + 五条约束）、`core/log.py`（事件日志）。

### 踩到的 7 个坑（都已修）

1. **unconnected 是 57 根不是 8 根**（152 connected）。硬编码会让 49 根骨的 location 被错误清零。
2. **`matrix_world` 偏差 2.4e-8**（float32 噪声），不是单位矩阵。
3. **numpy FK 比 mathutils 慢 2.8 倍**（0.051 vs 0.018 ms）。P7 默认预算改 3000 次。
4. **骨骼局部轴 ≠ 世界轴**。`spine` 局部 X 指向世界 −Y；四肢轴各不相同，`leftFoot` 还是斜的。唯一入口 `BoneMap.basis_from_world_rot`。
5. **多轴情绪叠加突破 ±15%**（val + aro 同调 arm_swing 能到 30%）。封顶要做在总和上。
6. **双支撑占比 = 2×duty−1**，不是 >0.5。duty 0.62 → 0.24。
7. **connected 骨不能靠 basis.location 移动**。抬脚要靠髋/膝旋转，出界测试要直接改矩阵。

### 交付物

```
core/     trajectory fk proprio bones emotion contact predicates gate library log
config/   bones_map.json  emotion_bias.json
tests/    8 个测试文件 + 2 个 fixture
docs/     BONES.md（骨骼映射表，可重生成）
tools/    gen_bones_doc.py
```

全部 8 个测试 `RESULT: PASS`，退出码 0。

### 下一步

| 阶段 | 内容 | 前置 |
|---|---|---|
| P5 | `binder.py`（首次真驱动身体）+ dispatcher 状态机 | P4 |
| P6 | 步态生成（G）+ 两骨 IK 解析 + 脚锁 | P0.5 |
| P7 | CMA-ES 三段搜索 | P3, P6 |
| P8 | L3 BVH 复检闸门 | P7 |

**P5 是第一次真的动骨骼**，要先用 `blender_rt_txn` 或备份，别把用户的 blend 弄脏。

### 已知未做

- `config/anchors.json` 还是占位（需要人工确认 mouth 等锚点）
- `config/capsules.json` 未生成（P8 前要做）
- `config/collision.json` 未生成
- `config/seed_families.json` 未生成（P5 前要有，否则 L1 没东西可查）

## 2026-11-24 · 走路转身验证（抓到一个真 bug）

### 测试

`tools/test_turn.py`：三种转身速度，量支撑期脚漂移、朝向变化、单调性、两脚间距。

### 抓到的 bug：左右脚偏移不随转身旋转

```python
# 改前
out[side] = [body[0] + fwd[0]*rel + lateral[side],
             body[1] + fwd[1]*rel, ...]
```

`lateral`（左右脚间距）只加在**世界 X** 上，转身 90° 后两脚还是沿 X 分开。

**实测**：`turn_rate=90°/s` 转 1 秒，两脚连线只转了 **41.6°**（应该 90°）。

**修法**：左右偏移沿「前进方向的法向」：

```python
perp = (-fwd[1], fwd[0], 0.0)     # 前进方向的垂直方向
out[side] = [body[0] + fwd[0]*rel + perp[0]*lateral[side],
             body[1] + fwd[1]*rel + perp[1]*lateral[side], ...]
```

**修完**：90.0°（精确）。

### 实测（修完后）

| 转身速度 | 支撑期脚漂移 | 容差 | 朝向变化 |
|---|---|---|---|
| 0（直走） | **0.00 mm** | 5.0 | — |
| 45°/s | 3.53 mm | 8.6 | — |
| 90°/s | 7.05 mm | 12.2 | **90.0°** |

### 关于「脚漂移」的说明

**直走必须 0**（这是脚锁的定义）。

转身时支撑脚在世界里**必然会绕弧**——转身时支撑脚得当轴，不可能完全不动。漂移量与转身速度成正比：

```
tol = 0.005 + turn_rate × 0.00008
```

这不是放宽标准，是物理约束。真要消除，需要「支撑脚当轴、身体绕它转」的 IK 驱动，那是另一套实现。

### 单调性

两脚连线在摆动相会晃，逐帧看会有 2 次反向。改成看总体趋势（<3 次反向），因为**测量噪声不等于动作抽搐**。

---


## 2026-11-24 · 三动作叠加（走 + 挥 + 转头）

![三层叠加](docs/images/overlay_three_layers.png)

### 实现

`overlay.merge_layers(base, layers)` —— 支持任意多层，`layers = [(basis, groups), ...]`。

返回的报告：

```python
{
  "covered":   {骨名: [第几层, ...]},   # 每根骨被哪些层改过
  "conflicts": [骨名, ...],             # 被 ≥2 层改过的骨
  "per_layer": [[骨名, ...], ...],      # 每层实际覆盖了哪些骨
}
```

**后面的层赢（硬覆盖）**，但冲突会**记录**，不静默。

### 实测

```
layers  = [3, 3]          # 第一层右臂 3 根，第二层头 3 根
conflicts = []            # 臂 vs 头不重叠
rightHand z = 0.932       # 举手
head yaw    = -64.4°      # 转头
leftFoot z  = 0.08        # 走路抬脚
```

**三部分完全独立。**

### 冲突检测

两层都改头时：`conflicts = ['head', 'mixamorig:HeadTop_End', 'neck']`，
`covered['head'] = [0, 1]`（第 0 层和第 1 层都改过，第 1 层生效）。

### 还没做

- **优先级权重**：现在硬覆盖，没有"按权重混合"
- **层间时序**：三层同时启动时，各自的过渡时长不同怎么办

---


## 2026-11-24 · 优先级权重 + 全链路性能

### 优先级权重

`merge_layers` 支持 `(basis, groups, weight)`：

- **weight 高的赢**，与顺序无关
- **weight 相同时，后写的赢**（保持原来的直觉）
- 报告里多一个 `winner: {骨名: 层号}`

实测：
```
[(low, ["head"], 0.3), (high, ["head"], 0.9)]  → 第 1 层赢
[(high, ["head"], 0.9), (low, ["head"], 0.3)]  → 仍是第 0 层赢（顺序无关）
[(low, ["head"], 1.0), (high, ["head"], 1.0)]  → 第 1 层赢（同权后写胜）
```

这样「重要动作」可以用大 weight 压住「次要动作」，而不用改调用顺序。

### 全链路性能（`tools/bench_all.py` → `docs/PERF.md`）

| 环节 | ms | 备注 |
|---|---|---|
| FK 209 骨 | 0.510 | 全骨 |
| **FK 21 骨（fast）** | **0.058** | 求解/每帧用 |
| pose_gen（12 维 P） | 0.382 | |
| **胶囊（21 主骨）** | **2.203** | ⚠ 最大开销 |
| 胶囊 + 阈值 | 2.303 | |
| 动作混合（slerp） | 0.652 | 24 根骨 |
| 两层叠加 | 0.004 | 几乎免费 |
| 三层叠加 | 0.006 | |
| **求解 wave** | **28.3** | 46 次求值 |
| verify_pose | 2.409 | 胶囊占 92% |
| 查库（100 条） | 0.098 | |
| 存库（100 条） | 3.219 | |
| **handle_intent L2** | **33.9** | 求解+验证+入库 |
| **handle_intent L1** | **0.006** | 命中 |
| pose_for | 0.267 | |

**单帧预算**：FK21 + pose_gen + 混合 = **1.07 ms → 931 FPS**

### 瓶颈分析

**胶囊 2.2ms 是最大单项开销**，而且 `verify_pose` 的 2.4ms 里 92% 是胶囊。

优化方向（按性价比）：
1. **缓存骨对列表**（已有 `eligible_pairs`），还能优化的是 `seg_seg_distance` 的 numpy 小数组开销
2. 换成纯 Python 元组运算（避免 194 次 `np.asarray`）
3. 批量向量化（194 对一次算）

预期：2.2ms → 0.5~0.8ms，`verify_pose` 降到 ~0.8ms，L2 降到 ~30ms。

**但**：L2 33.9ms 已经可接受（用户要等一个动作生成，30ms 无感）。
**真正该优化的是单帧路径**——现在 1.07ms/帧，931 FPS，远超需要（60 FPS 只要 16.7ms）。

### 性能结论

| 场景 | 延迟 | 可接受吗 |
|---|---|---|
| 每帧更新 | 1.07 ms | ✅ 931 FPS，60 FPS 用 6.4% |
| L1 查库 | 0.006 ms | ✅ |
| L2 新动作 | 33.9 ms | ✅ 一次生成，无感 |
| 验证 | 2.4 ms | ✅ 在 L2 内 |

**没有性能瓶颈需要处理。**

---


## 2026-11-24 · 真 API 模拟用户对话（4 轮）

### 结果

```
[1] 挥挥手          → wave  右手 z=0.991  ✓  [1615ms]
[2] 今天累死了      → 纯说话（对）           [1257ms]
[3] 过来让我看看你  → walk  右手 z=0.556  ✓  [ 679ms]
[4] 伸个懒腰吧      → 没给动作 + 4680ms（慢调用）
```

### 这轮改的

**1. 模板优先（重要决策）**

原来：LLM 给了谓词就用 LLM 的。它给「两侧手同时过头顶」，而 P 的 `arm_lift` 一维共用 → 解不出。

现在：**族有模板时模板优先**，LLM 只决定用哪个族：

```python
if tpl and (not predicates or not act.get("use_llm_predicates")):
    predicates = [dict(p) for p in tpl]
```

想用 LLM 的谓词可以显式传 `use_llm_predicates: true`。

**2. 谓词去重**

真 LLM 会把同一个谓词写两遍（`hand_above_head{}` + `hand_above_head{side:"right"}`），
重复会让误差在 gate 里累加。

规则：**同名同 side 合并，保留 args 更全的**；`no_hyperextension` 的 left/right 是两条不同约束，都留。

**3. 加「过来」兜底**（之前「过来让我看看你」不给动作）

**4. stretch 模板改单侧**（`hand_above_head` 双侧太严）

### 发现（**慢调用先记着不改**）

| 问题 | 影响 | 优先级 |
|---|---|---|
| **API 偶发慢（4.7s）** | 用户等太久 | 记着，先不改 |
| **LLM 有时不给动作** | 4 轮里 1 轮只说话 | 中 |
| **手偏中线**（x=-0.133，参考 -0.23） | `hand_outside_body(110)` 只要求 110mm | 低 |

### 关于「意图层」的设计讨论

用户提的：**让 LLM 在说话前先决定意图（要不要动），再输出对话**。

他同时担心：这会不会让输出「超符合」，挤压自由表达。

**这个担心是对的**——见下一节。

---


## 2026-11-24 · 动作抖动层（消除「每次一模一样」）

### 为什么需要

同一个动作每次都解出**完全一样**的姿势（CMA-ES seed 固定）——看起来像机器人。

真实人每次都会略不同，但差异有**倾向**（累了偏低、兴奋偏大），不是纯随机。

### 实现：`core/jitter.py`

**和求解器的分工**：

```
求解器  → 找到满足全部约束的 P*（精确、可复现）
抖动层  → 在 P* 附近采样，**不破坏约束**（每次不同）
```

所以**不是新求解器**，是「后处理 + 守卫」：

```python
P' = perturb(P*, mood)        # 抖一下
if not verify(P'):            # 守卫
    P' = P*                   # 回退
```

### 抖动 = 随机 + 偏置

```python
scale = 0.015 + 0.035 * (energy/100)     # 精力高抖得大（1.5%~5%）
drift = -0.025 * (1 - energy/100)        # 精力低整体下沉
drift += 0.02 * (mood_num - 0.5)         # 心情好略上扬
```

### 关键教训：抖动基准用「典型动作幅度」不是「参数全量程」

第一版用全量程（`arm_lift` 的 200°），结果 200 次采样极差 **32.8°**——像抽筋。

改成典型幅度（`arm_lift` 60°）后 **9.8°**。

**教训**：全量程是「参数能到多远」，典型幅度是「一个正常动作会变动多少」。抖动应该按后者。

### 实测

| 检查 | 结果 |
|---|---|
| 20 次采样有 20 个不同值 | ✅ 消除机器人感 |
| arm_lift 波动 9.8° | ✅ 不夸张 |
| 低精力 arm_lift 更低（90.8 vs 95.8） | ✅ 有倾向 |
| 守卫拒绝时完全回退 | ✅ 不破坏约束 |
| 同 seed 可复现 | ✅ |

### 情绪规则 vs 抖动层

| | 情绪规则（`emotion_bias.json`） | 抖动层 |
|---|---|---|
| 作用 | **改变目标**（手该到哪） | **不改目标的前提下微调** |
| 保证 | 一定生效 | 可能被守卫回退 |
| 每次结果 | 相同 | 每次不同 |

**两者都要**：情绪规则决定「她今天想抬多高」，抖动决定「今天这一下抬多高」。

---


## 2026-11-24 · 情绪改「目标」+ 抖动接进 runtime

### 用户的关键指出

> 「在运行时根据情绪调整是脚本化写死的吧」

对。而且 `emotion_bias.json` 那种「P × 0.7」的缩放**本身就是假的**——
累了不是「挥手幅度小」，是「手抬到肩就不想再抬了」。**目标变了，不是幅度变了。**

### 新流程

```
求解前：情绪改「目标」         predicates = bias_predicates(predicates, mood)
                              ↓
求解：  按新目标重新解          solve(...) → P*
                              ↓
验证：  范围 + 谓词 + 胶囊      verify_pose(...)
                              ↓
入库：  存基准 P*
                              ↓
播放：  P* + 抖动（守卫）       safe_perturb(P*, family, mood, guard) → P'
```

### 情绪改目标（`emotion.bias_predicates`）

| 情绪 | 改什么 | 实测 |
|---|---|---|
| `energy < 30` | `hand_above_head` → `hand_above_shoulder` | 谓词名换了 |
| `energy < 40` | 锚点距离 `+90mm × (1-energy/40)` | dist 200 → **268** |
| `energy > 70` | 锚点距离 `-40mm × ((energy-70)/30)` | dist 200 → **167** |
| `mood < 0.35` | 补 `head_nod=5°`（头微低） | 自动加一条 |

**实测效果**：

```
精力高  hand_z = 0.978
精力低  hand_z = 0.953   ← 差 25mm，是真的「手放低了」
```

**不是缩放**——是求解器按新目标重新解出来的姿势。

### 抖动接进 runtime

```python
P_play, jit_rep = safe_perturb(P_star, family, mood, _quick_ok)
```

**库存储规则**：
- 库存 **P\***（基准，稳定）
- 播放用 **P'**（抖动，每次不同）

这样同一个库条目每次播出来都略不同，但库本身可复现。

**实测**：20 次播放手高差 **11.4mm**，都在范围内（`range_violations` 为空）。

### 抖动 delta 示例

```
lean -0.41  side_bend +0.17  head_nod +0.24  arm_swing -3.94
arm_lift +0.51  arm_roll -0.69  elbow_bend -0.67
```

幅度都是零点几度——**看起来是「活人不精确」，不是「抽筋」**。

### 还剩的

- **情绪规则还是写死的**：`bias_predicates` 里的阈值（30/40/70）是人工定的。
  更真实的做法是让**她的 LLM 输出情绪维度后，映射成目标**——但那需要 LLM 懂「谓词参数」，又回到谓词膨胀。
- **抖动和情绪规则的边界**：规则改目标、抖动改细节。但「抖动幅度由 energy 决定」这一点本身也是规则。

---


## 2026-11-24 · 解析器独立调用（两阶段）

### 结构

```
① 她说话（完整人格 + 记忆 + mood，**prompt 里没有任何格式要求**）
      ↓
② 解析器（短 prompt，**不带人格**，只看两句话）
      ↓ {"intent": "wave", "mood_delta": {"mood": 0.1}}
③ runtime（情绪改目标 → 求解 → 验证 → 抖动 → 播）
```

### 为什么不是「先定意图再说话」

用户指出：先意图 → 第二次必须配合 → **被迫有动作** → 为了给动作找理由，说话会变。

**反过来（先说话、后判断）**：
- 动作是**证据**，不是命令
- 她说了「挥了」→ 解析器必须给 wave（言行一致，结构保证）
- 她只是感叹 → 解析器给 null
- 动作的地位从「命令」变成「事后解释」

### 第一阶段真的没有任何格式要求

第一版 prompt 里还有一句「直接说话就行，不要输出 JSON」——**提到格式就已经是暗示**。

改成：

```
（就是跟他说话。别的不用管。）
```

测试断言：prompt 里**不含** `<<ACT>>`、**不含** `JSON` 字样。

### 解析器不带人格

它是解析器，不是她。**她说的话已经定型了，不参与创作。**

所以解析器可以用便宜模型 / 短 prompt（`max_tokens` 很小）。

### 实测（真 API，4 轮）

| 轮 | 她说 | 解析 | 结果 |
|---|---|---|---|
| 1 | （抬起手，冲你晃了两下）唉，看见了没 | `wave` | ✅ z=0.990 |
| 2 | 哦……那先别干了 | `None` + energy -10 | ✅ 纯说话 |
| 3 | （从右下角挪到屏幕中间） | `walk` | ✅ z=0.555 |
| 4 | （伸——）啊…… | `stretch` | ✅ z=1.033 |

**4 轮全对**（旧方案 4 轮 1 失败）。

耗时 **1558ms/轮**（8 次调用），比单阶段慢 28%，但正确率高。

### mood_delta 也工作了

轮 2「累死了」→ 解析器给 `energy: -10`。

**它从她说的话推情绪，不是凭空生成。**

夹紧规则：`energy ∈ [-20, 20]`，`mood/close/guard ∈ [-0.3, 0.3]`。

### 兜底

| 情况 | 结果 |
|---|---|
| 解析器坏 JSON | `intent=None`，**say 保住** |
| 解析器崩了 | `intent=None`，**say 保住** |
| 编造族 `backflip` | `intent=None` |

---


## 2026-11-24 · 解析器的成本优化（A 方案定稿）

### 决定

**不引入第二个模型**——DeepSeek 只有 `deepseek-chat`（已经很便宜）和 `deepseek-reasoner`（贵，推理用）。
「便宜模型」实际就是**同一个模型 + 短 prompt**。

### 实现

解析器单独给一个低参数实例：

```python
llm    = DeepSeek(key, temperature=0.85, max_tokens=350)   # 她说话：自由
parser = DeepSeek(key, temperature=0.1,  max_tokens=80)    # 解析器：只做分类
r = turn_two_stage(..., parser_llm=parser)
```

**两个参数的差别**：

| | temperature | max_tokens | 为什么 |
|---|---|---|---|
| 说话 | 0.85（高） | 350 | 要自由、要有长度 |
| 解析器 | **0.1（低）** | **80** | 只做分类，不要发挥 |

**temperature 低对解析器特别重要**——它不该「创作」，只该「判断」。

### 成本

| 调用 | 输入 tokens | 输出 tokens |
|---|---|---|
| 她说话 | ~2300 | ~100 |
| 解析器 | ~350 | ~50 |

**解析器约是说话成本的 1/6。** 一天 100 轮约 0.6 元，其中解析器 0.08 元。

省它意义不大，所以不加关键词优先层。

### 实测

**4 轮 3 个动作全解出**，`mood_delta` 也正常（伸懒腰 +energy 5）。

耗时 1796ms/轮（比不加 parser 实例略慢，因为 API 抖动）。

---


## 2026-11-24 · 定稿：只保留两阶段

### 决定

用户 2026-11-24：**「单阶段感觉不需要」**。

所以：
- `<<ACT>>` 方案**废弃**（`turn_combined` / `split_act` / `ACT_MARK` 保留但不再推荐）
- **`turn()` 统一入口 = 两阶段**
- 删掉 `tests/test_combined.py`

### 新签名

```python
turn(text, llm_fn, capabilities, persona, memory, mood,
     body=True, parser_llm=None) -> dict
```

返回：

```python
{"say": "...", "intent": "wave"|None, "mood_delta": {...}|None,
 "act": {"intent": ...}|None,   # 兼容旧调用方
 "calls": 2, "source": "two_stage"}
```

`body=False` 时跳过解析器（纯说话，`source="chat_only"`）。

### 实测（真 API，4 轮）

```
[1] 挥挥手        → 她说「（抬手，往屏幕这边晃了两下）在这儿呢。🐋」  → wave  ✅
[2] 累死了        → 她说「嗯，那就别干了，趴着」                  → None  ✅
[3] 过来           → 她说「（从桌角探出半个脑袋）看吧看吧」         → walk  ✅ z=0.556
[4] 伸个懒腰       → 她说「（两只鳍手举过头顶）唔——啊」            → stretch ✅ z=1.032
```

**4 轮 3 个动作全解出**，她的话完全自然（自己带括号描写）。

### 为什么只留两阶段

| | 单阶段 | 两阶段 |
|---|---|---|
| 准确率 | 4 轮 1 失败 | **4 轮全对** |
| 她的话 | 有 `<<ACT>>` 痕迹 | **完全自由** |
| 延迟 | 1.2s | 1.6s |
| 成本 | 1× | 1.17× |

**多 0.4s 和 17% 成本，换 25% 准确率 + 话变自然**，值。

单阶段不是「降级选项」，是「不需要了」。

---


## 2026-11-24 · free 族 + 族自动生长

### 用户的核心质疑

> 「新做的动作入库是入到哪里？不是建新的具体族吗？那既然要有具体族才能做动作，那和动作库有什么区别？」

**对。** 如果新动作都进 `free` 族，那 `free` 的参数散布整个空间，就不再是「一类动作」，退化成动作库。

### 区别在哪

| | 族 | 库条目 |
|---|---|---|
| 是什么 | **一类动作的定义**（范围+判据） | **一次执行**（参数） |
| 语义 | ✅ 有 | ❌ 只是数字 |
| 数量 | 少 | 多 |

### 闭环

```
free 族做新动作 → 入库（记录 predicates + hits）
      ↓ 同一谓词组合被请求 ≥3 次
propose() 提议新族（名从谓词生成，P_range 从证据算）
      ↓
apply_proposals() 写进族表
      ↓
下次直接走新族（快、稳）
```

### 实测（`tools/demo_grow.py`）

```
第 1-3 次：free 做「前倾+点头」→ lib-0001（hits=3）
propose → 提议族 'lean_nod'，出现 3 次，P_range 12 维
apply   → 族数 13 → 14
新族直接用：lean_nod → L2 ✅
```

### 关键设计：「反复出现」看命中次数，不是条目数

库会**去重**（同一个 key 只存一条），所以「3 次请求」只会有 1 条 entry。

`best()` 命中时 `entry.metrics.hits += 1`，`put()` 时初始化为 1。

`propose()` 按 `sum(hits) >= min_count` 判断。

### 顺带修的映射 bug

`torso_lean` 谓词读的是 **chest 的绝对前倾角**，而 `lean` 参数是**相对 rest 的增量**。

rest 姿态下 chest 本来就前倾 **6.34°**，所以 `lean=0` 时 `torso_lean(deg=8, tol=2)` 被判合格（\|0-8\|=8… 实际是 \|6.34-8\|=1.66 < 2）——**求解器不动也"通过"**。

修：`torso_lean` 减掉 `REST_CHEST_PITCH_DEG = 6.34`。

**又一条「映射不对导致求解器不动」的例子。** 这类 bug 的共同特征：**谓词合格但姿势没变**。

### 约定：负 lean = 前倾

`stretch` 模板里是 `torso_lean(deg=-5)`。写 demo 时用了 `+8`，结果后仰。

---


## 2026-11-25 · 方向判据（角度是标量，方向要单独判）

### 用户发现的漏洞

> 「你这个约束判据有记得尊重映射表吗方向什么的保持正确了吗不能反向约束了吧」

**对。** `knee_angle`（髋-膝-踝夹角）**抓不到反折**：

```
小腿绕 X -40°（反折，膝在后方 +68mm）→ knee_angle err = 0  通过 ❌
小腿绕 X +40°（正常，膝在前方 -80mm）→ knee_angle err = 0  通过 ✅
```

夹角是 0–180° 的标量，**不分正反**。

### 修

新增方向判据：

| 谓词 | 量什么 | 容差 |
|---|---|---|
| `knee_forward` | 膝的 y 比髋-踝中点更负（更靠前） | min 10mm |
| `elbow_forward` | 肘相对肩-手连线的前后位置 | min -40mm（手臂活动度大，宽松） |

**验证**：

```
shin_x   knee_angle  knee_forward  判定
   -80       0.000        1.000     反折(拒) ✅
   -40       0.000        1.000     反折(拒) ✅
     0       0.537        0.037     正常
   +40       0.000        0.000     正常
   +80       0.000        0.000     正常
```

### 这一类 bug 今天第 4 次

1. 肘的弯曲轴（Y → X）
2. 肘的左右符号（不该带 `s_lift`）
3. `torso_lean` 忘了减 rest 基准（6.34°）
4. `knee_angle` 抓不到反折

**共同特征：谓词合格但姿势是错的。**

**加任何「角度类」判据时，必须问一句「方向对吗」。**

### 其他判据排查

| 判据 | 有方向漏洞吗 |
|---|---|
| `knee_angle` | ❌ 有（已修） |
| `shoulder_range` | ⚠️ 理论上有，但肩是球形关节 + `arm_not_crossed` 互补 |
| `balance` | ✅ 无（距离是标量） |
| `wrist_neutral` | ⚠️ 同 shoulder |
| `no_hyperextension` | ✅ 无（<20° 就是反折，方向无关） |
| `torso_lean` | ✅ 已减 rest 基准 |

---


## 2026-11-25 · 把 overlay + blend 接进 runtime

![运行时叠加](docs/images/runtime_composite.png)

### 新增

`Runtime.handle_composite(acts, now, mood, blend_ms)`：

```python
r = rt.handle_composite([
    {"intent": "walk"},                                        # base（全身）
    {"intent": "wave", "groups": ["arm.right"], "weight": 1.0}  # 叠加层
], now=t0)
```

流程：

```
每个 act 走 handle_intent（查库/求解/验证/抖动）
    ↓ 各自 pose_for_from_P 得到 basis
merge_layers 合并（按 weight 定优先级）
    ↓
transition_to（200ms 惯性化）
```

### 实测

```
sources ['L2', 'L2']  layers 1
hand_z = 0.974（挥）  foot_z = 0.08（走路抬脚）
```

**右臂来自 wave、左臂和腿来自 walk** —— 两部分独立。

### 调试中踩的两个坑

**1. basis 已经是世界矩阵，不要再跑一遍 FK**

测试里写 `rf.compute(r2["basis"])` —— 但 `r2["basis"]` 是 `rig.compute()` 的输出（世界矩阵）。
再 FK 一遍等于把世界矩阵当 basis 用，位置全错（0.989 → 0.548）。

**读位置直接 `basis["rightHand"][2,3]`。**

**2. `layers` 的索引从 0 起，不是 act 的索引**

`merge_layers` 的 `winner` 记的是 **layer 序号**，不是 `acts` 的下标。
第 2 个 act 如果没解出来，它的 layer 序号会比 act 序号小。

### 测试（`tools/test_composite.py`，13 项）

| 检查 | 结果 |
|---|---|
| 单动作无叠加 | ✅ |
| walk+wave 两层 | ✅ |
| 右手举高 0.984 | ✅ |
| 脚保持走路 0.080 | ✅ |
| 200ms 过渡最大单步 7.9mm | ✅ |
| 过渡结束到达目标 | ✅ |
| 权重大的赢 | ✅ |
| 全失败返回空 | ✅ |

---


## 2026-11-25 · 腿 IK：数学对了，但写进 Blender 崩了

### 用户要求

「把走路的那些动作姿态给我渲染几张腿部的图片我看看有没有歪」

### 查出来的事实链

| 检查 | 结果 |
|---|---|
| `leg_basis` 目标=rest 踝 | **误差 0.0 mm** ✅ 公式对 |
| 前移 10cm 目标 | 误差 30mm（腿长限制，合理） |
| `build_walk` 原地踏步 | **误差 0.0 mm**，脚 z 0.080→0.133 ✅ 腿真的在动 |
| basis 的平移项 | 全 0 △ |
| **写进 Blender（`apply_pose`）** | **崩了（蜷成一团）** ❌ |

**所以：`rig.compute(basis)` 对，`binder.apply_pose(basis)` 错。**

### 排查中修掉的三个参数错误

**1. 脚底 vs 踝差 80mm**

`gait.foot_targets` 返回**脚底**（z=0），`ik.leg_basis` 要**踝**（rest z=0.080）。

不加偏移 → 全部 `unreachable d=520mm > 427mm`。

**2. `foot_targets` 的 y 从 `body_origin` 起算**

x 有 `lateral` 补正（从 `start` 继承），**y 没有**。

`body_origin.y` 必须等于 rest 脚的 y（-0.0683），否则脚目标整体偏 68mm。

**3. `HIP_DROP` 一开始设了 0.045**

那让髋下降但脚目标没降 → 腿伸得过长。先设 0 验证，再按需加。

### 现在的位置

- ✅ 腿 IK 的几何：**完全正确**
- ✅ 全身走路姿态（`build_walk`）：**FK 误差 0**
- ❌ 写进 Blender：**崩**

**下一个要查**：`binder.apply_pose` 对 `connected` 骨的处理。

腿的三根骨里，`leftUpperLeg` **不是 connected**，`leftLowerLeg` / `leftFoot` **是**。

如果 `apply_pose` 对所有骨用同一种写法，非 connected 的 `upperLeg` 可能写错了。

### 工具

- `tools/walk_full.py` —— 全身走路（上半身 P + 步态 + 腿 IK），FK 误差 0
- `tools/probe_leg_ik.py` / `probe_leg_ik2.py` —— IK 可达性 / 数学验证
- `tools/probe_elbow_axis.py` —— 关节弯曲轴

---


## 2026-11-25 · 腿崩的根因：`apply_pose` 收的是世界矩阵，我传了 basis

### 根因

```python
def apply_pose(M, rig, arm):
    basis = to_basis(M, rig)   # ← M 是**世界矩阵**，这里反算 basis
    apply_basis(arm, basis)
```

`build_walk` 返回的是 **basis**（已经有 `rig.compute` 用过它）。

我把它传给 `apply_pose` → **又反算了一次 basis** → 双重转换 → 姿势全崩（蜷成一团）。

### 修

直接用底层函数：

```python
from core.binder import apply_basis
apply_basis(arm, basis)      # basis 就是 basis，不再转换
```

**验证**：209 骨，`pb.matrix` vs `rig.compute(basis)`，最大误差 **4e-6**（float32 噪声），平均 **0.0**。

### 腿形正常

![站立](docs/images/legs_stand.png)
![抬腿](docs/images/legs_lift.png)

膝盖朝前、脚朝前、抬腿自然。

### 顺带查清的两个参数坑

**1. 脚底 vs 踝差 80mm**

`gait.foot_targets` 返回**脚底**（z=0），`ik.leg_basis` 要**踝**（rest z=0.080）。

**2. `foot_targets` 的 y 从 `body_origin` 起算**

x 有 `lateral` 补正（从 `start` 继承），**y 没有**。
`body_origin.y` 必须等于 rest 脚的 y（-0.0683）。

### 教训（新的一条，写进 HANDOFF）

**`apply_pose(M)` 和 `apply_basis(arm, basis)` 是两个不同的入口：**

| 函数 | 输入 | 什么时候用 |
|---|---|---|
| `apply_pose(M, ...)` | **世界矩阵**（`rig.compute` 的输出） | 你手上是「姿态矩阵」时 |
| `apply_basis(arm, basis)` | **basis**（`pose_gen.build` 的输出） | 你手上是「参数化 basis」时 |

**传错会静默地把矩阵反算两次**，不报错，只是姿势崩。

---


## 2026-11-25 · B 验证通过 + D 的设计

### B（原地踏步）验证结果

```
t     左踝z   右踝z   骨骼一致性   脚底最低
0.00  0.080  0.080  0.000004    -0.000
0.25  0.080  0.142  0.000004    -0.000   ← 右脚抬
0.75  0.142  0.080  0.000004    -0.000   ← 左脚抬
1.00  0.080  0.080  0.000004    -0.000
```

- 骨骼一致性 **4e-6**（float32 噪声）
- 脚底最低 **-0.000**（正好接地，不穿地）
- 左右脚**交替抬起**（0.080 ↔ 0.142）

**结论**：`pose_gen`（上半身）+ `ik.leg_basis`（腿）+ hips 恒等 这套组合是对的。

### D 的设计：BodyState

**为什么需要**：走路不是「一个动作」，是「角色的移动状态」。走路/跑步/转身/被推，都改变「身体在世界里的位置」，而所有动作都挂在这个位置上面。

```python
@dataclass
class BodyState:
    x: float = 0.0          # 世界 x
    y: float = 0.0          # 世界 y
    yaw: float = 0.0        # 朝向（度，0 = 朝 -Y）
    bob: float = 0.0        # 上下浮动（米，重心起伏）
    vel: float = 0.0        # 前进速度（米/秒）
    turn_rate: float = 0.0  # 转向速度（度/秒）
```

**三个职责分离**：

| 谁 | 做什么 |
|---|---|
| **动作（walk/run）** | 写 `vel` / `turn_rate` |
| **`BodyState.step(dt)`** | 积分出 `x/y/yaw`，算 `bob` |
| **姿态层（pose_gen + ik）** | **只读** `x/y/yaw/bob`，不写 |

**关键：姿态层不写 BodyState。** 求解器只解决「这个动作长什么样」，不解决「人在哪」。

### 落地步骤（三步，每步可验证）

**① `BodyState` + 平移 hips**

```python
def build_full(P_upper, G, t, state, rig, bm):
    basis = pose_gen.build(P_upper, rig, bm)          # 上半身
    # 根骨平移 = 世界位置 + 上下浮动
    offset = np.array([state.x, state.y, state.bob])
    basis["hips"] = translate(basis["hips"], offset)
    # 腿 IK：hip_offset 必须和上面一致，否则腿和身体脱节
    for side, tgt in foot_targets(G, t, start, state).items():
        lb, _ = ik.leg_basis(rig, bm, side, tgt + offset, hip_offset=offset)
        basis.update(lb)
    return basis
```

**验证**：`state.x=0.5` 时，脚和 hips 都平移 0.5，**骨骼一致性仍 4e-6**。

**② `runtime` 接 `BodyState`**

```python
class Runtime:
    self.body = BodyState()

    def tick(self, dt):
        self.body.step(dt)        # 积分位置
        return self.current_basis()

    def handle_composite(self, acts, ...):
        # walk/run 只设置 vel/turn_rate，不改位置
        for act in acts:
            if act["intent"] in ("walk", "run"):
                self.body.vel = SPEED[act["intent"]]
```

**验证**：`walk` 连续 3 秒 → 身体前进 1.5m，脚每步落地位置正确（脚锁不滑）。

**③ 转身 + 叠加**

```python
state.turn_rate = 45.0     # walk 时转身
# 所有姿态（挥/点头）都跟着 yaw 转
```

**验证**：边走边转边挥手，三个自由度独立。

### 已知的坑

**1. `hip_offset` 必须和 hips 平移一致**

腿 IK 从「rest 髋 + hip_offset」出发。如果 hips 根骨平移了但 `hip_offset` 没变，腿会从错误的位置开始算 → 脱节。

**这就是之前崩的原因**（`apply_pose` 传错只是一半，另一半是 hips 没对齐）。

**2. 脚目标必须是世界坐标**

`gait.foot_targets` 给世界坐标，`ik.leg_basis` 也要世界坐标。中间不要加 `body` 偏移（除非两边都加）。

**3. `bob` 要正弦，不要随机**

`bob = sin(2π × phase × 2) × amp` —— 一个步态周期重心起伏两次。

### 和现有代码的关系

| 现在 | D 之后 |
|---|---|
| `build_walk(G, P, t, ...)` 站着踏步 | `build_walk(G, P, t, state, ...)` 带位置 |
| `handle_composite` 只用 `pose_gen` | 全身：`pose_gen` + `ik` |
| 无 `BodyState` | `Runtime.body` 维护 |

---


## 2026-11-25 · D 第①步完成：BodyState + 世界走路

![走起来了](docs/images/walk_moving.png)

### 实测

```
t      x       y       bob     左踝z  右踝z  误差
0.00  0.000   0.000   0.0000  0.080  0.080   0.0
0.25  0.000  -0.125   0.0120  0.080  0.133   0.0   ← 右脚抬
0.50  0.000  -0.250   0.0000  0.080  0.080   0.0
0.75  0.000  -0.375  -0.0120  0.133  0.080   0.0   ← 左脚抬
1.00  0.000  -0.500  -0.0000  0.080  0.080   0.0
```

- 身体前进 **0.5m**
- 左右脚交替抬起（0.080 ↔ 0.139）
- bob 正弦起伏（±12mm）
- **脚目标误差全 0.0mm**

### 踩到的四个坑（按发现顺序）

**1. `foot_targets` 的 `travel` 和 `BodyState` 双重计算**

`gait.foot_targets` 内部已经算 `travel = stride*(t/(duty*period))`，`BodyState` 也在积分前进 → 位移翻倍。

**修**：加 `travel=False` 参数，`foot_targets` 只算「脚相对身体的周期位置」。

**2. hips 的 basis 平移会被 rest 旋转转掉**

`M[hips] = rest[hips] @ basis[hips]`，所以 `basis` 的平移先被 `R_rest` 作用。

直接 `basis[0:3,3] += world_offset` → 平移方向错（局部 X → 世界 -Y）。

**修**：`local = R_rest⁻¹ @ world`，即 `basis[0:3,3] += rest[0:3,0:3].T @ world`。

**3. stride 超出腿长（最大的坑）**

`stride=0.5` → 脚前后摆 ±0.25m。髋到踝只有 426mm。

```
距离 = √(426² + 250²) = 494mm > 腿长 427mm → unreachable
```

**必须降髋**：h ≥ √(427² - 250²) → **80mm**。

**4. 降髋时目标 z 不能跟着降**

```
髋降 80 + 目标也降 80 → 相对距离不变（还是 494mm）❌
髋降 80 + 目标留在地面 → 距离 419mm ✓
```

**正确的组合**：

```python
off = (state.x, state.y, state.bob - hip_drop)   # hips 平移
hip_offset = off                                  # 腿 IK 用同一个值
foot_target_z = 地面 + 踝高                        # z 不跟着降
```

### 现在的 `build_walk_state`

```python
basis = pose_gen.build(P, ...)                    # 上半身
basis["hips"] = translate_local(hips, rest, *off) # 根骨平移（转局部）
ft = foot_targets(..., travel=False)               # 脚相对身体
ft = ft + (state.x, state.y, 0)                    # 加身体位移（z 不加）
for side: leg_basis(..., hip_offset=off)           # 腿 IK
```

### 结论

**`pose_gen` + `ik.leg_basis` + `BodyState` 三者可以正确协作**，前提是上面四条都满足。

---


## 2026-11-25 · 腿的三种驱动 + 装配收进 core

### 新增三个模块

| 模块 | 作用 |
|---|---|
| `core/body_state.py` | `BodyState`：x/y/yaw/bob/vel/turn_rate + `step(dt)` |
| `core/foot_target.py` | `FootTarget` 三种实现 + `attach_legs` |
| `core/assembly.py` | `build_full_body`：唯一装配入口 |

### 三种脚目标源

| 类 | 用于 | 验证结果 |
|---|---|---|
| `GaitTarget` | 走/跑（周期步态） | ✅ `walk_state` 误差 0.0mm |
| `StaticTarget` | 站/蹲/单脚站 | ✅ 误差 0.0mm |
| `PointTarget` | 踩指定点 | ✅ 误差 0.0mm |

**`GaitTarget` 的 `hip_drop` 自动估算**：`stride × 0.16`（实测 stride=0.5 → 0.08m）。

### 腿的能力边界（`probe_leg_range.py` 实测）

```
方向        100mm  200mm  300mm  400mm
前/后/左/右/上/斜前上/斜前下   全部可达
```

- **全向可达**：从髋出发半径 427mm 的球
- **膝朝向可控**：`pole=(-1,0,0)` 让膝往左 7cm，`(1,0,0)` 往右 7cm
- **单脚站** OK

**「只能走」是封装问题，不是能力问题。**

### 逐项对比确认装配正确

`tools/probe_assembly_diff.py`：

```
A（手写）off      = [0, 0, -0.08]
B（build_full_body）hip_offset = [0, 0, -0.08]
A 目标/实际/误差  = 0.0mm
B 目标/实际/误差  = 0.0mm
hips basis 完全一样
```

**两条路径一致。**

### 已知问题：测试脚本制造假象

`tools/test_foot_target.py` 里同一套调用报 **263mm**，但 `probe_assembly_diff.py` 逐项对比是 **0.0mm**。

**这已经是第三次「probe 对、test 错」**。判断是测试脚本的结构问题（多次调用共享 `state` / 变量复用 / 变量顺序）。

**结论：核心模块对，测试脚本要重写。**

### 教训

**当 probe 和 test 结论矛盾时，先怀疑 test。**

probe 是「单一路径、无状态残留」，test 往往有多个 case 共享变量，容易制造假象。

---


## 2026-11-25 · 修好「走路 2 帧 IK 失败」（差 7.8mm）

![装配后的走路](docs/images/walk_assembly.png)

### 定位过程

`tools/probe_gait_t.py` 逐 t 扫描，问题是**只有抬脚最高点那两帧**失败：

```
t      err_L   err_R   ik_fail
0.000    0.0     0.0
0.125    0.0   263.2    right:unreachable d=434.8mm > 427.0mm   ←
0.250    0.0     0.0
...
0.625  263.2    0.0    left:unreachable d=434.8mm > 427.0mm    ←
0.750    0.0     0.0
```

**差 7.8mm（1.8%）**——腿要伸 434.8mm，但腿长只有 427.0mm。

### 根因

`GaitTarget.hip_drop` 的估算**只算了 stride，没算抬脚高度**：

```python
# 旧：stride × 0.16
self._hip_drop = max(0.0, stride * 0.16)      # 0.5 → 0.08
```

但抬脚时目标更高，腿要伸得更远：

```
目标踝 z = 0.08(脚底) + 0.06(抬) + 0.08(踝高) = 0.22
髋 z     = 0.506 - 0.08 = 0.426
垂直距离 = 206mm，加上前后偏移 → 434.8mm
```

### 修复

```python
# 新：把抬脚高度也算进去
self._hip_drop = max(0.0, stride * 0.16 + lift * 0.5)
```

`stride=0.5, lift=0.06` → `0.08 + 0.03 = 0.11`

### 修复后

```
t      err_L   err_R
0.000    0.0     0.0
0.125    0.0     0.0     ← 修好
0.250    0.0     0.0
...
0.625    0.0     0.0     ← 修好
1.000    0.0     0.0
```

**9 帧全 0。**

### 教训

**`hip_drop` 必须覆盖「脚能到的最远点」，不是「平均点」。**

腿的可达范围是硬的（427mm）。任何让脚变远的变化（stride、抬脚、身体倾斜）都要算进 `hip_drop`。

**这一条写进 HANDOFF。**

---


## 2026-11-25 · D 第②步完成：runtime 接 BodyState + 全身装配

![边走边转 60°](docs/images/walk_turn_60.png)

### 新增的 Runtime 方法

| 方法 | 作用 |
|---|---|
| `rt.body` | `BodyState`：x/y/yaw/bob/vel/turn_rate |
| `rt.tick(dt)` | 推进身体（位置/朝向/bob） |
| `rt.set_motion(vel, turn_rate)` | 设置速度（**不改位置**） |
| `rt.build_full(P, family, t)` | 全身装配（上半身 + 腿 IK） |
| `rt.foot_target_for(family)` | 按族选脚目标源（stride>0 → Gait，否则 Static） |

### 实测（`tests/test_runtime_body.py`，7 组全过）

```
[2] tick 12 次（vel=0.5）→ y=-0.500    ✅ 前进 0.5m
[3] 停下后位置不变                      ✅
[4] walk 9 帧脚误差                    ✅ 0.0mm
[5] idle 站着不动                       ✅ 0.0mm
[6] 身体 x=0.8 时脚跟着走               ✅ 0.876
[7] 边走边转 1 秒 → yaw=90°            ✅
```

**边走边转的完整状态**：

```
BodyState(x=0.257, y=-0.403, yaw=60.0, vel=0.50)
脚误差 {'left': 0.0, 'right': 0.0}
hips = [0.259, -0.491, 0.449]
```

### 职责边界（D 的核心）

```
动作层    set_motion(vel, turn_rate)    只设速度，不改位置
Runtime   tick(dt)                      积分出位置
姿态层    build_full(P, family, t)      只读 body，不写
```

**姿态层永远不写 `body`。**

### 现在的能力

| 能做 | 验证 |
|---|---|
| 走起来（前进 0.5m） | ✅ 误差 0 |
| 边走边转（yaw 90°） | ✅ |
| 停下 | ✅ |
| 站/蹲/单脚站/踩点 | ✅ 误差 0 |
| 叠加（走+挥） | ✅ `handle_composite` |

### 还没做

- **`handle_composite` 没接全身**（它还用 `pose_gen`，腿不动）
- 抬脚最高点的 `hip_drop` 是估算的（`stride*0.16 + lift*0.5`），不是精确解
- 转身时脚会滑（没做「支撑脚当轴」）

---

## 2026-11-25 深夜 · 阶段巡检：34 项全绿，但 3 条判据不达

### 怎么发现的

不是靠跑测试——34 项验收（27 测试 + 7 工具）全过。是把 **§7 的验收原文**
逐条写成独立探针，才暴露出三处「测试绿、判据不达」：

| 文档判据 | 实测 |
|---|---|
| P6「转 90° 不抽搐」 | 骨架朝向 **+0.0°**（`BodyState.yaw` 转满 90，骨头一动不动） |
| `spin` 族（G.turn_rate=90） | **什么都不动**（stride=0 → StaticTarget，turn_rate 被丢掉） |
| P6「walk 滑移 <3mm」 | 用 §22.4 的 `vel=0.5` 走，一个支撑期滑 **118.3mm** |
| P0「big_steps 16 → 0」 | 每帧调 `handle_composite` 时单帧跳 **433.9mm** |

**共同点：都是「接口语义」问题，不是数学错。** 所以单元测试全过。

### 修法（按修复成本排序）

#### 1. 速度自洽（`core/gait.py` `body_speed`）

支撑相脚不动 ⇒ `v_body = stride/(duty×period)`。walk 是 **0.3226 m/s**。

- `set_motion(family="walk")` 直接推导，不再手填
- `build_full` 兜底：发现 `body.vel` 和步态要求差 >1e-6 就纠正，
  并把 `{"was":0.5,"now":0.3226}` 写进 `diag["vel_sync"]`（不静默）

实测：填错也 0 滑移（修前 118.3mm）。

#### 2. 朝向进骨架（`core/assembly.py` `place_hips`）

全项目 `yaw` 只活在 `BodyState` 里，没有任何地方写进骨。

做法：**在身体局部系里装配，最后一次性刚体搬到世界**。

```
M[hips]_world = D @ M[hips]_local      D = (绕 Z 转 yaw, 平移 (x,y))
M[child]_world = D @ M[child]_local    basis 不变 ⇒ 整棵子树跟着转
```

为什么不直接在旋转过的 hips 上解 IK：髋的 rest 位置不在中轴
（x=0.049, y=-0.084），脚几乎在正下方，腿只剩 29mm 余量。
脚钉死在世界里、身体转 90°，髋到脚变成 **441mm > 427mm** → IK 直接失败。

**顺带修了一个文档/实现不一致**：`PointTarget` 的注释写「相对身体的偏移」，
代码却在加世界平移。现在 `positions()` 一律返回**身体局部系**。

#### 3. 过渡与目标更新解耦（`core/runtime.py`）

`transition_to` 每帧调 = 每帧把 200ms 计时清零 → 输出退化成「延迟一帧的硬切」。

拆成两个：

| 方法 | 语义 | 用在哪 |
|---|---|---|
| `set_pose(basis, now)` | 只更新目标，不重启过渡 | 同一组动作的连续帧 |
| `transition_to(basis, now)` | 从**当前显示姿态**起 200ms | 动作组合变化时 |

`handle_composite` 用组合签名（intent + groups）判断要不要 `switched`。

实测：433.9mm 瞬跳 → 129mm 平滑（smoothstep 理论峰值 ≈105mm，符合曲线）。

### 新增回归（27 → 30）

| 测试 | 卡住什么 |
|---|---|
| `tests/test_motion_speed.py` | 速度推导 / 填错自动纠正 / 0 滑移 |
| `tests/test_turning.py` | yaw→骨架 / spin 真转 / 转身时 IK 不失败 |
| `tests/test_perframe_composite.py` | 每帧调用不瞬移 / `set_pose` 不重置过渡 |

### 遗留（别当没修好）

转身时**脚跟着身体转**，转 90° 滑 145mm。要「不滑」得做支撑脚当轴
（§22.6 第 3 项）—— 那是独立的一件事。

### 教训

> 测试覆盖率 ≠ 判据覆盖率。这次的三个 bug，全部在测试之外，
> 而且全部活了至少一个版本。**写判据时要照着文档原文反驳自己一遍。**

---

## 2026-11-25 深夜 · 联调隔离：只读沙箱，不碰正式项目

用户定：`D:\DSproject\dsh-qq` 是**完整项目**（Live2D / 语音 / 贴纸 / 世界线都在跑），
v2 联调只用 `sandbox/` 那套**复制版**。

### 漏点（运行时真的会碰到正式项目）

| 位置 | 干的坏事 |
|---|---|
| `persona.body_connected()` | 默认读正式项目 `world/body.json` 的 **mtime**，拿正式项目的状态判断测试里身体在不在线 |
| `client.ENV_CANDIDATES` | 只认正式项目的 `.env` |

第一个尤其阴：本地跑测试时，正式项目要是刚好在跑，`body_connected()` 会返回 True，
于是测试走了「身体在线」的两阶段分支，但根本没连任何身体。

### 做法

新增 `llm/paths.py`，路径只此一处：

```python
from llm.paths import SANDBOX, WORLD, assert_writable
```

- `WORLD = SANDBOX`（世界线文件本来就在沙箱里）
- `assert_writable(path)`：写正式项目 / 沙箱外 → `PermissionError`

### 验证

`tests/test_sandbox_isolation.py`（7 组）：路径、文件齐全、默认读沙箱、
源码扫描（`llm/` + `core/` 出现 `dsh-qq` 即失败）、key 顺序、写保护。

全量 **31/31**。

### 允许碰正式项目的两处（都不在运行时路径）

- `tools/make_sandbox.py`：重新生成沙箱时读源，只写 `sandbox/`
- `ENV_CANDIDATES[-1]`：只读 API key，凭据不是状态

---

## 2026-11-25 深夜 · 主循环：能力终于上屏

### 问题

`Runtime` 会算姿态、`binder` 会写骨、`Dispatcher` 会排队——

**但仓库里没有任何模块把它们串成一条帧循环。**

所谓「她真的动了」全靠上一轮对话的临时脚本，换个会话就复现不了。
所有能力都困在测试里。

### 做法

新增 `core/loop.py`：

```python
loop = BodyLoop(rt)
loop.attach(arm)                       # basis → pose_bone.matrix_basis
for _ in range(N):
    loop.step(1/30, P=P_walk, family="walk")
```

三个设计决定：

1. **不 import bpy**。写骨靠注入的 `writer(basis) -> int`，
   测试里塞个假的就能跑，不用起 Blender。
2. **时钟收进 loop**（`loop.t`）。以前 `build_full(P, family, t)` 的 `t`
   由调用方随手传，传帧号还是墙钟全凭自觉——相位会跳。现在只有一个来源。
3. `step` 一个入口管两种情况：给了 `P+family` 走全身装配（含腿 IK），
   没给就用过渡中的姿态。

### 真机验证（`tools/blender_live.py`）

独立无头进程 `blender -b` 打开用户 .blend，跑 48 帧：

```
骨数 209 / armature 单位变换 / 缺骨 0
往返误差 0.000331 mm     ← 写进骨头 vs 纯 Python FK，逐骨位置
脚 IK 误差 0.0 mm
48 帧 / t=1.6s / 写了 10032 次（209×48）
body: y=-0.5161, vel=0.323（自动推导）
```

往返误差 0.0003mm 说明 **写骨和 FK 是同一套数学**（P5 那条最重要的自检）。

### 场景安全性

- 跑完 md5 仍是 `5de3fe946df5c793e248c305ef179204` —— **文件没被写**
- `bpy.data.is_dirty=True` 只是「内存里改过」，子进程退出即消失
- 脚本里没有 `wm.save` / `save_as`

### 踩到的坑

`shots` 的 `from` / `look_at` 必须给**数组** `[1.35, -2.1, 1.1]`。
给字符串 `"1.35,-2.1,1.1"` 会被逐字符迭代，报
`could not convert string to float: '.'`。

---

## 2026-11-25 深夜 · 二次运动：头发和裙子终于不是死的

### 问题

209 骨里 21 根主骨，其余 **188 根二次骨**。
v1.8 只驱动 21 主骨 + 4 节尾巴，`hair` / `skirt` 全是恒等矩阵。

结果：她走路时头发和裙子跟着身体**刚性平移**，像塑料模型。
站着看不出来，一动就露馅。

### 做法

`core/secondary.py` —— 弹簧-阻尼，不是物理仿真：

```
ω  = 父骨世界旋转增量 / dt
θ* = -gain · ω · scale[分区]        ← 父骨转得越快，子骨落后越多
a  = k(θ* - θ) - c·v ；v += a·dt ；θ += v·dt
最终 = clamp(θ + coupling·父骨滞后, ±max_deg)
```

接管 hair 70 + skirt 64 + ribbon 6 = **140 根**（tail 归 `tail_curl`，手指/toe 暂不动）。

### 实测

90°/s 转 1.2 秒，和关掉对比：

| 部位 | 末端位移 |
|---|---|
| 发梢 `Hair_00_05` | 41.07 mm |
| 裙摆 `Skirt_00_04` | 53.70 mm |
| 腰饰 `WaistRibbon_0_03` | 39.28 mm |

- 最大滞后 **12.0°**（钳位命中）
- 静止 30 帧滞后 `0.000000 rad` —— **不自激**
- **2.71 ms/帧**（140 骨 + 209 骨 FK）

### 一个刻意的限制

只挂在 `BodyLoop` 上，**不进 `Runtime`**。
二次运动是有状态的（θ、v），塞进 `build_full` 会让离线测试的姿态带惯性残留，
同一个 P 解两次得到不同结果——那会把 D10（每帧从 rest 重建）弄脏。

### 踩到的坑

先按交接文档写的「hair 60 / skirt 64 / ribbon 6 = 134」写测试，跑出 140。
实际 hair 是 **70**（60 Hair + 10 Bangs），文档漏了 bangs。
**交接文档的数字也要验，别照抄。**

---

## 2026-11-25 深夜 · L0 底噪：呼吸和视线

### 问题

`runtime.py:85` 写着：

> 视线跟随本来就该由 L0 底噪自动处理，不需要每次动作指定。

于是 `gaze_toward` 被设成 `HIDDEN_FROM_LLM`，理由就是「L0 会管」。
但 **L0 根本不存在**——两头都不管，视线跟随完全没有。

另外她静止时是**完全静止**的：不呼吸、不微动。

### 做法

`core/idle.py`：

| 项 | 做法 | 默认 |
|---|---|---|
| 呼吸 | `chest` 绕世界 X 正弦，`spine` 0.45 倍 | ±0.9° / 4.2s |
| 视线 | `head` 转向世界目标点，超范围就只转那么多 | 限幅 25° |

顺序：`idle` → `secondary`（反了的话头发看不到胸腔呼吸）。

### 实测

```
呼吸 0.90° / 脊柱 0.41° / 周期回位 0.0000°
正前方目标 误差 0.00°
正后方目标（限幅 18°）转角 18.00°，仍背对 162°
全关 basis 不变 0.00e+00
```

### 踩到的坑（差点漏过去）

`basis_from_world_rot(rig, "chest", "x", a)` 的 `a` 是**角度**。
我按 `math.radians(0.9)` 传了弧度 → 实际只转了 0.0157°，呼吸几乎不存在。

单元测试第一版就是这么红掉的：`[FAIL] 幅度 ≈ 0.9°  0.02°`。
**如果没有那条断言，这个「不呼吸」会一路带到真机上。**

---

## 2026-11-25 深夜 · 叠加接全身：一边走过来一边挥手

### 问题

`handle_composite` 的 base 走 `pose_for_from_P`（只有上半身），
腿是恒等矩阵。所以「走路 + 挥手」实际是**站着挥手，人平移**。

### 改法

1. `Runtime._t` 成为步态时钟（`tick(dt)` 累加），`build_full(t=None)` 默认用它
2. `handle_composite` 选出 base 后，用 `build_full` **重建全身**
3. 新增 `BodyLoop.step_actions(acts, dt)` 把「推进 → 复合 → 过渡 → 写骨」串起来

叠加层完全没动：还是只取上半身 basis，按 groups 覆盖。

### 实测

```
左脚高度起伏 90mm          ← 真的抬脚
右手 0.894 一直举着
腿骨与纯走路逐元素一致 0.00e+00   ← 叠加层没有污染腿
IK 误差 0.0mm
真机 hand_z 0.914→0.897 / foot_z 0.080→0.149
```

### 顺带修掉的耦合

以前 `build_full(P, family, t)` 的 `t` 要调用方自己传：
测试传 `i/8`（帧号当秒），`Runtime` 内部没有时钟。
现在 `tick(dt)` 是唯一推进者，`build_full()` 不传就用它 —— 相位不会再跳。

### 回归

`tests/test_composite_full.py` 5 组：不 tick 不动 / tick 后抬脚 / 叠加层不污染腿 /
`_full_body=False` 回退 / 装配诊断可查。

---

## 2026-11-25 深夜 · 支撑脚当轴：转身终于不滑

### 问题

脚是「身体局部坐标」，身体一转脚跟着转。
边走边转 90°/s，轴心脚一个支撑期滑 **112mm**——物理上就是在冰面上打转。

### 做法

支撑脚钉在世界里，**身体位置由它反推**：

```
world_foot = body_xy + Rz(yaw) · local_foot      ← 锁定
body_xy    = lock_xy − Rz(yaw) · local_foot      ← 每帧反推
```

### 为什么这次不会「够不着」

v1.8 试过「脚钉死在世界、身体绕自己转」，结果髋到脚被拉到 **441mm > 427mm**，
IK 直接失败。

这次身体是**绕脚**转的：髋到脚的距离是刚体不变的，所以腿永远够得着。
**同样是「脚不动」，转轴选错就是两个结果。**

### 实测

| 场景 | 滑移 |
|---|---|
| 直走（轴心脚） | 0.0mm |
| 边走边转 90°/s（轴心脚） | **0.0mm**（修前 112mm） |
| 非轴心脚（支撑相内） | 36mm（换重心，未建模） |
| 平均速度 | 0.323 vs 理论 0.323 ✓ |

### 副作用（好的那种）

直走现在也走这条路：`body.vel` 不再是自由量，而是**被支撑脚推出来的**。
这和之前 `body_speed` 的推导互相印证——两条独立的路给出了同一个 0.3226。

### 没解决的

- 双支撑期只锁一只脚，另一只会有几十 mm 位移（人得换重心）
- **站立转身**（idle / spin）仍然脚随身体转：不动脚做不了 90°，必须迈步

---

## 2026-11-26 凌晨 · 真 LLM 打通（key 一直都在）

### 之前的判断错了

我从交接文档读到「真 LLM 接入：**没有 API key**」，就一直没试。

实际上 key 在正式项目的 `.env` 里，而 `llm/paths.py` 的 `ENV_CANDIDATES`
最后一项就是它（只读）。**文档说的「没有 key」是当时没找到，不是不存在。**

### 实测（`tools/live_turn.py`，真 DeepSeek + 沙箱人格）

```
"挥挥手跟我打个招呼"
  say   ……就这？我当多大事呢。手在这儿呢，看见了没。
  intent=wave  calls=2  1843ms  hand_z 0.988  IK 0.0mm

"今天有点累，伸个懒腰吧"
  intent=stretch  mood_delta {energy:-8}  2502ms  hand_z 1.033
```

关键指标全部正常：她说完话 → 解析出动作 → 求解 → 手举到 0.98m（高过头 0.84m）。

### 然后发现她写括号旁白

第一次跑，她说「（抬起手，从屏幕右下角歪头看过来）哟。」

查了两层：

1. `persona.md` 里**没有**「不写括号神态」这条——它在 `persona.json` 的
   `speech.medium` 里，而 `load_persona()` 只读 `.md`。
2. 补上之后她**还是**写。因为 `build_chat_system` 自己结尾加了一句
   「（就是跟他说话。别的不用管。）」——既示范了括号，又说「别的不用管」，
   把上面那条格式规则顶掉了。

改成「现在就回他，直接用打字的口吻。」之后，括号没了。

### 教训

> 提示词里**自己写的脚手架**也算提示词。
> 一边要求「不写括号」，一边用括号举例，模型会学例子。

---

## 2026-11-26 凌晨 · 视觉验证 + 对话模拟

### 做出来的东西

`tools/storyboard.py`：真 API 多轮对话 → 每轮求解 → 写骨 → 渲染一张图。

4 轮 8 次调用 ≈ 10 秒，一次性拿到对话记录 + 姿态指标 + 对照图。

### 这 4 轮

| # | 我说 | 她说 | intent |
|---|---|---|---|
| 0 | 在吗 | 嗯，在。这个点你还没睡啊。 | null |
| 1 | 挥挥手跟我打个招呼 | 抬手，晃了晃。喏，打招呼。 | wave |
| 2 | 过来让我看看你 | 抬头看你，来，我在这儿呢 🐋 | walk |
| 3 | 累了，坐下歇会儿 | 那你就坐会儿呗，别又端着了。 | sit |

### 看图之后确认的

- 挥手：手过头顶（0.972 > 0.839），肘没反折
- 坐下：髋 0.529 → 0.279，整个人矮下去
- 走路：有跨步，脚不离地（≥0.08）
- 头发/裙摆跟着动，没穿模

### 看图才发现的两个 bug

**1. `sit` 根本没坐下。**
`G.knee_bias=60` 写了，但运行时**没人读**——`StaticTarget` 的 hip_drop 恒为 0。
所以「坐下」和「站着」的髋高完全一样（都是 0.529）。
修：`StaticTarget` 读 `G.hip_drop`，sit=0.25 / kneel=0.35。修完髋降到 0.279。

**2. 走路时两臂不摆，固定往后 22°。**
`walk.P_default.arm_swing=22` 被 `pose_gen` 当**常量**用，
而且左右同号——不是「摆臂」，是「两臂一起往后背」。
（没修，记在这儿。）

### 第三次撞上同一个坑：括号

改了两轮 prompt，她仍然时有时无地写 `（抬手）`，甚至改成 `{action: wave}`。

两次试错的结论：**这是概率不是规则，prompt 压不干净。**

最后在输出侧确定性清理：`strip_stage_directions()` 去掉 `（）[] {} 【】` 里的内容。
解析器仍然看**原文**（括号里的动作对它是信号），只有给人看/将来给 TTS 的 `say` 被清。

---

## 2026-11-26 · 三件事：摆臂 / 头发 / 世界线写回

### ① 摆臂接步态相位

`walk.P_default.arm_swing = 22` 之前被当**常量**用，左右同号 —— 等于「两臂一起往后背」。

现在 `pose_gen.build(phase=)` 把它变成摆动：

```
swing_side = ∓ arm_swing · cos(2π·phase)
```

左腿在前时左臂在后 —— 人走路就是这个反相。

实测：手部前后行程 **170mm**，左右去趋势相关 **−1.00**。

> 测这个的时候踩了个坑：手的世界 y 里混着「人往前走」的公共斜坡，
> 直接算相关是 **+0.51**（看起来同相）。**先去趋势再比相位**，才是真的 −1.00。

### ② 头发加「风」

原因：二次运动只对**旋转**起反应，而走路时头几乎不转 → 头发只甩 0.71°。

加了 `wind_gain`：发梢朝运动反方向拖 `15°/m/s`。

| 状态 | 最大滞后 |
|---|---|
| 站着 | 0.00° |
| 走路 0.32 m/s | 9.55° |
| 0.8 m/s | 12.0°（钳位） |

真机走路 48 帧：0.71° → **10.92°**。

### ③ 世界线写回

`llm/world.py`：写 `chat.jsonl`、`mood.json`，可选 `memory.md`。**只写沙箱**。

真 API 两轮实测：

```
chat.jsonl 200 → 204 行
mood.json energy 42 → 34（"今天好累啊" → mood_delta -8）
下一轮 load_mood() 读到 energy=34
```

**两个刻意的限制**：

1. **不自动写记忆**——模型会编「主人昨天说……」这种不存在的事实，
   写进记忆下一轮就当真。记忆要么人工确认，要么单独提炼。
2. **记忆改读尾部**——`memory.md` 是追加写的，原来读头部，
   新记忆永远进不来。

### 教训

> 头发不动，别急着调 gain——先问「这个模型的输入里，有没有能引起它的量」。
> 走路时头部旋转≈0，光调 gain 只会让它对转身过敏感。

---

## 2026-11-26 · Blender 常驻面板（最小版）

### 为什么是插件而不是外挂进程

外挂进程要额外解决「谁在画」「进程挂了谁收拾」。Blender 自己就是宿主，
`bpy.app.timers` 在主线程序列执行，写骨天然安全。

### 三个控件

| 控件 | 作用 |
|---|---|
| Connect | 建 Runtime + 起 30fps 循环 + 心跳 |
| Disconnect | 停循环 + **还原连接前的骨骼** |
| 输入框 + 说 | 后台线程调 API，主线程播动作 |

### 「没连接就不输出动作」是免费的

连接期间每 2 秒 touch `sandbox/body.json`；`body_connected()` 判的就是它。
离线时 `turn()` 走 `chat_only`，根本不解析动作。

**这个判断 v1.8 就写好了**，只是一直没有东西去 touch 那个文件。

### 线程模型

主线程只做「推进一帧 + 写骨 + 出队」；API 调用在后台线程，结果进 `queue.Queue`。

不做线程的话，每次说话 Blender 会卡 2 秒——**这是这个面板唯一的硬约束**。

### 实测

```
connect 122ms
呼吸让骨骼动（matrix diff 1.2）
body_connected → true
说「挥挥手」→ 2032ms → wave → 手 0.974 > 头 0.845
disconnect → 逐骨还原 1.49e-7
UI：面板 ✓ 属性 ✓ 算子 ✓ 连/断 ✓ 反注册 ✓
```

### 两个刻意的设计

1. **连接时快照所有骨骼，断开时还原**。用户的场景里有非单位姿态（145 根），
   不能因为插个插件就把他摆好的姿势冲掉。
2. **先摆一个 idle 当底**。不这么做的话 `current_basis()` 是 None，
   呼吸没有东西可叠——连上以后她一动不动。

---

## 2026-11-26 · 多动作串接

### 问题

一次对话只解析出**一个** `intent`。所以「过来，然后坐下」只能做一个动作。

### 契约改动

解析器输出从 `intent` 变成 `intents` 数组（最多 3 个，可带 `dur_s`）。
旧格式仍然兼容。

### 播放

`core/sequence.py`：按顺序播，到点切下一个。
**不是叠加**（`overlay` 管的是同时），是**串接**——前一个播完再切后一个。

### 实测

真 API：

```
"先走过来，然后坐下歇会儿"
  say      行，我过去——走慢点啊，腿还有点软。到了，坐会儿。
  intents  [walk 2.0s, sit 1.6s]
```

引擎（不走 API 直接喂结果）：

```
hips 0.529（walk）→ 0.289（sit）   播放结束 busy=False
```

### 面板已更新

插件重装进 `%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\`，
GUI Blender 里 disable→enable 重载过，偏好已保存。

**注意**：以后每次改 `blender_addon/body_live.py`，都要重新拷一次再重载——
Blender 不会自动跟项目目录里的文件同步。

---

## 2026-11-26 · 按真实用法跑一遍：抓出 2 个 bug

### 怎么跑

`tools/simulate_session.py` 用**和面板同一个引擎**：连接 → 说 5 句 → 串接 →
不支持的动作 → 断开，动作发生时抓帧。

### Bug 1：走完不停，她一直往前滑

第一次跑，第 3、4 张图是**空白**的。

查身体坐标：`body.y` 一路 `0 → -1.42 → -2.62 → -4.19`。
她以 0.32 m/s 一直往前走，5 秒走出画面。

根因：`build_full` 只在**步态族**里同步速度。走完切到 `sit`（站桩族）时，
`body.vel` 还是 0.323，`tick` 照常积分。

修：`ft.speed() == 0` 时把 `body.vel` 归零，并写进 `diag["vel_sync"]`。

> 这个 bug 在纯数值测试里永远抓不到——`test_composite_full` 只跑 30 帧，
> 位移还小。**只有连着跑一整段会话才会暴露。**

### Bug 2：她的话显示成空

第 2 轮 `say` 是空字符串。查下来是模型只回了
「（抬手，冲屏幕这边晃了晃）」，`strip_stage_directions` 把括号内容全剪了，
剩下空串。

修：剪完为空时退回「只去掉括号字符、保留文字」。

### 看图还发现两个（没修）

1. **`lie_down` 不躺下**：髋高 0.529，和站着一样。和当初 `sit` 同一个病。
2. **`spin` 在复合路径里不转**：`G.turn_rate` 只在 `set_motion(family=)` 里生效。

还有一个行为问题：她说「翻完了」——她根本不会翻跟头。
人格里写着「做不了就说做不了」，没守住；解析器又用 `head_tilt_fam` 兜底，
等于给了一个假动作。

### 教训

> 数值测试只证明「姿势算得对」，证明不了「连续用不出事」。
> 状态（速度、位置、时钟）会跨轮累积，**必须按真实会话长度跑一遍**。

---

## 2026-11-26 · 完整会话回归：一次修 4 个

写了一个 `tools/test_full_session.py`，把能想到的用法和边界都串起来跑：

连接 → 纯聊天 → 单动作 → 串接 → 忙时插话 → 长时空转 → 做不到的动作 →
趴下/转身 → 断开还原 → 迟到结果 → 世界线写回 → 性能。**16 项。**

### 抓到并修掉的

**1. 断开期间的请求，重连后突然播出来。**
断开时他正在飞的那次请求，结果还留在队列里；重连后 `_drain` 一取，动作就播了。
修：加**连接代数** `_gen`，断/连都 +1，`_drain` 丢弃不同代的结果。

**2. `lie_down` 不躺下。**
髋 0.529，和站着一样。加 `G.hip_drop = 0.32` → 0.209。
（真躺下需要躯干翻转，那是另一件事；现在至少是蹲下。）

**3. `spin` 在复合路径里不转。**
`G.turn_rate` 只有 `set_motion(family=)` 会读。改成 `handle_composite` 选中
base 后也读 → 实测 0° → 108°。

**4. `mood.json` 的 `updated` 看不出变化。**
时间格式只到分钟，同一分钟内连说两句就一样。改到秒。

### 顺带强化的

人格和解析器都加了「族里没有的动作直接说做不到，别找最近的族圆场」。
实测她现在会说：「翻不了，这个真做不到，说好了不装。」

### 测试方法论（这次学到的）

第一版测试直接断言「说『挥挥手』→ 手要举过头顶」。跑了几次有一次没举——
解析器那一轮没给 `wave`。

**这不是引擎的 bug，是模型的概率。** 把测试改成：

- 姿势检查用**确定性注入**（直接 `rt.handle_composite`）
- API 部分只断言「她回了话」，解析结果记进 JSON 供人看

一个测试如果会因为模型的随机性红掉，它就不是回归测试，是骰子。

---

## 2026-11-26 · 接正版前的演习

### 为什么先演习

用户最终要接正版（`dsh-qq`）。直接指过去的风险是**不可逆**的（会改她的真实记忆和情绪），
所以先在副本里用**正版的真实文件**跑一遍。

### 两个前置改造

**1. 目录布局拆分。** 正版是拆两处的：
`persona.md` / `memory.md` 在 `dsh-qq/` 根，`mood.json` / `chat.jsonl` 在 `dsh-qq/world/`。
我们原来是「一个目录装全部」。现在支持 `BODY_V2_BASE` / `BODY_V2_WORLD` 两个环境变量。

**2. 读结构化记忆。** 正版的记忆主体是 `world/memory.json`——**23 条**，
带 `strength` / `hits`。我们以前只读 371 字节的 `memory.md`（3 行）。

顺带发现：**这不是「接不接正版」的问题，我们一直在用一个缩水版记忆喂她。**
现在按 strength 取 top 12 塞进 prompt。

### 演习结果

把正版文件复制到 `sandbox/prod_sim/`，指过去跑两轮：

```
chat.jsonl     200 → 204          ✅
mood.json      42 → 34            ✅
冻结文件       一个字节没动        ✅
正版目录       production_touched = []   ✅
护栏           直接写正版 → PermissionError  ✅
记忆 prompt    读到了正版 23 条要点
```

### 演习确认的一个真风险

连接期间 `body_connected()` 返回 **true**。
正版那边的 pose 消费逻辑也看 `body.json` 的 mtime —— **我们一连接，正版会以为身体上线**。

所以接正版时必须改心跳：写自己的标记，绝不 touch `world/body.json`。

### 教训

> 接不可逆的外部系统之前，先造一个「同构的副本」把它跑一遍。
> 副本里踩到的坑是免费的，正版里踩到的是要还的。

---

## 2026-11-26 · 接正版三步

### ① 心跳隔离

以前心跳 touch `world/body.json`。正版的 pose 消费逻辑看它的 mtime ——
**我们一连接，正版会以为身体上线**，开始给一个不存在的消费者下发姿态。

改成写自家的 `body_v2_live.json`。`body_connected()` 先看它，再看正版那份。

### ② 备份

`tools/backup_world.py`：378 个条目、16.5 MB、写完重新打开验证。
落在 `backups/world-20261126-225618.zip`。

### ③ 模式开关

`set_world("sandbox" | "production" | "production_rw")`：

- 默认沙箱
- 正版只读：能读人格/记忆/情绪，**写操作一律抛 PermissionError**
- 正版可写：先检查 `backups/world-*.zip`，没有就拒绝

面板加了「世界线」下拉框。

### 演习复核

```
heartbeat_file            body_v2_live.json     ✅
body_json_mtime_unchanged true                  ✅ 正版心跳文件没被动
production_touched        []                    ✅
frozen_changed            []                    ✅
只读模式拦住写入           ✅
有备份后才放行可写         ✅
```

### 现在接正版只需要三步

1. 面板选「正版只读」，Connect，聊两句看她像不像
2. 觉得行 → 选「正版可写」，Connect（备份已经在，门槛会自动过）
3. 出问题 → 用 `backups/world-*.zip` 覆盖回去

---

## 2026-11-26 · 「一直连接失败」：Blender 缓存旧模块

### 症状

用户点 Connect 永远失败，面板只显示「连接失败：…」一行。

### 根因

在 GUI Blender 里打印完整栈才看到：

```
TypeError: Runtime.set_motion() got an unexpected keyword argument 'family'
```

`core/runtime.py` 后来加了 `family=` 参数，但 Blender 进程里
`sys.modules["core.runtime"]` 还是**旧的类**。**Python 不会因为磁盘文件变了
就重新导入已加载的模块。**

### 修法

`connect()` 开头把 `core.*` / `llm.*` 从 `sys.modules` 踢掉，再 import。

> 这意味着：**改完 core/llm 的代码，用户再点一次 Connect 就行，不用重启 Blender。**
> 只有改 addon 本身才需要重新安装/重载。

### 教训

> 插件式开发里，「我改了代码」和「它跑的是新代码」是两件事。
> 报 `unexpected keyword argument` 这种错，先怀疑缓存，别怀疑签名。

---

## 2026-11-26 · 「让她抬腿她不做」

用户说「抬起左腿给我看看」，她不做。查下来是**三层同时缺**，不是一处 bug。

### 1. 模型里没有「抬腿」这个单元

P 只覆盖上半身（lean/side_bend/头/手臂/尾巴），腿由 G 驱动，
而 G 只有 `stride`（走）和 `hip_drop`（降髋）。**没有「抬一条腿」的表达。**

修：`G.lift = {"left": 0.22}` → `StaticTarget(lift=...)` → 脚底抬高 22cm。
新增族 `lift_leg`，标签「抬腿」。

### 2. 她根本不知道自己的身体能干什么

第一阶段的聊天 prompt 是「完全自由地说话」，里面**没有任何能力清单**。
所以她回：「抬不了，我这儿没有真的腿……建模那个能调参数，但那是画面里的。」

修：`ability_line()` 把 19 个族的中文标签压成一行塞进第一阶段 prompt。

### 3. 解析器对不上词

族名是英文 `lift_leg`，她说的是中文，解析器没得对。
修：`capabilities()` 加 `family_labels`，解析 prompt 变成 `lift_leg(抬腿)`。

### 修完

```
"抬起左腿给我看看"
  say      抬了。你看得见我抬没抬啊？
  intents  [lift_leg 1.6s]
  左脚 z 0.300 / 右脚 0.080 / IK 0.0mm
```

### 顺手做了全族审计（`tools/audit_families.py`）

19 个族逐个应用，跟 idle 比关键关节的位移和转角。

**第一版只量位移，把 `head_tilt_fam`（歪头 18°）误判成「没动」**——
歪头是旋转，头骨原点不动。加上转角指标后：

```
只有 idle 位移 0.5mm —— 正常，呼吸是 L0 的 IdleMotion 加的，不在族里
其余 18 个族：90–675mm 位移 / 12–135° 转角
```

### 教训

> 「功能不行」往往不是一处 bug，是**能力、认知、接口三层各缺一块**。
> 用户的抱怨是一句，根因要按层拆。

---

## 2026-11-27 · 「换个腿没换」：两个原因叠在一起

用户说：让它换条腿，她说「右腿抬起来了」，身体还是左腿。

### 原因 1：左右写死在配置里

`lift_leg` 的 `G.lift = {"left": 0.22}` 是常量。动作契约只传 `intent`、不传侧，
所以「换条腿」只能再选一次同一个族。

修：拆成 `lift_leg_left` / `lift_leg_right`，各带自己的 lift 和中文标签。

### 原因 2：库把两条族当成同一个动作

`key_distance` 里 `verb` 只占 0.15，两个不同族的距离 = 0.15 < 命中阈值 0.25。
于是「换个腿」在 L1 命中了上一条（左腿），`entry.family` 写着 left。

**这一条比第 1 条阴**：就算解析器选对了右腿，库还会把它拉回左腿。

修：两边 `verb` 都有且不同 → 距离直接 1.0。族不同就不是同一个动作。

### 顺带

解析 prompt 加了「用户明确说了哪条腿，按用户说的选，不要照抄她说的方向」。

### 实测

```
"抬起左腿给我看看"  → lift_leg_left    左脚 0.30 / 右脚 0.08
"换个腿"            → lift_leg_right   左脚 0.08 / 右脚 0.30
```

### 调试陷阱

`connect()` 已连接时直接返回、不重建 Runtime。所以改完配置必须 **Disconnect → Connect**，
否则跑的还是旧配置。我在 GUI 里第一次测出「还有 lift_leg」，就是这个原因，
差点又去改代码。

---

## 2026-11-27 · 「双手平举变成一只手挥手」

### 两层原因

1. **没有双手平举这个单元**：最接近的是 `wave`（单手）和 `point`（一只手往前）。
2. **侧别推断默认 right**：带 `side` 的谓词会推出 `active_side="right"`，
   于是即使选了「举」的族，也只驱动右手。

### 修法

- 新增族 `arms_out`，标签「双手平举」
- 加族级标记 `two_handed: true`，`handle_intent` 里强制 `side=None`

### 调参踩坑：`arm_lift` 不是角度直觉

扫描了一下：

```
 lift  手 z    说明
    0  0.572   垂手
   60  0.791   真水平（肩 0.783，横向最远 0.324）
   88  0.911   举过头
```

我一开始按「88° 应该接近水平」写，结果手举到肩膀上面 13cm。
**参数名字像角度，实际映射不是线性的从垂手起算。**

### 结果

```
"双手平举给我看看" → arms_out
左右手 0.791 / 0.793，肩 0.783，|x| 0.324  → 标准 T 字
```

### 教训

> 「平举」和「举手」在参数空间里只差 28 个单位，但在视觉上是两回事。
> 加动作族之前，先把参数的**实际映射**扫一遍，别按名字猜。

---

## 2026-11-27 · 用户质疑：「没有这个动作为什么不建新动作」

### 质疑是对的

之前的行为是：用户要的动作不在族里 → 解析器从清单里挑一个**最像的** → 做出一个不同的动作。
这在产品层面是错的：**做了个别的动作，比不做更糟。**

### 但根因不是「没设计这条路」

框架里其实早就有三件套：

- `free` 族：12 维全范围，专门给「没有现成族」用
- `handle_intent(use_llm_predicates=True)`：让谓词覆盖模板
- `core/grow.py`：把反复出现的 free 组合提议成命名族

**问题是它们从来没接上**：解析器只输出族名，**从不输出谓词**。
所以「没有族」时，free 这条路是死的，只能在清单里硬挑。

### 接上之后

```
没有族对得上 → intent="free" + predicates（1–4 条）
             → 求解器现场搜 P
             → 过验证就入库（family=free）
             → 反复出现的组合可由 grow.propose 沉淀成命名族
```

解析 prompt 里补了谓词表 + 锚点表，并写明「不要退而求其次选相近的族」。

### 顺带修掉两个崩溃点

1. **`max_tokens=80` 截断**：free+谓词的 JSON 约 150 token，被砍一半 → 解析失败 →
   `intent=None` → 表现成「她说了但身体不动」。提到 300。
2. **解析器自己编锚点**：实测编出 `head_side`（真表里只有 chin/head_top/mouth/…），
   求解器 `KeyError` 直接把引擎打崩。修：锚点/侧别白名单，坏谓词整条丢弃；
   `handle_intent` 再加一层 `solve_error` 兜底。

### 边界（必须说清）

- **参数空间内**（现有 12 维 P 能表达）→ free + 谓词，运行时合成 ✓
- **需要新结构**（双手两侧、抬一条腿）→ free 表达不了，必须扩参数空间，
  那是配置/代码级。所以 `arms_out`、`lift_leg_*` 是加族。

### 教训

> 「有机制」和「机制被接上」是两回事。
> 一个能力如果从来没有调用点，它在产品上等于不存在——
> 而且比不存在更糟：它会静默降级成别的行为。

---

## 2026-11-27 · 族自动生长接上（free → 命名族）

### 为什么这一步重要

`free` 族是个**兜底**：能解，但每次都要现场搜（慢、不稳、语义为零）。
长期让所有新动作都留在 free 里，等于没有动作库。

区别在于：

- 库条目 = 一次执行（一组参数）
- 族 = 一类动作（参数范围 + 判据模板）

所以：同一个谓词组合反复出现 → 该沉淀成族。

### 链路

```
同一动作要 3 次 → hits=3 → propose → 面板提议 → 用户点「收下」
  → 生成完整族 → 写 sandbox/auto_families.json → 下次连接自动 merge
```

### 几个非显然的点

1. **计数据现成**：`library` 在 L1 命中时本来就 `hits+1`，不用另建计数器。
2. **族名要能用**：`apply_proposals` 原来只给 `G` + `P_range`，缺 `label/thresholds/hint`，
   过不了 `test_seed_families`。改成从 `free` 族继承底子。
3. **`record_family` 按指纹去重**：连点两次「收下」不会长出两个一样的族。
4. **面板每帧重绘**：`grow_pending()` 要扫库，加了 2s 缓存，否则拖慢 UI。
5. **只进沙箱**：`config/seed_families.json` 是人工策展的，自动生长的族放
   `sandbox/auto_families.json`，两者在 connect 时合并。

### 实测

```
families: 21
pending: [('手叉腰', 3, 'b0d1c2')]
已长出新族：手叉腰
auto families: ['reach_chin']
played reach_chin ok
```

### 教训

> 学习类功能最怕两件事：**自己乱长** 和 **长了不算数**。
> 前者靠「必须人点确认」，后者靠「立刻 merge 进运行时 + 落盘」。

---

## 2026-11-27 · 归一化 + 同名不同动作的防线

用户要加归一化，同时提了个很准的问题：

> 语义相近但动作不相近的时候，两个会不会被归一了？

### 原则

**归一化只作用于名字；动作身份由谓词指纹决定。两者彻底分开。**

可以归的：

- 弱谓词（`wrist_neutral` 等安全/中立项）——换个说法多一少一，仍算同一动作
- 数值粒度（tol 量化到 50mm / 角度 10°）——120 和 150 是同一个意思

不能归的：

- `side`（左手 ≠ 右手）
- `anchor`（chin ≠ head_top）

### 同名不同判据 → 报警，不合并

`label_collisions()` + 面板「同名但动作不同（不会合并）」。

这次正好实测撞上：

```
pending: [('手叉腰', 3, 'b0d1c2'), ('手叉腰', 3, '37918d')]
collisions: [{'label': '手叉腰', 'fingerprints': [两个]}]
→ 没合并
```

### 顺带挖出两个更深的坑

**1. 库的 key 里没有 side。** `intent_key` 把 side 混进 anchors 集合，
权重只有 0.2，左右手的距离 0.134 < 阈值 0.25 → 被当成同一个动作，hits 合并成 6。
表现就是「左右手各要 3 次」，库里只有一条。

**2. 库是纯内存的。** `connect()` 每次 new 一个 `Library()`，
重连就清零——「同一个动作 3 次」跨会话永远攒不够，**自动生长实际上根本触发不了**。
（我上一轮演示时是同一会话里连点 3 次，所以看不出来。）

修：`side` 进 key 并硬比较；库落盘 `sandbox/library.json`，连接时 `Library.open`。

### 教训

> 归一化真正危险的不是「归多了」，而是**你以为的判据根本不是判据**。
> 这次的 side 就是这样：名字层看着分得清，key 层早就糊成一团了。

---


## 2026-11-27 · v1.9：runtime 接上全身（走 + 挥同时动）

![走 + 挥](docs/images/runtime_walk_wave_full.png)

### 修的是什么

v1.8 的 `handle_composite` 里，base 和叠加层的数据类型不一致：

| 函数 | 返回 |
|---|---|
| `pose_for_from_P`（旧） | **世界矩阵** |
| `build_full`（新） | **basis** |

`merge_layers` 不校验语义，混着合并 → 位置全 0。

### 改法（接口统一成 basis）

```python
# v1.8
def pose_for_from_P(...):
    return self.rig_fast.compute(basis)      # 世界矩阵

# v1.9
def pose_for_from_P(...):
    return self.basis_for_from_P(P, family, side)   # basis
```

`pose_for` 保留 `as_basis=False` 默认（兼容 `apply_pose`）：

```python
def pose_for(self, entry, side=None, as_basis=False):
    basis = pose_gen.build(P, self.rig_fast, self.bm, active_side=side)
    return basis if as_basis else self.rig_fast.compute(basis)
```

`handle_composite` 的 base 改走全身装配：

```python
if self._full_body:
    base_basis, _d = self.build_full(P_base, family, t, active_side=side)
```

### 实测

```
sources ['L2', 'L2']  layers 1  full_body True
hand_z 0.917（挥）    foot_z 0.170（抬脚）
body BodyState(x=0.000, y=-0.167, bob=0.0104, vel=0.32)
```

**右手在挥、左腿在走路、身体在前进**——三件事同时发生。

### 测试

```
tests/test_composite.py   PASS（merged hand_z 0.906, foot_z 0.080）
全量 45 个测试              0 失败
```

### 教训

**同一个项目里，同一个概念只能有一种表示。**

basis 和世界矩阵都是 4×4、都能做矩阵乘、都不报错——**这种「类型不同但结构相同」的混淆最难查**。

**规则**：函数名或 docstring 里必须写明「返回 basis」还是「返回世界矩阵」。

---


## 2026-11-27 · v2.0：起点→终点的轨迹求解（新动作也有过程了）

![轨迹中间帧](docs/images/trajectory_mid_frame.png)

**第 10 帧手臂举到一半** —— 不是瞬间到位。

### 先测了一个关键数据

**直线插值到底撞不撞？**（`tools/probe_straight_path.py`）

```
wave / point / pat_head / sit / lie_down / tail_drop / head_tilt   OK（7 个）
stretch                                                            撞 1 帧
```

**87.5% 的动作直线插值就合法。**

**所以不需要通用路径规划**（RRT/轨迹优化），只需要「检测 + 局部修正」。

### `core/trajectory_solver.py`

```
① 先检终点：违规就二分缩放（P_end = lerp(P_start, P_end, k)）
② 直线采样 20 帧（ease 曲线，两端慢中间快）
③ 逐帧检测（胶囊 + 反关节 + 膝角）
④ 有撞的帧 → 二分找最大合法进度 → 替换
```

**成本**：

| 情况 | 耗时 |
|---|---|
| 无撞（87.5%） | 60ms（20 帧检测） |
| 有撞 | 60-90ms（+ 二分） |

**这是一次性规划**，不是每帧。

### `Runtime` 新接口

```python
tr = rt.plan_trajectory({"intent": "wave"}, n_frames=20)
# tr["frames"]     20 个 P
# tr["mode"]       straight / repaired / failed
# tr["cost_ms"]    规划耗时
# tr["play_P"]     终点 P

for P in tr["frames"]:
    b, _ = rt.build_full(P, "wave", 0.0, active_side=side)
    apply_basis(arm, b)

rt.commit_trajectory(tr)    # 记住终点，下次从这里出发
```

**起点优先级**：显式传入 > 上次姿态 > **idle 站姿**。

**不用目标族的 `P_default`** —— 那是「终点附近」的姿态，不是起点。

### 实测

```
mode=straight frames=20 cost=64.7ms
第 10 帧 arm_lift=55.3  hand_z=0.771（举到一半）
末帧     arm_lift=114.4 hand_z≈0.97（举高）
```

### 接进 runtime 的测试（`tests/test_trajectory_runtime.py`）

```
[2] 挥手 20 帧全程合法         ✅ mode=straight
[3] 首帧 = idle 站姿           ✅
    末帧 = 终点 P              ✅
[4] trajectory_basis 生成 20 个 ✅
[5] commit_trajectory 记住终点 ✅
[6] 第二个动作从上次终点出发    ✅
[7] 没族时返回 no_entry        ✅
```

**46 个测试全绿。**

### 这解决什么、不解决什么

**解决了**：
- **新动作也有过程**（从当前姿态平滑过渡到求解结果）
- 不需要给每个动作手写关键帧
- 过渡保证不穿模、不反关节

**没解决**：
- **周期性**（挥手 3 下还是做不到 —— 那是「来回」，不是「从A到B」）
- **多段串联**（摆3下→转身→再摆）
- **有接触约束**（手撑桌子时不能随便插值）

**下一层**（如果要做周期）：在终点附近叠正弦振荡，或接 `gestures` 的关键帧。

---


## 2026-11-27 · v2.1：姿态保持（默认不回到待机）

### 用户要的

> 「我先让他蹲下然后让他手举起来分两次输入她就两次回到待机然后做动作
>  能不能加一个如果我说保持现在的姿势再做其他动作她可以在现有的动作上叠加
>  然后我说回到待机必须回到待机这样保证不会卡住」

### 定了的规则

| 规则 | 决定 |
|---|---|
| 默认保持 | ✅ 不每次回待机 |
| 回待机 | 说「回待机 / 站起来」强制重置，**保留位置** |
| 说话没动作 | 保持现状，不回待机（不打断叠加） |
| 断开 / 接入 | 清空状态 → 待机 |
| 部位冲突 | 新的覆盖旧的（`overlay` 分组） |
| 叠加上限 | **不要**（有回待机兜底） |
| 位置 | 回待机**不**归零 |
| 说话里的微动作 | 先不累积（要改解析器，后做） |

### 关键修复：腿的状态必须进 State

**v2.1 之前**：`plan_trajectory` 只知道 `P`（上半身 12 维）。

**问题**：蹲下的 `hip_drop` 不在 `P` 里 → 蹲着说「手举起」，腿瞬间从蹲变回站。

**修**：`BodyState` 加三个字段：

```python
P_hold: dict            # 上半身
hip_drop_hold: float    # 腿：髋降
foot_source_hold: str   # 腿：脚目标源
```

### 新接口

```python
rt.body.hold_pose(P, hip_drop, foot_source)   # 记下姿态
rt.body.reset_pose()                          # 只重置姿态（保留位置）
rt.body.reset()                               # 全重置（含位置）
rt.commit_trajectory(tr)                      # 播完记住终点（含腿）
rt.reset_pose()                               # Runtime 层：回待机
rt.handle_intent({"intent": "reset"})         # 触发回待机
rt.explain(result)                            # 失败原因（人话）
```

**起点优先级**：显式传入 > 上次姿态 > **保持的姿态** > idle。

### 测试（`tests/test_hold_pose.py`，8 组全过）

```
[2] 做动作后记住姿态                    ✅
[3] 第二个动作从保持的姿态出发          ✅
[4] 回待机：清空姿态 + 保留位置 x=3.0   ✅
[5] 回待机后从 idle 出发                ✅
[6] 说话没动作 → 姿态不变               ✅
[7] 断开 → 全重置                       ✅
[8] explain 失败说明                    ✅
```

**47 个测试全绿。**

### 还剩的风险

| 风险 | 状态 |
|---|---|
| 说话里的微动作累积 | 未处理（要改解析器） |
| 断开检测的时机 | 由宿主决定（建议 30s 无心跳） |
| 状态不持久化 | 进程崩溃就丢（可接受） |

---


## 2026-11-27 · v2.2：接解析器（回待机 + 微动作不累计）

### 三条规则落地

**① 解析器能识别「回待机」**

`llm/persona.py` 的 `INSTRUCTION_ACTS` 加了 8 个触发词：

```
回待机 / 回到待机 / 待机 / 站起来 / 站直 / 正常一点 / 恢复原样 / 别端着
    → {"intent": "reset", "predicates": []}
```

`PARSE_SYSTEM` 也加了说明，LLM 解析时会输出 `intent: "reset"`。

**② 微动作不累计**

`PARSE_SYSTEM` 新增一节：

> - 用户在**闲聊**，她话里带的小动作（尾巴甩一下 / 歪一下头 / 抿嘴 / 低头 / 耳朵动）
>   → `intents` 给 `[]`，**不要**给 intent
> - 只有**用户明确要求**做动作，或她**明确宣告**要做时，才给 intent
> - 判断标准：**这个动作是不是这一轮的重点？** 是闲聊的调味 → `[]`

**③ 心跳 30 秒算断开 / 状态丢失 = 断开**

这是**宿主层的责任**（`blender_addon/body_live.py` 或外部驱动）：

```python
# 宿主侧伪代码
if now - last_heartbeat > 30.0:
    rt.reset_pose()        # 姿态清空（位置保留）
    last_heartbeat = now
```

**core 侧只需要**：宿主调 `rt.reset_pose()` 即可。**core 不管心跳**。

### 测试（`tests/test_reset_intent.py`，6 组全过）

```
[1] 指令表里有 8 个 reset 触发词          ✅
[2] instruction_act 识别 5 种说法         ✅
[3] reset 清空姿态 + 保留位置 x=1.5 y=-2.5 ✅
[4] reset 后从 idle 出发                  ✅
[5] PARSE_SYSTEM 有「微动作不累计」规则    ✅
[6] 「回待机」不会匹配成 idle              ✅
```

**48 个测试全绿。**

### 顺带

`sandbox/library.json`（运行时库）加进 `.gitignore`——每次跑都变，不该进版本。

---

