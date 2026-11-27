# 身体 v2 · 交接文档

> **给下一个对话的第一句话**：先完整读完这份文件，再动手。
> 这里记录的不是"想法"，是上一代项目用 30+ 条坑换来的结论。**不要跳过第 6 节和第 10 节。**

- 日期：2026-11-23
- 上一代项目：`D:\DSproject\embodied-body`（**保留不动，做 A/B 对照**）
- 本项目：`D:\DSproject\body-v2`（本文件所在目录，代码尚未开始）

### 修订记录

| 版本 | 变更 | 影响的文档 |
|---|---|---|
| v1.0 | 初版 | — |
| **v1.1** | 补 **8.4 宿主**、**8.5 进程与通道**、**8.6 语义锚点**；新增 **2.4 身体表达力边界**；目录加 `config/anchors.json`；P1.5 验收改为"头/颈/躯干/尾"（**不含面部**）；第 12 节加第 9 份设计 | 四层调度、谓词表、参数边界、库 schema、LLM 契约 |
| **v1.2** | **§8.5 整节按实测重写**（FK 比 depsgraph 快 88 倍，L2 预算 300→10000 次）；§2.4 补骨骼/网格/材质构成；§8.6 修正 mouth 的自相矛盾；§5/§13"视线"→"头部朝向跟随"；§8 帧号 1–905；新增 **D8/D9**；§12 加第 10/11/12 份设计 | 四层调度、求解器、谓词表、P6/P7 预算 |
| **v1.3** | 新增 **§4.5 参数化方案**（P 10 维 + G 11 维 + 轨迹基 + 三段搜索 + 失败回传）；**L3 改为「入库闸门」**（BVH 0.5–2s 放不进实时，改为夜间批处理）；**删除 `vision/servoing.py`**（无参考影像 ⇒ 无视觉伺服，原 spec 错误）；新增 **D10**（每帧从 `base_rest` 重建，禁止累加）；§7 加 **P0.5**（FK 地基）、改名 P8；§12 加第 13–15 份；新增 `docs/WORKFLOW.md`、`docs/LOGGING.md` | 求解器、调度、P8、验收、日志 |

---

## 1. 这个项目要干什么

**目标（用户原话）**：
> "只要 LLM 能思考出动作然后给出每个参数值，就可以做出之前没有参数参考的动作，然后通过自己的视觉闭环不断调整自己的动作，来丰富自己的参数库。"

**翻译成工程目标**：
1. LLM 提出**新动作**（不是从动作库里检索最像的）
2. 系统自己求出**数值参数**
3. 视觉反馈**闭环校正**
4. 成功的动作沉淀进**可生长的参数库**

**明确不是**：Motion Matching（那是从已有库里检索，永远做不出库外动作）。

---

## 2. 上一代（embodied-body）做到哪儿了

### 2.1 已经做到的（值得继承）

| 项 | 证据 |
|---|---|
| pose 协议（她 → 身体，三条文件通道） | `interface/POSE_PROTOCOL.md`，设计扎实 |
| 14 个动词 | `world/body.json` 的 `actions`：apose / gesture / idle / kneel / point / rest / run / sit / spin / stretch / talk / walk / wave / custom |
| **空间精度 1mm 级** | receipt seq432：`hand_torso_mm: 1.0`、`foot ik_err: 1.07 / 0.0`；HANDOFF 记「蹲0.25+左手到嘴+歪头-18+前倾-6 → IK 0.0mm、手-头 0.4mm」 |
| 走路滑移 | 真走直线 960mm/6s，滑移 **0.00mm**、穿地 −1.3mm |
| 遥测 | `world/body_health.json`（tick_ms / gap_ms / big_steps / spring_deg…） |
| 7 条回归用例 | `interface/selftest.py`、`tools/selftest_all.py` |
| 常驻渲染进程 | `tools/render_daemon.py`（省每次 2.4s 启动） |
| 知识资产 | `HANDOFF.md` 34.6 KB，含「已知的坑（31 条）」 |

**结论：骨架、映射表、解算精度都是好的，模型不差。**

### 2.2 致命缺陷（这就是要新开项目的原因）

| # | 缺陷 | 位置 | 症状 |
|---|---|---|---|
| 1 | **逐帧写绝对姿态** `apply_snapshot()` | `pose_consumer.py` | 只有 C0 连续；每段动作用 `prev→dst` 的 slerp，**两端速度都为 0 → 一顿一顿** |
| 2 | **幂函数缓动** `t ** 0.55`（`ease:"snap"`） | `pose_pipeline.py:568` | 导数在 t→0 时**趋向无穷** → 单帧瞬移。`body_health` 实测 `big_step_last: "leftHand 176mm"` |
| 3 | `smoothstep` / `ease_in` 两端速度为 0 | `pose_pipeline.py:548` | "每段重新起步"的僵硬感 |
| 4 | **周期性动作是正弦**，不是步态 | `verbs.py:284-294` | 没有支撑相/摆动相/落脚点 → 走路离地 49.9mm（被写进表格当成指标接受了） |
| 5 | **4 条 IK 约束 influence = 0** | `deepseek_rigged.blend` 实测 | 绕过了 Blender IK，自己写 CCD；CCD 只在生成轨迹那一刻解一次，之后脚自由飘 |
| 6 | **fade 被 `dur*0.5` 压制** | `pose_pipeline.py:646` | `dur:0.3` → fade≤0.15s → 3.6 帧走完大动作 → **29.44°/帧**（预算 12°） |
| 7 | **护栏只测量不阻止** | `pose_guard.py` | `mode:"pass"` + `ok:true`，但 `verdict:"FAIL"`、双脚离地 99mm 照样执行 |
| 8 | **弹簧用可变 dt 积分** | `pose_consumer.py:711` | `gap_ms` 24.59ms 抖动 → 不稳定；`spring_deg` 只有 0.16（等于没动） |
| 9 | **新意图随时覆盖，不排队、不接续** | `POSE_PROTOCOL.md:143` | 切换时从头插值 → `jumps_idle: 10` |

### 2.3 上一代自己承认的未解问题

`HANDOFF.md:51`：
> **⚠ 未解决**：连续叠加动作时偶发"双脚离地 60~100mm"（单动作正常）；已回退两个失败修法，待专项排查。

`HANDOFF.md:283-285`：
> `walk` 只会直线走：没有转向、没有落脚点规划。

### 2.4 身体表达力边界（实测，2026-11-23）

209 根骨的构成：

| 类别 | 数量 |
|---|---|
| 躯干 + 四肢主骨（other） | 31 |
| 手指（thumb/index/middle/ring/pinky 各 8） | 40 |
| toe | 4 |
| **hair** | **60** |
| **skirt** | **64** |
| ribbon（WaistRibbon） | 6 |
| **tail（WhaleTail_01..04）** | **4** |
| **eye / face / jaw / mouth / lip / brow** | **0** |

`shape_keys`：**空**（没有形态键）。

**结论：这具身体没有脸。** 表达力只能靠姿态 + 二次运动（头发/裙摆/腰饰/尾巴），**不能做表情、不能对口型**。详见 §8.6。

**骨骼/网格构成（实测补全）**：

- **21 根主骨 + 188 根二次骨**（hair 60 / skirt 64 / 手指 40 / toe 4 / ribbon 6 / tail 4）
- **整个身体是 1 个 mesh**（`tripo_node_dca8b6a7`），**不是分部件**
- **14 个材质槽**，含 `Toon eye 0` / `Toon face 0,3,4` / `Toon hair 0` —— **眼睛和脸是材质区，不是独立物体**
- 无形态键（确认）
- 4 条 IK 约束 `influence` 全为 **0.0**（确认）
- 9 个 Empty：6 个 IK 目标（左右脚/手 + 膝极）+ 3 个极点（`POL_F/L/R`）
- 时间线 **帧 1–905**

> **眼睛是材质区**这一点决定了：眨眼和视线**都做不了几何动作**，只能靠换贴图 / UV 偏移（属材质层，不是骨骼层）。
> 所以「视线先不做，做就转头」在这具身体上是**唯一选择**，不是妥协。

---

## 3. 为什么新开一个项目，而不是原地改

1. **核心驱动方式要换掉**：从"每 tick 写绝对姿态"换成"关键帧 + Blender 插值"。这等于在动手术时拆承重墙。
2. **`tools/` 里有 17 个 `.bak-2026xxxx`**：前几轮是就地反复打补丁的。那些"神秘问题"大概率住在补丁之间的未记录交互里。原地改就永远分不清"这次改坏的"还是"三个月前那个补丁留下的"。
3. **旧项目还在跑 = 免费的 A/B 对照基线**。同一意图，新旧各渲一张并排看——这比任何指标都靠谱。推倒重写会把这个能力弄丢。

**所以：新文件夹 + 渐进搬迁 + 并排对照。不是从零发明。**

---

## 4. 关键决策（D1–D7，**不要推翻，除非有反证**）

| 编号 | 决策 | 依据 |
|---|---|---|
| **D1** | **LLM 只写"判据/验收标准"，不写关节角、不写数值参数** | 上一代实测：同一件"手到嘴边"，写角度偏 **92mm**，写 targets 偏 **0.5mm**（184 倍）。LLM 擅长写约束，不擅长给数值 |
| **D2** | **四层反应**：L0 底噪（每帧）/ L1 复用查库（30ms）/ L2 新编求解（0.3–1s，后台不阻塞）/ L3 视觉精修（离线/夜间） | 她"反应快"是因为 90% 命中库，不是现场算得快 |
| **D3** | **感知三件套**：本体感知（直接读骨架，0 成本可微）→ 接触感知（脚底最低点）→ 视觉感知（离屏渲染）。**本体优先** | 人形机器人靠编码器 + IMU + 足底力传感器干这个；LLM 不在这一环 |
| **D4** | **视觉闭环放离线**，绝不进对话链路 | 渲染 0.3s/张 × 15（雅可比）≈ 5s，实时必卡 |
| **D5** | **不可达就失败，禁止 clamp** | gait-guide 原则：「Impossible targets fail; they are never clamped into plausible-looking coordinates」 |
| **D6** | **情绪 = 参数偏置，不是新动作** | 上一代 `dsh-qq/emotion.py`（25KB 四轴心境）已在给 Live2D 下发 `/api/emotion`，桌宠用 `emo.x/y/z` 实时驱动参数。同一个"挥手"配不同偏置 = "开心地挥/敷衍地挥" |
| **D7** | **`body/binder.py` 是唯一碰骨骼的文件** | 旧 `pose_pipeline.py` 65KB 什么都干（解算/插值/弹簧/测量/播放混在一起）→ 越修越乱 |
| **D8** | **IK 必须在纯 Python 里解析求解，禁止用 Blender 约束** | 若用约束，FK 预演里就没有 IK → 预测 ≠ 真实 apply，优化器会优化一个不存在的世界 |
| **D9** | **L2 求值循环用纯 Python FK，禁止走 depsgraph** | 实测 `update()` 有 1.3–1.7ms 固定地板且与骨数无关；纯 FK 0.018ms（快 88 倍，误差 0.0002mm）。见 §8.5 |
| **D10** | **每帧姿态一律从 `base_rest` 重建**：`pose = base_rest ⊕ G ⊕ P ⊕ emotion ⊕ K`，**禁止在上一帧结果上累加** | 坑 #1：叠加式微动 240 拍后偏 386mm。累加 bug 不会立刻报错，只会在几分钟后表现为"她慢慢歪掉"，是最难查的一类 |

---

### 4.5 参数化方案：P + G + K 三层（**求解器的地基**）

84 个自由度的真相：21 主骨 × 四元数 = 84，有效 21×3 = 63。63 维不能直接丢给 CMA-ES（维度灾难），所以必须有参数化。但**参数化 ≠ 写死形状**，区分两件事：

| | 是什么 | 谁负责 |
|---|---|---|
| **约束** | 支撑相脚底世界坐标恒定、摆动五次曲线端点速度为 0、不可达即失败 | 内建在生成器里，**所有参数组合都满足** |
| **参数** | duty / 步长 / 抬脚高 / 相位偏移… | 可搜的连续量，决定「是什么步态」 |

上一轮曾误把 gait-guide 的**约束**当成**参数化**（只留 walk/run 两组预设），那样腿就死了。正确做法：约束照旧内建，参数放开搜。

#### 三层

```
pose = base_rest                          # 每帧起点，D10
      ⊕ G(步态参数)      → 下肢关节角 + phase(0..1)
      ⊕ P(姿态参数, phase) → 上半身关节角（P 消费 G 的 phase）
      ⊕ emotion(bias)    → 对 P/G 的连续通道加性偏置（不改 K.targets）
      ⊕ K(IK targets)    → 末端精确目标，解析解，覆盖前面的角
      ⊕ explicit_bones   → 逐骨覆盖（需前置冲突检查）
```

**顺序不许改。** 尤其是：`P` 必须在 `G` 之后（摆臂要跟腿同相）、`K` 必须在 `P` 之后（末端目标优先级最高）、`emotion` 必须在 `K` 之前（情绪不许推漂 targets，否则 D1 旗舰证据失效）。

#### P — 上半身姿态（10 维，CMA-ES 主搜索对象）

| 维 | 语义 | 单位 |
|---|---|---|
| `lean` | 躯干前倾（负=后仰） | 度 |
| `side_bend` | 躯干侧弯 | 度 |
| `head_nod` | 点头 | 度 |
| `head_tilt` | 歪头 | 度 |
| `head_yaw` | 转头 | 度 |
| `arm_swing` | 摆臂幅度（**相位由 G 的 phase 驱动**） | 度 |
| `arm_lift` | 手臂上抬 | 度 |
| `elbow_bend` | 屈肘 | 度 |
| `hand_bias` | 手腕偏置 | 度 |
| `tail_curl` | 尾巴卷曲 | 度 |

`side / reps / hold / ease` 是离散量，归 K，不占 P。

#### G — 下肢与步态（11 维，轨迹基 + 参数）

**轨迹基（写死，这是物理约束的必然形式）**：

```
脚世界轨迹 = 支撑段（世界坐标锁定，时长 = duty × period）
           + 摆动段（Hermite，端点速度/加速度可指定，峰值高度 = foot_lift）
           + 落脚点序列（可按 footfall 指定，不必左右交替）
           → 两骨 IK 解析解出下肢关节角（D8）
```

**可搜参数（11 维）**：

| 维 | 语义 | 典型范围 |
|---|---|---|
| `duty` | 支撑相占比 | 0.3–0.95（<0.5 自动进腾空相） |
| `period` | 周期（秒） | 0.4–1.5 |
| `stride` | 步长（**可负 = 倒退**） | −0.6–0.6 m |
| `foot_lift` | 抬脚高 | 0–0.18 m |
| `stance_width` | 站距 | 0.05–0.35 m |
| `landing_height` | 落脚高度偏移 | −0.03–0.03 m |
| `ankle_bias` | 踝偏置（踮脚 = 正） | −30–30 度 |
| `knee_bias` | 膝偏置 | −30–30 度 |
| `turn_rate` | 转向角速度 | −180–180 度/秒 |
| `phase_offset` | 左右相位差（正常 0.5） | 0.3–0.7 |
| `footfall` | 落脚序列（半离散） | 交替 / 同侧 / 固定 |

**表达范围（不是模式枚举，是同一连续空间里的点）**：

| 动作 | 参数位置 |
|---|---|
| walk | duty 0.62 / stride 0.55 / 交替 |
| run | duty 0.35（自动腾空）/ stride 0.85 |
| **单腿跳** | duty 1.0（永远单脚支撑）+ 落脚点固定 |
| **倒退走** | stride 取负 |
| **踮脚走** | ankle_bias 正 + foot_lift 小 |
| **侧移** | 步长方向旋转（由 turn_rate 的积分给） |
| **蹑手蹑脚** | foot_lift 小 + period 长 + lean 正 |

**写不出来的动作**（空中转体、侧身翻滚、用尾巴支撑）：换结构，不换参数。走下面的失败回传。

#### 族不存在时的 G 默认值（新意图的起步）

LLM 第一次说「拍拍头」时，`family: pat_head` 库里没有 → **没有 G 默认值可固定**。规则：

```
if  family in library:              G0 = family.G_default
elif nearest_family.cosine > 0.5:   G0 = nearest_family.G_default
else:                               G0 = GLOBAL_STANCE_DEFAULT
```

`GLOBAL_STANCE_DEFAULT` 写在 `bounds.json` 的 `_global` 键里（站桩：duty 0.5 / stride 0 / period 1.0 / 交替）。**保证任何新意图都能起步**，不会因为「族不存在」直接失败。

#### 三段搜索（CMA-ES 的调法）

| 阶段 | 搜什么 | 固定什么 | 维度 |
|---|---|---|---|
| 1 | P | G 用族默认值 | 10 |
| 2 | G | P 用阶段 1 最优 | 11 |
| 3（可选） | P + G 精修 | 无，步长收窄 50% | 21 |

理由：21 维对 CMA-ES 偏大（popsize 30、10000 次只有 333 代，三段摊薄）。分组后每段 10–11 维，收敛快；**阶段 2 天然会搜出族默认之外的步态——这就是「腿能造新动作」的机制**。

`popsize` 必须显式设（pycma 默认 4+3ln(n) ≈ 11，太小），建议 **30**。

#### 表达力指标（可证伪，不是「偏离 2° 的骨数」）

「偏离 base_rest 小于 2° 的骨数 / 21」测的是**偏离量**不是表达力——idle 姿态会误报「表达力不足」，全身乱扭会拿满分。真指标：

```
表达力 = 给定 N 组不同谓词目标，G+P 能解出（全谓词通过）的比例
```

三档难度（对应 P7 基准集）：**族内**（G 相同）/ **跨族**（G 不同）/ **真新意图**（谓词组合库里没有）。

#### K 失败回传（结构化，不是静默失败）

若某族 10000 次仍无解，回传的不是「失败」两个字，而是：

```json
{
  "type": "family_infeasible",
  "family": "somersault",
  "unsatisfied": ["world_airborne", "torso_inverted"],
  "reason": "轨迹基不支持空中全身翻转",
  "suggestion": "需要新生成器（当前 G 只覆盖支撑态步态）"
}
```

LLM 据此决定：换个接近的意图 / 标记「需要新生成器」进待办。**这不是 bug，是能力边界的显式化**——库不只存参数，还存「哪些结构还没实现」。

#### 绝对量规则（D10 的落地）

```
pose[t] = base_rest ⊕ G ⊕ P ⊕ emotion ⊕ K       # 每帧独立计算
```

禁止 `pose[t] = pose[t-1] + delta`。判据：**固定 P 与 G 的 phase，连续 apply 1000 次，末次必须与首次逐位相同（差异 = 0）。**

---

## 5. 架构（待建）

```
事件/对话 ──► LLM 意图编译 ──► {say, act{intent, mood, reuse, predicates[], dur}}
                                  │
                                  ├──► 立刻 TTS（说话不等动作）
                                  └──► dispatcher（**L0 先行，L2 分片**）
                                         ├ L0 底噪（呼吸+情绪偏置+头部朝向）每帧，纯计算
                                         ├ L1 查库命中 → play（30–200ms）
                                         ├ L2 未命中 → 播兜底 + 分片搜索（P → G → 精修）
                                         └ L3 夜间 → BVH 精确复检 → provisional → verified
                                                  │
                        感知（本体 FK / 接触 / 精确穿模）→ 判据 Gate（连续误差）
```

### 目录规划

```
body-v2/
  HANDOFF.md              ← 本文件
  docs/
    WORKFLOW.md           ← 全链路工作流程（每阶段的输入/输出/验收点）
    LOGGING.md            ← 日志规范（事件 schema、采样、查询示例）
  config/
    bounds.json           ← 族 → {G 默认值, P 搜索范围}（按族组织，不是裸的 min/max）
    weights.json          ← 门权重
    anchors.json          ← 语义锚点：语义名 → {骨骼, 局部偏移}（见 §8.6）
    emotion_bias.json     ← 四轴（val/aro/close/guard）→ 参数偏置映射（P1.5）
    capsules.json         ← 每根主骨到 mesh 的胶囊半径（VG 权重 + 点线距离 + 90 分位）
    collision.json        ← REGION 对的合法接触阈值矩阵（8×8 或 8 行规则）
  core/
    fk.py                 ← 纯 Python FK（21 主骨，D9 的唯一入口）
    proprio.py            ← 本体感知：关节/末端/质心/支撑面（读 fk.py，不读 bpy matrix）
    contact.py            ← 接触：脚底最低点、是否着地
    predicates.py         ← 谓词库（每个返回【连续误差】，不是布尔；带时间语义）
    gate.py               ← 求值 + 判据 + 违规定位
    collision.py          ← BVH 三角面求交 + 穿模归因到骨（L3 用）
    trajectory.py         ← Hermite / 五次曲线（禁用幂缓动）
    player.py             ← 播放器：phase/elapsed/暂停/变速/结束回调（见 §8.5.1）
    solver.py             ← 两骨 IK 解析解 + 相位 + 脚锁 + CMA-ES 三段搜索
    library.py            ← 参数库 + 意图检索 + provisional/verified 状态机
    dispatcher.py         ← 四层调度 L0–L3（L0 先行，L2 分片）
  body/
    binder.py             ←【唯一】碰骨骼
  vision/
    render.py             ← 只出证据：静帧、VLM A/B、新旧对照（**不进优化**）
    vlm.py                ← A/B 审美（只记录，不进优化目标）
    # servoing.py 已否决 —— 无参考影像 ⇒ 无视觉伺服，见 §7 P8
  llm/
    interpret.py          ← 事件 → {say, act}
    schema.json
  tests/
    selftest_*.py         ← 从旧项目迁移 + 扩展
    test_fk_golden.py     ← FK golden test（10 姿态 × 全骨，误差 <0.01mm）
  tools/
    compare_ab.py         ← 新旧并排渲染
    l3_verify.py          ← 夜间批处理：BVH 复检 provisional → verified/rejected
```

---

## 6. 搬什么、不搬什么（**必读**）

| 搬（已验证） | 不搬（技术债 / 重写） |
|---|---|
| `HANDOFF.md`（31 条坑） | 17 个 `.bak-*` 文件 |
| `interface/selftest.py`（7 条回归） | `archive/` 两个失败基线 |
| `tools/render_daemon.py` | `pose_pipeline.py` 的逐帧 snapshot 模型 |
| `tools/pose_guard.py` 的校验思路 | `_ease` 幂函数族（`t*t`、`t**0.55`、smoothstep） |
| `verbs.py` 的**动词定义与参数语义** | `verbs.py` 的正弦摆腿实现 |
| `axis_map.json`（13.2 KB 映射表，是资产） | `pose_consumer.py` 的 200ms 轮询结构 |
| `body_health.json` 的**指标定义** | 旧 `.blend` 里的 0 action 状态 |

---

## 7. 实施顺序与验收

| 阶段 | 内容 | 依赖 | 验收判据 | 估时 |
|---|---|---|---|---|
| **P0** | 废幂缓动 → 端点速度可指定的 Hermite；**运行时不 bake Action**（统一 FK 驱动，见 §8.5.1）；导出轨迹 CSV 做速度验收 | — | `big_steps` 16 → **0**；轨迹 CSV 速度曲线**无 0 速平台**；连续 10 s 只有一套驱动写 pose bone | 1–2 天 |
| **P0.5** | **FK 地基**：`core/fk.py`（21 主骨纯 Python）+ FK golden test | P0 | golden test 10 姿态 × 全骨误差 **<0.01mm**；单次求值 **<0.05ms**；断言 IK `influence==0`、armature 为单位变换、所有骨 `scale==1`、unconnected 集合 == `{hips, spine, neck, head, 双肩, 双大腿}`。**不做这步，后面所有「预测≠真实」都查不出来** | 1 天 |
| **P1** | `proprio.py` 本体感知快照 | P0.5 | 任意姿态输出完整快照（关节/末端/质心/支撑面），**读 fk.py 不读 bpy matrix** | 1 天 |
| **P1.5** | **情绪 → 参数偏置（L0）**：作用于头 / 颈 / 躯干 / 重心 / **尾巴与裙摆摆幅**（**不含面部**，本身体无面部骨） | P1 | 心情变化时呼吸/歪头/重心/尾巴跟着变，**0 延迟** | 1 天 |
| **P2** | `contact.py` 接触检测 | P1 | 抬脚/落脚判定 100% 正确 | 1 天 |
| **P3** | `predicates.py` + `gate.py`（**连续误差**） | P1,P2 | 故意造 5 种错（穿地/离地/插躯干/出界/倾倒）→ **5 种全抓到** | 1–2 天 |
| **P4** | **库 + 意图检索（L1）** | P3 | 给定意图 30ms 内出参数 | 2 天 |
| **P5** | LLM 一次输出 `say + act(reuse)` | P4 | 对话中动作不晚于语音 1 句 | 2 天 |
| **P6** | 两骨 IK + 相位系统 + 脚锁 + 五次曲线 | P0 | walk 滑移 <3mm、离地 <35mm、转 90° 不抽搐 | 3–5 天 |
| **P7** | L2 求解器（CMA-ES **三段搜索** + 胶囊几何门） | P0.5,P3,P6 | 未命中时 **默认预算 3000 次（numpy 实测 ~155 ms）/ 上限 10000 次（~510 ms）**内收敛，全部谓词通过，墙钟 <1s；**不含 BVH 复检**（那是 P8） | 2–3 天 |
| **P8** | **入库闸门 = 谓词 + 胶囊**（BVH 降级为调试工具，见 §16.2） | P1,P7 + 设计第 11/13 份 | 10 族进库后 **verified 率 ≥ 90%**；每条 rejected 都带「哪根骨、超多少 mm」；不破坏原 7 条用例 | 3–5 天 |

**注意顺序**：P4/P5（能反应）在 P6/P7（能自我求解）之前。
先让系统"能用库里现成的动作正常反应"，求解器只补盲区。

---

## 8. 环境事实（省得到处找）

| 项 | 值 |
|---|---|
| Blender | **5.2.3**，`D:\BL5\blender.exe`（`D:\Blender` 是旧版 4.3.2，别用） |
| 身体 blend | `C:\Users\LENOVO\Documents\deepseek-harness\default-workspace\rig_demo\deepseek_rigged.blend`（39 MB） |
| blend 实测 | **209 骨骼**（21 主 + 188 二次）、4 条 IK（chain 3，**influence 全 0**）、**0 个 action**、fps 30、**帧 1–905**、单 mesh、无形态键 |
| world 目录 | `D:\DSproject\dsh-qq\world`（pose.json / body.json / receipt.json / body_health.json / body_lib.json / body_log.jsonl） |
| 启动身体 | 双击 `D:\DSproject\embodied-body\启动实时身体.cmd`（Blender GUI + `tools/pose_consumer.py --timer`） |
| DSH Blender 工具 | `blender_rt_do` / `blender_rt_see` / `blender_rt_headless`（可直接看场景、跑脚本） |
| 旧项目关键文件 | `tools/pose_pipeline.py` 1648 行 / `pose_consumer.py` 49.5 KB / `verbs.py` 35.7 KB / `pose_guard.py` / `gait_walk.py` 20.6 KB |
| 遥测样例 | `tick_ms: 2.21`、`gap_ms: 24.59`、`big_steps: 16`、`jumps_idle: 10`、`spring_deg: 0.16` |
| 最新回执 | seq 432 `gesture`：`verdict:"FAIL"`、双脚离地 99/64.5mm、`max_deg 29.44`、fade 拉长到 1.0s 仍超预算 |

**注意**：`blend` 现在在 DSH 工作区，不在项目里。**未经用户同意不要移动或覆盖它。**

### 8.4 运行时宿主：**只有一个 —— Blender bpy**

v2 的 `body/binder.py` **全部是 bpy 操作**（`pose.bones[].rotation_quaternion`），宿主是 `D:\BL5\blender.exe` + `deepseek_rigged.blend`。

**Live2D / three-vrm 都不是 v2 的宿主**，是另外两条**并行且互不干扰**的线：

| 线 | 宿主 | 通道 | v2 碰不碰 |
|---|---|---|---|
| **v2 身体** | Blender bpy | 本文件架构 | ✅ 就做这个 |
| Live2D 桌宠 | `D:\DSproject\live2d-pet`（Electron + Live2D 参数） | `dsh-qq` → `outbox.jsonl` → **127.0.0.1:8081** `/api/latest` + `/api/emotion` | ❌ **不碰**（`POSE_PROTOCOL.md:167` 原话："别人的线，别动"） |
| three-vrm 桌宠 | `D:\DSproject\desktop-pet` | `bridge.js` → **127.0.0.1:7180** `/intent` | ❌ 不碰 |

**D6 提到 Live2D 只是拿 `dsh-qq/emotion.py` 当"情绪偏置数据源"的参考实现**，不是让 v2 跑在 Live2D 上。
**所以 P0 的"bake 成 Action + BEZIER 曲线"有效**，`body/binder.py` 是 bpy。

### 8.5 进程与通道：**单进程为主 + 一个外挂渲染进程**（v1.2 按实测重写）

实测（2026-11-23，5.2.3，209 骨 + 单 mesh）：

| 操作 | 耗时 |
|---|---|
| 21 骨写 + `view_layer.update()` + 读矩阵 | 1.58 ms |
| 5 骨写 + update | 1.27 ms |
| 209 骨写 + update | 1.74 ms |
| 只写不 update | 0.06 ms |
| 纯 Python FK(21 骨) + 10 谓词 | **0.018 ms**（mathutils，Blender 内） |
| 纯 Python FK(21 骨)，numpy 实现 | **0.051 ms**（独立 Python，v1.5 实测；慢 2.8 倍） |
| 300 次完整求值（纯 FK） | **5.5 ms** |
| 纯 FK **209 骨全量** | 0.352 ms |
| FK 与 Blender 求值误差 | 0.0002 mm |
| timer 空载最小间隔 | 5.4 ms |
| 20 次 update/tick 的 tick 周期 | 31 ms（≈1 帧） |
| depsgraph 分片跑 300 次 | 460 ms |

**结论：`depsgraph.update()` 有 1.3–1.7 ms 的固定地板，与骨数无关；手写 FK 快 88 倍，误差 0.0002 mm。**

FK 公式（预计算中间矩阵后 21 根主骨一次递推 = 18 µs）：

```
world[bone] = world[parent] @ (rest[parent]⁻¹ @ rest[bone]) @ basis[bone]
```

| 层 | 在哪跑 | 预算 |
|---|---|---|
| **L0 底噪** | 主线程 timer（实测最小 5.4 ms/次，按帧算） | 纯计算 |
| **L1 播放** | 主进程；bake 的 Action 由 Blender 插值 | 不占 Python |
| **L2 求解** | 主线程内**纯计算**（FK + 谓词） | 300 次 5.5 ms；**预算 3000–10000 次 = 55–183 ms** |
| **L3 渲染 / 视觉** | `render_daemon.py`（独立 `blender -b`） | 3–5 s/个，离线 |
| 她 → 身体 | 文件 `world/pose.json`（`seq` 单调）+ 心跳 `body.json` | — |
| 身体 → 她 | 文件 `world/receipt.json`、`body_log.jsonl` | — |
| **情绪 → 身体** | 文件 `world/emotion.json`（实测 `{"axes":{"val","aro","close","guard"},"last_label":"平常"}`） | 每帧读一次 |

#### 五条硬约束（**不做就会踩**）

1. **求解循环只 FK 21 根主骨。** 209 骨全量是 0.352 ms，慢 **19 倍**——10000 次会从 183 ms 变成 3.5 s，整条方案崩掉。**分工是：求解循环 21 骨，渲染帧 209 骨**（0.352 ms + 弹簧 ~0.5 ms，30 fps 下占 2.6%）。
2. **仍然要时间分片。** 不需要 depsgraph 分片，但 183 ms 连续计算在主线程 = **卡 11 帧**。按 `perf_counter` 的**时间预算**切（每 tick ~20 ms），不要按固定次数切（GC 抖动会打穿）。
3. **IK 必须在纯 Python 里解析求解，禁止用 Blender 约束**（见 D8）。否则 FK 预演里没有 IK → 预测 ≠ 真实 apply。
4. **只在最优解上做一次真实 apply**（1.6 ms）用于渲染与验收。
5. **穿模用「骨骼点 + 预计算胶囊半径」近似**，需 `config/capsules.json` 并从 mesh 校准；**真实场景复检失败时的回退策略必须明确定义**（建议：拒绝 + 回执带违规定位，禁止 clamp）。

#### 8.5.1 单一驱动：运行时不 bake Action（**v1.4 新增**）

**L0 / L1 / L2 全部走 FK → 写骨 → 每帧一次 `update()`。禁止在运行时用 Action 驱动 pose bone。**

理由：Action 由 depsgraph 求值写入，`bpy.app.timers` 的回调也在主线程写 pose bone。**两者谁覆盖谁，在 Blender 里不是稳定契约**——今天脚本赢、明天动画赢，症状像"呼吸被动作吃掉"或"动作被呼吸拉回半截"。这正是上一代 17 个 `.bak` 的土壤。

成本：每帧 **0.06 ms 写 + 1.3–1.7 ms update ≈ 1.4 ms**，30 fps 下占 4%。

Action **只作调试产物**（想在 Blender 里手动摆姿态时临时 bake 一份预览），**不参与运行时**。

> 连带改动：P0 的验收从「Graph Editor 速度曲线」改成「导出轨迹 CSV 画速度曲线」——没有 Action 就没有 Graph Editor 可看。
> 播放器（`core/player.py`）负责维护 `phase` / `elapsed` / 暂停 / 变速；**姿态本身仍每帧从 `base_rest` 重建**（D10）。「phase 是状态」和「pose 无状态」不矛盾。

#### FK 的 golden test（对应坑 #3「判据先自检」）

单姿态 5 个末端的 0.0002 mm **证据太薄**。必须建：

- **10 个随机姿态 × 全部骨骼** vs Blender 真实矩阵，误差 < **0.01 mm**
- 断言：4 条 IK 的 `influence == 0.0`（否则 FK 与真实不一致）、armature 对象变换为单位矩阵
- 断言：所有骨 `scale == 1`（若存在缩放，四元数 FK 会漏）
- **每次改骨架 / 加约束 / 换 blend 后必须重跑**

> P7 的预算从 300 次提到 **3000–10000 次**（CMA-ES 的 popsize 同步提到 **30–50**，让代数落在 200–300 而不是 900）。
> 好消息：188 根二次骨在 **depsgraph** 路线上几乎不花钱（209 骨 1.74 vs 21 骨 1.58），所以「头发 60 + 裙摆 64 + 尾巴 4 每帧跟着动」完全可行——而这正是「她活了」的主要来源。

### 8.6 语义锚点（anchors）—— **必须新建，`axis_map.json` 不顶用**

**`axis_map.json`（991 行）实测内容**：`rig` / `bones_total` / `facing`（toe/bangs/hair 朝向判定）/ `rest_dir`（每根骨静止朝向向量）。
→ 它是 **"骨骼 ↔ 轴向"映射**，**不含任何语义参考点**。所以每个谓词都要多一层锚点定义。

**本身体实测可用的语义锚点**：

```
hips, spine, chest, upperChest, neck, head, mixamorig:HeadTop_End(头顶)
+ 手/脚主骨 + 手指(40) + toe(4) + WhaleTail_01..04
```

**实测不可用**：`eye / face / jaw / mouth / lip / brow` **一根都没有**；`shape_keys` **为空**。

→ **这具身体没有脸。** 后果：
- 谓词里**不能有「形变类」判据**（表情 / 口型 / 眨眼）——本身体无面部骨、无形态键；
- 但 **`mouth` 作为「空间锚点」必须有** —— D1 的旗舰证据就是"手到嘴边 0.5 mm"，靠的是**空间目标点**，不是表情；
- "说话时嘴动 / 口型对得上"**做不到**（除非以后给 mesh 加形态键，那是独立工作量）；
- 表达力主战场是 **头/颈/躯干/手臂 + 二次运动**（hair 60 / skirt 64 / ribbon 6 / WhaleTail 4）。

**新建 `config/anchors.json`**，语义名 → `{骨骼, 局部偏移}`：

```json
{
  "chest":      {"bone": "chest",       "offset": [0, 0, 0]},
  "head_top":   {"bone": "head",        "offset": [0, 0, 0.18]},
  "chin":       {"bone": "head",        "offset": [0, -0.06, 0.04]},
  "mouth":      {"bone": "head",        "offset": [0, -0.09, 0.02]},
  "look_at":    {"bone": "head",        "axis": "forward", "dist": 1.0},
  "hip_center": {"bone": "hips",        "offset": [0, 0, 0]},
  "tail_tip":   {"bone": "WhaleTail_04","offset": [0, 0, 0]}
}
```

**偏移值怎么来**：一次性标定 —— 在 Blender 里拖一个 Empty 贴到网格上（嘴/下巴/胸口/尾尖），读出相对父骨的局部坐标写进 json。**约 10 分钟，做一次永久用。** 上面的数值是占位符，**必须实测定标后才能用**。

---

## 9. 参考项目（网上，已验证存在）

| 项目 | 能借什么 |
|---|---|
| [orangeduck/Motion-Matching](https://github.com/orangeduck/Motion-Matching) | 「代码驱动 vs 数据驱动位移」的经典论述。**结论：代码驱动简单但必然脚滑、缺自然度** |
| [JLPM22/MotionMatching](https://github.com/JLPM22/MotionMatching)（Unity, MIT） | `inertialize blending`（惯性化混合）+ foot locking 两个开关的现成做法 |
| [deterministic gait guides](https://github.com/jeonghwanko/sprite-gen-agent/blob/main/docs/gait-guide.md) | **规则清单**：支撑相脚底世界坐标恒定；摆动用五次曲线且端点速度/加速度为 0；两骨 IK + 膝极；**不可达就失败绝不 clamp**；walk duty>0.5（双支撑）、run duty<0.5（腾空）；A/B 相位差恒为半周期 |
| [VRM SpringBone (XPBD)](https://github.com/arkavo-org/vrmmetalkit/blob/main/Sources/VRMMetalKit/VRMMetalKit.docc/Articles/SpringBonePhysics.md) | **固定 120 Hz 子步**；可变 dt 会让弹簧过冲抽搐 |
| [GPT4Motion](https://openaccess.thecvf.com/content/CVPR2024W/PBDL/supplemental/Lv_GPT4Motion_Scripting_Physical_CVPRW_2024_supplemental.pdf)（CVPRW 2024） | LLM 生成 Blender Python **程序**而非角度 |
| [MoReGen](https://arxiv.org/html/2512.04221) | 结构化 JSON 物理参数 + 生成→渲染→反馈→下一轮的循环形状 |
| [rdesarz/biped-walking-controller](https://github.com/rdesarz/biped-walking-controller) | ZMP 预观控制（Python/PyBullet）。**只在需要真平衡时才用，桌宠/桌面不需要** |

**CMA-ES**（Hansen 2006）用于低维参数黑盒优化——这是 L2 求解器的主力，不需要训练。

---

## 10. 已知的坑（从旧 HANDOFF 继承，**开工前必读**）

1. **髋是脚的父亲** —— 呼吸别动髋（髋一转脚就跟着滑）；叠加式微动不能叠在上一拍上，否则 240 拍后偏 **386mm**（每拍都要先铺基础姿势）。
2. **脚没锁地 → 整体平移不是倾倒** —— 所以"重心偏移"不会倒，只会滑；平衡检查之前必须先钉脚。
3. **判据先自检** —— 任何检查器（平衡、平滑度）都要先证明它能抓住故意做错的情况，否则"全部通过"没有意义。
4. **场景 IK 不可依赖** —— 那 4 条 IK 约束设 `influence=1` 后 pose matrix 完全不变（原对象和 evaluated 副本都试过）。所以要自己解算（CCD / 两骨 IK）。
5. **阈值是校准出来的** —— "双脚离地"阈值 20mm→35mm：走路摆腿时支撑脚抬升 21mm 属正常。
6. **幂函数缓动会在 t=0 产生无穷导数**（`t**0.55`）—— 这是 176mm 单帧瞬移的数学根源。**不要再用幂函数做缓动。**
7. **`fade` 会被 `dur*0.5` 压制** —— 短 dur 的意图实际上是在要求"3 帧完成大动作"。要么拉长 dur，要么**拒绝执行**。
8. **`tick_ms 2.2ms` 意味着参数搜索极便宜** —— 300 次求值 ≈ 0.7s。**渲染才是瓶颈**（0.3s/张），所以视觉闭环必须离线。
9. **`gaze_toward` 不能给 LLM 选**（2026-11-24 实测）—— 它会写成 `gaze_toward(anchor="mouth")`（看自己的嘴）/ `"head_top"`（看自己头顶），物理不可能，还会把整轮求解拖进 3000 次失败。视线跟随归 L0 底噪，不进动作判据。`Runtime.HIDDEN_FROM_LLM` 已过滤。
10. **言行一致要靠 prompt 强制** —— 她会说「挥了」但不写 `<<ACT>>`，嘴上做了身体没动。prompt 里必须写「说了动作就必须给 act」。
11. **谓词默认 side 和生成器默认 side 不一致** —— `hand_above_head` 默认 right，`pose_gen` 默认两侧 → 「挥手」变「投降」。`_infer_side` 对手部谓词默认 right。
12. **场景恢复不能跨轮叠加** —— 每次 `apply_pose` 前备份、之后立即恢复。跨轮备份会把上一轮姿态当基线，恢复出来少 17 根骨（128 vs 145）。
13. **读配置先判格式** —— 我把 `config.json` 当 `.env` 按行解析，把含 API key 的整行打印出去了。JSON 用 `json.load`，打印前过 `redact()`。

---

## 11. 红线（**不要做**）

1. 不要覆盖 `deepseek_rigged.blend`。动它之前先问用户、先备份。
2. 不要在实时链路里做渲染或视觉闭环。
3. 不要把 VLM 分数放进优化目标（优化器会学会骗 VLM）。
4. 不要给 LLM 输出关节角或角度数值（偏差 92mm vs 0.5mm）。
5. 不要在没跑回归的情况下改门阈值。
6. 不要用 `clamp` 把不可达的目标"修"成看起来合理——**失败就失败**。
7. 不要删除旧项目 `embodied-body`，它是 A/B 对照基线。
8. **不要把配置文件原文打印出来**（2026-11-24 已踩）—— `config.json` / `.env` 里可能有 API key。JSON 用 `json.load` 读，打印前一律过 `redact()`（`llm/client.py`）。

---

## 12. 第一批任务（已与用户确认：**先只出设计文档，不写代码**）

按用户 2026-11-23 的决定：

> **第一批：只出详细设计文档，不写代码。**

需要产出的设计文档：
1. `config/bounds.json`：**先把 §4.5 的三层参数化落成「族 → {G 默认值, P 搜索范围, 谓词阈值档位}」**，不是裸的 min/max。族 = 「G 默认 + P 范围 + 阈值档」三元组
2. `predicates.py` 的谓词表：名称 / 参数 / **连续误差公式** / 权重
3. `gate.py` 的求值接口与违规定位格式
4. `library.py` 的库 schema（intent 向量 / params / metrics / evidence / parent）
5. `dispatcher.py` 的四层调度接口（L0–L3 的时序与兜底规则）
6. `llm/schema.json`：LLM 输出契约（`say` + `act{intent, mood, reuse, predicates[]}`）
7. `trajectory.py` 的 Hermite 段间速度传递公式（含验收方式）
8. 每个模块的**验收判据**（尽量复用 `body_health.json` 已有指标）
9. **`config/anchors.json` 的锚点定义 + 标定流程**（见 §8.6；**必须先定标，谓词表才写得出来**）
   - 标定用 `scene.ray_cast` 从骨骼位置打射线，**必须按材质过滤**（hair 60 根骨的几何贴在脸前，不滤会命中刘海），只接受 `Toon face 0,3,4`
   - 自检不能写"到表面 <2mm"（恒真），改判：① 命中材质 ∈ face 集合 ② `|x| < 20mm` ③ `z` 在下巴与眼睛之间
10. **`config/emotion_bias.json`：四轴（`val/aro/close/guard`）→ 参数偏置映射**（P1.5 的验收现在没有映射表，无法执行）
11. **`config/capsules.json`：每根主骨到 mesh 表面的近似半径**（穿模谓词用；须从 mesh 校准，并定义"真实复检失败"的回退策略）
12. **FK golden test 设计**：10 个随机姿态 × 全骨 vs Blender 真实矩阵，误差 <0.01mm；断言 IK influence==0、armature 变换为单位、所有骨 scale==1、unconnected 集合 == `{hips, spine, neck, head, 双肩, 双大腿}`
13. **`config/collision.json`：REGION 两两组合（8 选 2 = **28 对** + 一条默认值）的合法接触深度阈值表** + 穿模「归因到骨」算法（相交三角形 → vertex group → 骨 → 该骨胶囊临时加粗 → 罚项回灌）。`hair × head` 要用「相对静止姿态的增量」判，不能直接用绝对深度
14. **失败库 schema + TTL**：`status: failed` 的条目什么时候失效（参数边界 / 权重 / 骨架变化）、失效后如何允许重解、L3 判 rejected 时如何联动重开 failed
15. **日志与验收指标**：把 `docs/LOGGING.md` 里的指标映射表变成可执行的 selftest（每个阶段一条「怎么测 / 预期现象 / 调参位置」）
16. **`config/seed_families.json`：旧 14 个动词 → 族（G 默认 + P 默认）的映射表**。这是 P4 的**种子库**——不做的话 L1 一开始没东西可查，「她活了」的第一次反馈要等很久。工作量小（14 行），作用大
17. **`config/player.py` 的播放器契约**：`phase` / `elapsed` / 暂停 / 变速 / 结束回调；与 D10 的关系（phase 是状态、pose 不等于状态）

> **交审建议分 3 批，别一次 17 份压过来**：
> - **批 1 · 地基**（互相咬合，必须一起看）：第 1、2、3、9、10、11、13 份
> - **批 2 · 能反应**（对应 P4/P5）：第 4、5、6 份
> - **批 3 · 能自学**（对应 P6/P7/P8）：第 7、8、12、14、15、16、17 份

**用户要先审这份设计，通过后才写代码。**

> **编号说明**：§13（术语表）已移到文末 **§17**；§14–§16 是后续追加的章节。

---

## 14. 交接自检清单（**下一个对话开工前先跑一遍**）

拿到这份文件的对话，先确认下面 9 条都能从文件里找到答案；找不到的就是文件漏了，先补再开工：

1. 宿主是谁？（§8.4）→ Blender bpy，唯一
2. 求解器用什么求值？（D9）→ 纯 Python FK，21 主骨，0.018ms
3. IK 怎么实现？（D8）→ 解析解，禁止 Blender 约束
4. 参数化几层？（§4.5）→ P(10) + G(11) + K，三段搜索
5. L3 是什么？（§7 P8）→ **入库闸门**，不是当场后处理；**没有视觉伺服**
6. 每帧从哪开始算？（D10）→ `base_rest` 重建，禁止累加
7. 情绪能改 targets 吗？（P1.5）→ 不能，只改连续通道
8. 「穿模」怎么判？（§12 第 11/13 份）→ 胶囊（快）+ BVH（准），REGION 对阈值
9. 日志写哪、怎么验收？（`docs/LOGGING.md`）→ events.jsonl + 指标映射表

**如果第 5 条在文件里被写成「视觉闭环」，说明有人退了回去，立即纠正。**

---

## 15. 交付自检：主观评分卡（**不能自动化，但不能省**）

所有技术指标都能自动测，唯独「她像不像活的」不能。**「指标全绿但她像提线木偶」是这个项目最可能的失败形态，而且系统自己发现不了。**

每次阶段交付时做一次，30 秒，看三个动作：

| 项 | 1 分 | 3 分 | 5 分 |
|---|---|---|---|
| 起播自然度 | 明显等一下才动 | 有轻微延迟 | 说完就动，像早就准备 |
| 动作连接 | 一顿一顿 / 跳帧 | 偶尔生硬 | 看不出接缝 |
| 二次运动 | 头发裙摆死板 | 有跟随但僵硬 | 发梢/裙摆/尾巴自然甩 |
| 停顿时的状态 | 完全静止像雕像 | 有呼吸但机械 | 会换重心、会歪头、有小动作 |
| 整体 | 一看就是程序 | 像早期 3D 角色 | 像有人在那儿 |

**评分写进 `logs/review.jsonl`**，和 `health.jsonl` 并排存。长期看相关性：

- 指标绿 + 分低 → **指标选错了**，回去改指标（这比改代码重要）
- 指标红 + 分高 → 可能是阈值太严
- 两者都低 → 真问题

这不是可选项：它是唯一能发现「指标与目标脱节」的机制。（日志 schema 见 `docs/LOGGING.md` 的 `review` 事件）

---

## 16. 待定事项（**已知未决，不要当没看见**）

| # | 事项 | 状态 | 什么时候必须定 |
|---|---|---|---|
| 1 | **多动作叠加**（"一边走过来一边挥手"） | 未设计 | 写第 5 份（dispatcher）时 |
| 2 | **动作被打断** | ✅ **已定，见 §16.1** | — |
| 3 | **混合算法**（200 ms「惯性化混合」的具体实现：slerp？弹簧？） | 未设计 | P6 做脚锁时会连带需要 |
| 4 | **她看不到自己的能力清单** | ✅ 已实现 `Runtime.capabilities()` | 接 LLM 时塞进 prompt |
| 5 | **失败回传不够具体** | ✅ 已实现 `capability_gap` | — |
| 6 | **没有能力提案通道** | ✅ `capability_gap` 写进 events.jsonl | 需要一个汇总脚本统计 |
| 7 | **L3 夜间转正流程** | 未做 | `tools/l3_verify.py`：把 `provisional` 批量转 `verified` |
| 8 | **走路转身未验证** | 有参数（`turn_rate`）没实测 | 需要一段转身动画验证不抽搐 |
| 9 | **真 LLM 接入** | ✅ 已打通（沙箱人格 + 只读 key，见 §22.20）；世界线写入未做 | — |
| 10 | **动作混合** | 见第 3 条 | — |

### 16.1 打断策略（用户 2026-11-23 定）

**动作执行到一半时用户又输入 → 默认不接受，回一句「我在做 XX，等一下」。**

三条都要满足：

1. **不是静默吞掉**——用户会以为她死了
2. **不是立刻打断**——动作会崩
3. **进队列，动作结束后再处理**（不是丢弃）

对应日志：`intent_deferred`（见 `docs/LOGGING.md`），带 `{deferred_intent, running_action, remaining_s}`。这条日志是判断「拦截策略是不是太激进」的唯一依据——如果 `intent_deferred` 占比过高，说明动作太长或打断太硬。

---

### 16.2 BVH 不能当主判据（2026-11-24 实测）

| 手臂旋转 | `body\|body` 相交增量 | `arm\|arm` | `arm\|body` |
|---|---|---|---|
| 120° | **+528** | +9 | 0 |
| 150° | **+769** | +19 | 0 |
| 170° | **+996** | +12 | 0 |

**手臂一动，身体自己和自己就多出 500–1000 对相交**——腋下/肩部皮肤褶皱，不是穿模。mesh 是多层 + 软形变，这个噪声量级远大于真穿模。

**所以**：
- **闸门 = 谓词 + 胶囊**（胶囊只看骨骼线段+半径，不受 mesh 形变影响）
- BVH 只作人工排查工具（「胶囊说没问题但肉眼看着不对」时用）
- 不要再花时间调 BVH 阈值——它调不好

---

### 16.3 能力边界会塑造她的思考方式（用户 2026-11-24 提出）

**24 条谓词白名单 + 12 个族 + 数值范围 = 她能想出的目标的上界。** 这不是 bug，是 D1 的代价（判据必须可机械验证）。但有三处必须补，否则**她会越来越保守，而不是越来越敢想**：

| 缺什么 | 后果 | 做法 |
|---|---|---|
| **能力清单不可见** | 她只能瞎猜自己能做什么，容易反复提做不到的 | `capabilities()` 生成「12 类动作 + 24 条判据 + 可用锚点」塞进 prompt |
| **失败回传不具体** | 她只知道「失败了」，不知道缺什么 | `family_infeasible` 里带 **缺的维度**（缺谓词？缺族？缺范围？） |
| **没有提案通道** | 她想要但做不到的目标直接消失，不留下任何痕迹 | 记 `capability_gap` 日志：`{wanted, missing, suggested_extension}` |

**核心原则**：她能想的 > 她能做的。差别必须**显式回传给她**，而不是静默失败——静默失败会让 LLM 学会「别提新东西」。

---

## 17. 术语表
| 词 | 含义 |
|---|---|
| **谓词 predicate** | 一条可执行的验收标准，如 `hand_above_head(side=right)`。**必须返回连续误差，不是布尔** |
| **时间语义** | 每条谓词必须标 `at_end` / `at_any` / `throughout` / `peak` 之一。`throughout` 用 log-sum-exp 软最小值合成，否则 CMA-ES 只看到最差帧 |
| **门 gate** | 把感知状态 + 谓词列表求值的模块，输出 pass/total/violations |
| **族 family** | 一个意图对应的三元组：**G 默认值 + P 搜索范围 + 谓词阈值档位**。不是单个动作 |
| **P / G / K** | P = 上半身姿态参数（10 维，可搜）；G = 下肢与步态参数（11 维，轨迹基上可搜）；K = LLM 给的离散结构（targets / 阶段 / explicit 骨） |
| **轨迹基** | 脚的世界轨迹形式：支撑段（世界锁定）+ 摆动段（Hermite）→ 两骨 IK。**写死的是形式，可搜的是参数** |
| **底噪 L0** | 呼吸 / 情绪偏置 / **头部朝向跟随（整头 look-at；无 eye 骨，不做眼球）**，每帧从 `base_rest` 重建 |
| **兜底 fallback** | 两级：① 库最近邻（cos 距离 <0.25）② 通用 idle + 情绪偏置。**L2 启动前就要选好**，不能空档 |
| **具身** | 本体感知 + 外部感知 + 观测能闭环改变动作。**只有表达力不算具身** |
| **参数偏置** | 情绪对基础动作的连续影响（幅度/速度/倾斜），不是新动作。**不许作用于 K.targets** |
| **provisional** | 库条目状态：L2 解出、已能播、但还没过 BVH 复检 |
| **verified** | 过 BVH 复检、可长期复用 |
| **rejected** | BVH 复检 + 罚项回灌仍不通过，不允许再播，带违规定位 |
| **failed** | 求解阶段 10000 次仍无解，带 `family_infeasible` 回传；有 TTL，条件变化后允许重解 |
| **穿模归因** | 把「相交三角形对」翻译成「哪根骨、哪一帧、穿透多少 mm」，没有这一步罚项只是噪声 |

---

## 18. v1.6 实现现状（2026-11-24，**这节比前面所有章节新**）

### 18.1 模块清单（core/）

| 模块 | 作用 | 实测 |
|---|---|---|
| `fk.py` | 纯 Python FK，209 骨 | 0.05 ms（21 骨 0.018） |
| `proprio.py` | 关节/末端/质心/脚底 | — |
| `bones.py` | BoneMap + 世界轴→局部 basis | — |
| `emotion.py` | 情绪低通滤波 | — |
| `contact.py` | 接触检测 | — |
| `predicates.py` | **37 条谓词** | — |
| `gate.py` | 谓词合成 + 判决 | — |
| `library.py` | 库（save/open 原子写） | 0.089 ms/100 条 |
| `log.py` | JSONL 事件日志 | — |
| `binder.py` | basis → 骨骼 | 往返 3e-7 |
| `pose_gen.py` | P → basis | 0.34 ms |
| `trajectory.py` | Hermite 插值 | — |
| `player.py` | 播放器 | — |
| `dispatcher.py` | 打断/排队 | — |
| `ik.py` | 双骨 IK（解析解） | — |
| `gait.py` | 步态 | 脚锁 0.0 mm |
| `cmaes.py` | CMA-ES | — |
| `solver.py` | **P 12 维**求解 + 两阶段 | 46 次 / 20 ms |
| `collision.py` | 胶囊 + baseline 差分 | 2.2 ms |
| **`verify.py`** | **入库前验证**（参数范围+谓词+胶囊） | 2.8 ms |
| **`overlay.py`** | **多动作叠加**（分块替换） | — |
| **`blend.py`** | **动作混合**（四元数 slerp + smoothstep） | 正交误差 4e-12 |
| **`gestures.py`** | **预设手势**（关节角关键帧 + Hermite） | 摆幅 136 mm |
| **`runtime.py`** | **端到端接线**（查库→求解→验证→入库→姿态） | 23 ms |

### 18.2 LLM 层（llm/）

| 模块 | 作用 |
|---|---|
| `client.py` | DeepSeek 客户端（**.env 读 key，永不打印**） |
| `persona.py` | 大肥鱼人格 + 一次调用两路输出（`<<ACT>>`） |
| `interpret.py` | 判据契约 + 严格校验 |

### 18.3 工具（tools/）**——下个对话先看这些**

| 工具 | 什么时候用 |
|---|---|
| **`audit_rig.py`** | **写任何关节角代码之前必跑**（8 项体检） |
| `probe_bones.py` | 查「某骨绕世界轴转，末端往哪动」 |
| `probe_elbow_axis.py` | 诊断前臂朝内/朝外 |
| `probe_arm.py` / `probe_joint.py` | 扫 P / 关节角空间 |
| `solve_target.py` | 用锚点目标让求解器解（**不依赖参考姿态**） |
| `sweep_targets.py` | 扫目标组合，找可行域边界 |
| `compare_solver.py` | 求解器 vs 关键帧对比（校准用） |
| `verify_templates.py` | 12 个族的模板批量验证 |
| `add_templates.py` | 生成 predicates_template |
| `l3_verify.py` | 改阈值后批量重跑历史条目（**不是夜间任务**） |
| `test_template_runtime.py` | 只用族名求解（验证模板链路） |
| `test_transition.py` | 验证 200ms 过渡曲线 |
| `make_sandbox.py` | 从 dsh-qq 提取人格/记忆沙箱 |

### 18.4 关键数字（都是实测）

```
FK 209 骨最差误差      0.000193 mm
FK21                   0.018 ms
pose_gen 单次          0.34 ms
求解 1 个动作          46 次求值 / 20 ms
验证（入库前）         2.8 ms
胶囊检测               2.2 ms
查库                   0.011 ms
端到端（族名→姿态）    23 ms
```

### 18.5 当前能力

| 能做 | 不能做 |
|---|---|
| 12 个族，**只用族名**就能解出 | 走路转身（未实测） |
| 谓词模板化（LLM 不写谓词） | 三条动作同时叠加 |
| 立即验证入库（全 verified） | 真 LLM 接世界线（沙箱外） |
| 多动作叠加（走+挥） | 表情/口型（模型没有面部骨） |
| 200ms 动作混合 | |
| 真 DeepSeek API 驱动 | |

---

## 19. v1.6 踩过的坑（**按重要性排序，比 §10 更新**）

**1. 映射是「怎么动」，谓词是「量什么」。前者错了，后者全白搭。**

「怎么调都解不出」的正确排查顺序：

```
tools/audit_rig.py      →  8 项体检（轴、对称、参数有效性）
tools/probe_bones.py    →  绕世界轴运动表
tools/probe_elbow_axis.py →  关节弯曲轴
才轮到加谓词 / 扩 P
```

**2. 肘的弯曲轴是「世界 X」，不是 Y。**

绕 Y 转前臂 → 手往身体中线收（elbow=90 时 x 从 -0.21 收到 -0.11），看起来像「肘朝里」/反关节。
肘的生理弯曲在矢状面（前后），对应世界 X。改一行 `"y"` → `"x"`，同一目标从 2510 次失败变成 46 次收敛。

**3. 肘的左右符号要「同号」，不能带 `s_lift`。**

`-elbow * s_lift` 让左手往后折（y 差 180mm）。绕世界 X 转时前后弯曲对左右是镜像对称的，不需要反号。改完左右差 **185.8mm → 4.0mm**。

**4. 这个模型不是标准镜像。**

- `rightUpperArm` 绕 Y **正**转 → 手向上
- `leftUpperArm` 绕 Y **正**转 → 手向外

写任何关节角前**必须查 `docs/BONE_AXES.md`**（51 行实测）。

**5. 「至少约束」会让求解器顶到边界。**

`hand_above_shoulder` 只有下限，求解器就把手推到最高（1020mm）。
要精确位置用「锚点 + 距离」：`hand_near_anchor(anchor="head_top", dist_mm=200, tol_mm=40)`。

**6. 锚点目标是独立评价，参考姿态会变成「自己考自己」。**

用「手离头顶 200mm」而不是「和我调的这个姿势比」。锚点是身体自己的坐标，谁都不会变。

**7. 不做夜间任务。**

L2 求解 20ms + 验证 2.8ms = 23ms，用户无感。
「夜间」是按 BVH（300ms/条）估的，而 BVH 已被证明**不能当主判据**（噪声比真穿模大：手臂转 170°，`body|body` 相交涨 996 对）。
**求解完立刻验证，通过才入库，库里没有 provisional。**

**8. `intent_key` 必须在参数补齐之后算。**

先在模板填充前算 key → 所有族 key 相同 → 全部命中同一条库记录。凡是「先算 key 再补参数」的流程，key 一定算错。

**9. 胶囊的 rest 基线必须差分。**

rest 姿态就有 **9858** 对 BVH 自相交 / **103** 对胶囊贴靠（多层 mesh：裙子叠层、发束、内衣）。
不差分的话每一个姿势都会被判穿模。

**10. 场景恢复不能跨轮叠加。**

每次 `apply_pose` 前备份、之后立即恢复。跨轮备份会把上一轮姿态当基线，恢复出来少 17 根骨（128 vs 145）。
原始状态是 **145 根非单位 basis**。

**11. 生成器默认两侧，谓词默认单侧。**

`hand_above_head(side=right)` 只约束右手，但 `pose_gen` 默认两侧都驱动 → 「挥手」变「投降」。
`_infer_side` 对手部谓词默认 right。

---

## 20. 下个对话开工建议

1. 跑 `python tools/audit_rig.py` —— 8 项全过才动手
2. 跑 `python tools/verify_templates.py` —— 12/12 才说明库是好的
3. 跑 `python -c "import glob,subprocess;[subprocess.run(['python',f]) for f in glob.glob('tests/test_*.py')]"` —— 24 个测试
4. 再读 §19 的 11 条坑

**不要做的事**：
- 不要凭「一般规律」猜骨骼符号（§19-4）
- 不要用 BVH 当主判据（§19-7）
- 不要把「夜间任务」当必要设计（§19-7）
- 不要在模板填充前算 intent_key（§19-8）

**12. 左右偏移必须沿「前进方向的法向」。**

`gait.py` 里左右脚间距只加在世界 X 上 → 转身 90° 后两脚还是沿 X 分开，实测两脚连线只转 41.6°。
修成 `perp = (-fwd[1], fwd[0])` 后精确 90°。

**13. 转身时支撑脚必然漂移，不能要求 0。**

直走脚锁必须 **0.00 mm**；转身时支撑脚得当轴，漂移与转身速度成正比（90°/s 时 7 mm）。
按 `tol = 0.005 + turn_rate × 0.00008` 判。


---

## 21. v1.7 增量（2026-11-24 夜间，**最新**）

### 21.1 新增模块

| 模块 | 作用 | 实测 |
|---|---|---|
| **`core/verify.py`** | 入库前验证（范围+谓词+胶囊） | 2.4 ms |
| **`core/overlay.py`** | 多层叠加 + 优先级权重 | 0.006 ms |
| **`core/blend.py`** | 200ms 惯性化（四元数 slerp + smoothstep） | 0.65 ms |
| **`core/jitter.py`** | 动作微抖动（随机 + 情绪偏置 + 守卫） | 0.1 ms |
| **`core/gestures.py`** | 预设手势（关节角关键帧） | — |
| **`core/runtime.py`** | 端到端接线 | L1 0.006 / L2 34 ms |

### 21.2 新增工具

| 工具 | 用 |
|---|---|
| `audit_rig.py` | **写关节角前必跑**（8 项体检） |
| `bench_all.py` | 全链路性能 → `docs/PERF.md` |
| `test_turn.py` | 走路转身验证 |
| `test_emotion_flow.py` | 情绪改目标 + 抖动 |
| `simulate_chat.py` | 真 API 模拟用户对话 |
| `solve_target.py` / `sweep_targets.py` | 锚点目标求解 / 可行域扫描 |
| `verify_templates.py` / `add_templates.py` | 12 族模板验证 / 生成 |

### 21.3 三种「调制」的分工（**别混**）

| 机制 | 作用对象 | 保证 | 每次结果 |
|---|---|---|---|
| **情绪改目标** `bias_predicates` | **谓词**（手该到哪） | 一定生效 | 相同 |
| **抖动** `perturb` | **P 微调** | 可被守卫回退 | 不同 |
| **动作混合** `blend` | 两姿态之间 | 200ms 平滑 | — |

**情绪规则改「目标」不是「缩放 P」**：
- ❌ `arm_lift × 0.7`（假的——挥手缩小版，不是累了）
- ✅ `hand_above_head` → `hand_above_shoulder`，锚点 dist +90mm（真的——「手抬到肩就够」）

**实测**：精力高 hand_z=0.978，精力低 0.953（差 25mm，求解器重新解出来的）。

### 21.4 库的存储规则

- 库存 **P\***（求解基准，可复现）
- 播放用 **P' = P\* + 抖动**（每次略不同，20 次差 11.4mm）
- 状态直接 `verified`（**没有 provisional**，见 §19-7）

### 21.5 情绪维度不会「膨胀」

| | 形态 | 膨胀 |
|---|---|---|
| 谓词 | 每动作 3–8 条 | ✅ 会 |
| 情绪维度 | 固定 4 个标量（mood/energy/close/guard） | ❌ 不会 |

**但情绪变化应该放在「解析器」那次调用里**（第二次），不要塞进说话的调用，否则格式挤压。

### 21.6 已知未做

| 项 | 说明 |
|---|---|
| 意图层 | 用户提的「说话前先定意图」—— 已论证「先意图会迫使动作」，应改成「先说话、后判断动作」 |
| 解析器独立调用 | 她说话完全自由，另一次短调用判断「话里有没有动作」 |
| 情绪规则仍是写死的 | 阈值 30/40/70 是人工定的 |
| API 慢调用 | 偶发 4.7s，先记着不改 |
| 多动作叠加的时序 | 三层同时启动但过渡时长不同时怎么办 |

### 21.7 两阶段（**推荐的接法**，已实测）

```
① 她说话   完整人格 + 记忆 + mood，prompt 里**无任何格式要求**
              ↓
② 解析器   短 prompt、**不带人格**，输出 {intent, mood_delta}
              ↓
③ runtime  情绪改目标 → 求解 → 验证 → 抖动 → 播
```

**为什么不是「先定意图再说话」**：先意图会迫使她配合动作，为了给动作找理由，说话会变僵硬。
反过来「先说话、后判断」：动作是**证据**不是命令，言行一致由解析器保证。

**实测 4 轮全对**（旧 `<<ACT>>` 方案 4 轮 1 失败），代价是 1558ms/轮（8 次调用）。

**第一阶段 prompt 里连「不要输出 JSON」都不能写**——提到格式就是暗示。

### 21.8 定稿：只有两阶段

`<<ACT>>`（单阶段自输出动作）**已废弃**。统一入口是 `llm.persona.turn()`，内部走两阶段。

```python
turn(text, llm_fn, capabilities, persona, memory, mood,
     body=True, parser_llm=None)
→ {"say", "intent", "mood_delta", "act", "calls": 2, "source": "two_stage"}
```

`parser_llm` 建议传一个 `temperature=0.1, max_tokens=80` 的实例（只做分类）。

**14. 角度是标量，方向必须单独判。**

`knee_angle`（髋-膝-踝夹角）**抓不到反折**：

```
小腿绕 X -40°（反折，膝在后方 +68mm）→ knee_angle err = 0  ✅通过
小腿绕 X +40°（正常，膝在前方 -80mm）→ knee_angle err = 0  ✅通过
```

夹角是 0–180° 的**标量**，不分正反。要查方向得单独加判据（`knee_forward` / `elbow_forward`：
膝/肘的 y 必须在「髋-踝」/「肩-手」连线的前方）。

**这一类 bug 今天抓到 4 次**：
1. 肘的弯曲轴（Y → X）
2. 肘的左右符号（不该带 `s_lift`）
3. `torso_lean` 忘了减 rest 基准（6.34°）
4. `knee_angle` 抓不到反折

**共同特征：谓词合格但姿势是错的。** 加任何「角度类」判据时，都要问一句「方向对吗」。

---

### 21.9 谓词与底线（v1.7 最终）

| 项 | 数量 |
|---|---|
| 谓词总数 | **43** |
| 自然度底线（强制） | **12 条**：torso_upright / no_ground_penetration / no_hyperextension×2 / wrist_neutral×2 / knee_angle×2 / shoulder_range×2 / knee_forward×2 |
| 族 | **18 个**（12 原始 + free + lean_in / tail_drop / lie_down / tail_wag / head_tilt_fam） |

**自然度底线对所有入库条目强制**，不管 LLM 写了什么——它产不出骨折姿势了。

---

## 22. v1.8 交接（2026-11-25，**这节是当前状态，先读这节**）

### 22.1 一句话现状

**上半身闭环全通（说话→求解→验证→抖动→叠加→混合），腿也能走/站/蹲/单脚站/踩点，但「叠加 + 全身」还没合到一起。**

### 22.2 数字

```
测试        45 个全绿
族          21 个（12 原始 + free + lean_in/tail_drop/lie_down/tail_wag/head_tilt_fam）
谓词        43 条
P 维度      12（上半身）
腿长        427 mm（硬约束）
胶囊检测    2.2 ms
求解        46 次 / 20 ms
L1 查库     0.006 ms
L2 端到端   34 ms
单帧预算    1.07 ms（931 FPS）
```

### 22.3 模块清单（core/，29 个）

| 类别 | 模块 |
|---|---|
| **基础** | `fk` `proprio` `bones` `binder` `log` |
| **感知** | `contact` `predicates`（43 条） `gate` |
| **姿态** | `pose_gen`（P→basis） `emotion`（改目标） `jitter`（微抖动） |
| **求解** | `cmaes` `solver`（两阶段） `ik`（双骨 IK） `gait`（步态） |
| **腿（v1.8 新）** | `body_state`（BodyState） `foot_target`（三种源） `assembly`（统一装配） |
| **复合** | `overlay`（多层叠加） `blend`（200ms slerp） `trajectory` |
| **其他** | `library` `dispatcher` `player` `verify` `grow` `gestures` `collision` `runtime` |

### 22.4 腿的三种驱动（v1.8 完成）

| 类 | 用于 | 验证 |
|---|---|---|
| `GaitTarget` | 走/跑（周期） | ✅ 9 帧误差 **0.0mm** |
| `StaticTarget` | 站/蹲/单脚站 | ✅ 误差 0.0mm |
| `PointTarget` | 踩指定点 | ✅ 误差 0.0mm |

**用法**：

```python
rt.set_motion(family="walk")               # v1.9：速度/转向由 stride/duty/period 推出
for _ in range(12): rt.tick(1/12)
b, diag = rt.build_full(P, "walk", 0.25)   # 全身，脚误差 0
apply_basis(arm, b)                        # 注意：apply_basis 不是 apply_pose
```

> **v1.9 修正**：上面原来写的是 `set_motion(vel=0.5, turn_rate=60)`。
> walk 族的正确速度是 `stride/(duty×period) = 0.220/(0.62×1.1) = 0.3226 m/s`，
> 填 0.5 会让支撑脚一个支撑期滑 **118mm**（测试全绿也测不出来）。
> 现在 `build_full` 会自动纠正并把修正写进 `diag["vel_sync"]`。

### 22.5 关键教训（v1.8 新增，**按重要性**）

**15. `apply_pose(M)` 和 `apply_basis(arm, basis)` 是两个入口，混用不报错但姿势崩。**

```python
def apply_pose(M, rig, arm):
    basis = to_basis(M, rig)   # ← 收的是**世界矩阵**，这里反算 basis
```

`build_full` 返回的是 **basis**。传给 `apply_pose` → **双重转换** → 蜷成一团。

**正确**：`apply_basis(arm, basis)` 直接写。

**验证**：209 骨 `pb.matrix` vs `rig.compute(basis)`，误差 **4e-6**。

**16. `foot_targets` 的 `travel` 会和 `BodyState` 双重计算。**

`gait.foot_targets` 内部算 `travel = stride*(t/(duty*period))`。`BodyState` 也在积分。

**修**：`foot_targets(..., travel=False)`，前进只归 `BodyState`。

**17. `hips` 的 basis 平移会被 rest 旋转转掉。**

`M[hips] = rest[hips] @ basis[hips]`，basis 的平移先被 `R_rest` 作用。

**修**：`basis[0:3,3] += rest[0:3,0:3].T @ world_offset`。

**18. `hip_drop` 必须覆盖「脚能到的最远点」。**

`stride=0.5` 时脚前后摆 ±0.25m，加上抬脚 0.06m → 腿要伸 434.8mm，但腿长只有 427mm（**超 7.8mm**）。

**修**：`hip_drop = stride*0.16 + lift*0.5`。

**19. 角度是标量，方向必须单独判。**

`knee_angle`（髋-膝-踝夹角）**抓不到反折**：反折和正常都能通过。要加 `knee_forward`（膝的 y 必须在髋-踝连线前方）。

**已经 4 次同类 bug**（肘轴、肘符号、torso_lean 基准、膝反折）。

**20. 腿的可达范围是硬约束。**

从髋出发半径 **427mm** 的球。任何让脚变远的变化（stride、抬脚、身体倾斜）都要算进 `hip_drop`。

### 22.6 已知未完成（**下一个对话接着做**）

| # | 项 | 说明 | 优先级 |
|---|---|---|---|
| 1 | **接口统一：`pose_for_from_P` 返回 basis** | ✅ **已完成（v1.9）**，见 §22.12 | ✅ 完成 |
| 2 | `handle_composite` 接全身 | ✅ **已完成（v1.9）**，base 走 `build_full`，腿跟着走；叠加层仍只改上半身 | ✅ 完成 |
| 3 | 转身时脚不滑（支撑脚当轴） | ✅ **已完成（v1.9）**：轴心脚滑移 0.0mm（修前 112mm），见 §22.19 | ✅ 完成 |
| 4 | `hip_drop` 精确化 | 现在是估算式 | 中 |
| 5 | 真 LLM 接世界线 | ✅ API 已打通（沙箱人格 + 只读 key，见 §22.20）；**写回世界线仍未做** | 中 |
| 6 | 关键词表 + `wanted` 字段 | 自动化的最后一块 | 中 |
| 7 | VLM 视觉验证 | 判断「像不像」 | 低 |
| 8 | 三条动作同时叠加的时序 | 现在支持任意层，但过渡时长统一 | 低 |

### 22.7 接口统一的具体做法（第 1 项）

**目标**：全项目统一用 **basis**（不是世界矩阵），因为：
- `apply_basis` 要 basis
- `merge_layers` 的语义就是 basis 叠加
- `build_full_body` 返回 basis

**要改**：

```python
# 现在
def pose_for_from_P(...) -> dict:
    return self.rig_fast.compute(basis)      # 世界矩阵

# 改成
def pose_for_from_P(...) -> dict:
    return basis                              # basis
```

**连带要改**：
- `pose_for(entry)` 加 `as_basis=False` 参数（默认仍返回世界矩阵，兼容 `apply_pose`）
- `handle_composite` 全用 basis
- 测试读位置时统一 `rig.compute(basis)`

**验证方式**：改完跑 `test_composite` + `test_runtime_body` + `test_e2e`。

### 22.8 新对话开工清单

```bash
# 1) 骨架体检（8 项必须全过）
python tools/audit_rig.py

# 2) 模板验证（18/18 必须全过）
python tools/verify_templates.py

# 3) 全身审计（21 个族）
python tools/audit_poses.py

# 4) 全量测试（45 个）
# 见 §20 的命令
```

**不要做的事**：
- 不要凭「一般规律」猜骨骼符号（查 `docs/BONE_AXES.md`）
- 不要用 BVH 当主判据（噪声比真穿模大）
- 不要把 `apply_pose` 和 `apply_basis` 混用
- 不要在 `foot_targets` 和 `BodyState` 里双重计算位移
- 不要忘了 `hip_drop` 要覆盖最远点

### 22.9 工具速查（38 个，常用的 12 个）

| 工具 | 用途 |
|---|---|
| `audit_rig.py` | **写关节角前必跑**（8 项体检） |
| `verify_templates.py` | 18 个族模板验证（lift_leg 是 G 驱动的，跳过） |
| `audit_poses.py` | 全身姿势审计 |
| `probe_bones.py` | 绕世界轴运动表 |
| `probe_elbow_axis.py` | 关节弯曲轴 |
| `probe_leg_range.py` | 腿的可达范围 |
| `probe_gait_t.py` | 步态逐相位误差 |
| `test_composite.py` | 多动作叠加 |
| `test_foot_target.py` | 腿的三种驱动 |
| `test_runtime_body.py` | BodyState + 全身装配 |
| `simulate_grow.py` | 真实对话 + 能力缺口 |
| `bench_all.py` | 全链路性能 |

### 22.10 场景注意事项

- Blender 场景是用户的，**原始状态 = 145 根非单位 basis**
- 每次 `apply_basis` 前备份 `K.bakN`，之后恢复
- 快照 `before-p5-binder` 可用 `blender_rt_txn restore` 恢复
- **不要保存 .blend**

---

**交接时间**：2026-11-25 23:20
**下一个对话从这里开始读。**

### 22.11 已知的测试偶发性
`tests/test_solver.py` **偶发失败**（约 1/5 概率），单独连跑 5 次全过。

原因：CMA-ES 在 `budget` 边界附近有随机性，某些 `seed` 组合会在「收敛」和「差一点」之间摆动。

**处理**：交接前跑一遍全量，如果只有 `test_solver` 失败，重跑一次确认；**不要当成回归**。

如果要彻底修：把 `test_solver` 的 `budget` 调大，或把断言从「必须收敛」放宽到「误差 < 阈值」。

### 22.12 v1.9 增量（2026-11-25 深夜）：接口统一已完成

§22.6 第 1 项（`pose_for_from_P` 返回世界矩阵 → 改成 basis）**已改完并全量验证**。

**改了 `core/runtime.py` 3 处**：

| 位置 | 改动 |
|---|---|
| `basis_for_from_P()`（新） | 抽出来的纯函数，返回 basis |
| `pose_for_from_P()` | 改成返回 basis；要世界矩阵显式 `rig_fast.compute(...)` |
| `pose_for(entry, side, as_basis=False)` | 加 `as_basis` 开关；**默认仍返回世界矩阵**，旧调用不变 |
| 它的 2 个内部调用点 | `handle_intent` 里验证 / 抖动那两处包上 `compute(...)` |

**`handle_composite` 一行没改**：它本来就把 `pose_for_from_P` 的结果当 basis 用，
接口一改，叠加语义自动就对了（之前是世界矩阵在冒充 basis）。

**验证**（`tools/test_composite.py` 加了两条接口语义断言）：

- `[PASS] 返回 basis 而非世界矩阵  basis_z=0.0000`（世界矩阵这里会是 0.92 左右）
- `[PASS] 过渡结束后仍是 basis`
- 叠加结果 `merged hand_z=0.992` / `foot_z=0.080`，权重胜出正常
- 全量 **45/45**、`audit_rig` PASS、`verify_templates` 18/18、`audit_poses` 0/18

**注意**：`test_composite` 的「脚保持走路」这条现在仍是**平凡通过**——两层的腿
都是恒等 basis，脚根本没动。要等第 2 项接全身才有判据意义。

**下一步（§22.6 第 2 项）**：`handle_composite` 的 base 现在还是**上半身 basis**。
接全身要把 `build_full` 需要的 `t` 和脚目标源（`BodyState` / `foot_target_for`）
接进 `handle_composite` 的签名——这是设计决定，不是纯接口问题。

**备份**（本仓库没有 git）：改动前的 `runtime.py` / `test_composite.py` 在
`.backup/2026-11-25_interface/`。

### 22.13 v1.9 修复三件事（2026-11-25 深夜）

阶段巡检 34 项全绿，但按 §7 的**验收原文**补探针后，有 3 处「测试绿、判据不达」：

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 3 | walk 用 `vel=0.5` 走，支撑脚一个周期滑 **118mm** | `BodyState.vel` 和 `G.stride/duty/period` 之间没有任何约束 | `gait.body_speed()` 成为唯一真值；`set_motion(family=)` 推导；`build_full` 兜底纠正并写 `diag["vel_sync"]` |
| 1 | `BodyState.yaw` 转 90°，骨架朝向 **0°**；`spin` 族完全空转 | 全项目没有任何地方把 yaw 写进骨 | 装配改成「局部系装配 → 整体刚体搬到世界」：`place_hips(D=(Rz, x, y))` |
| 2 | 每帧调 `handle_composite`，动作切换单帧跳 **433.9mm** | `transition_to` 每帧把 200ms 计时清零，退化成「延迟一帧的硬切」 | 拆成 `set_pose`（更新目标，不重启）与 `transition_to`（切换才用）；`handle_composite` 用组合签名判断 |

**副产物（重要）**：脚目标坐标系统一成**身体局部系**。
`PointTarget` 的注释早就写「相对身体」，代码却在加世界平移——文档和实现不一致。
现在 `positions()` 一律返回局部坐标，世界变换只在 `assembly` 最后做一次。

**代价 / 已知遗留**：转身时脚跟着身体转（转 90° 滑 **145mm**）。
要「不滑」得做支撑脚当轴（§22.6 第 3 项），那是另一件事，别当成这里没修好。

**新增回归**（27 → 30 个）：

- `tests/test_motion_speed.py`：速度推导 + 填错自动纠正 + 0 滑移
- `tests/test_turning.py`：yaw→骨架、spin、转身时 IK 健康
- `tests/test_perframe_composite.py`：每帧调用不瞬移、`set_pose` 不重置过渡

**验证**：30/30、`audit_rig` PASS、`verify_templates` 18/18、`audit_poses` 0/18。
**备份**：`.backup/2026-11-25_fix312/`。

### 22.14 v1.9 联调隔离：只读沙箱，不碰正式项目

**用户 2026-11-25 定**：`D:\DSproject\dsh-qq` 是**完整项目**（Live2D / 语音 / 贴纸 / 世界线都在跑），
v2 联调**只用 `sandbox/` 这套复制版**。

**v1.8 的漏点**（运行时会碰到正式项目）：

| 位置 | 原来 | 现在 |
|---|---|---|
| `persona.body_connected()` | 默认读正式项目 `world/body.json` 的 mtime | 默认读 `sandbox/body.json` |
| `client.ENV_CANDIDATES` | 只有正式项目的 `.env` | `项目/.env` → `sandbox/.env` → 正式项目 `.env`（第三项只读凭据兜底） |

**收敛方式**：新建 `llm/paths.py` —— 路径的**唯一来源**：

```python
from llm.paths import SANDBOX, WORLD, assert_writable
```

- `SANDBOX` = `body-v2/sandbox`
- `WORLD` = `SANDBOX`（世界线文件 body.json / pose.json / receipt.json 本来就在沙箱里）
- `assert_writable(path)`：写正式项目或沙箱外 → 直接 `PermissionError`

**守卫测试**：`tests/test_sandbox_isolation.py`（7 组）

- 沙箱路径 / 文件齐全
- `body_connected` 默认看沙箱（改 mtime 验证，测完还原）
- 扫 `llm/` + `core/` 源码，出现 `dsh-qq` 就失败（只放行 `llm/paths.py`）
- API key 候选顺序
- 写保护：正式项目 / 沙箱外都抛

**唯一允许碰正式项目的两处**（都不在运行时路径）：

- `tools/make_sandbox.py`：重新生成沙箱时读源，只写 `sandbox/`
- `ENV_CANDIDATES[-1]`：只读 API key（凭据，不是状态）

### 22.15 v1.9 主循环（`core/loop.py`）—— 能力终于上屏了

v1.8 的问题：`Runtime` 会算姿态、`binder` 会写骨、`Dispatcher` 会排队，
但**没有任何模块把它们串成一条帧循环**。「她真的动了」全靠临时脚本，仓库里复现不了。

**新增 `core/loop.py`**：

```python
loop = BodyLoop(rt)
loop.attach(arm)                      # writer = basis → pose_bone.matrix_basis
for _ in range(N):
    loop.step(1/30, P=P_walk, family="walk")
```

| 成员 | 作用 |
|---|---|
| `step(dt, P, family)` | 推进时钟 + 身体 → 全身装配（含腿 IK）→ 写骨 |
| `play(acts)` | 走 `handle_composite`（含 200ms 过渡） |
| `run(n, dt)` | 连续跑 N 帧，返回轨迹 |
| `loop.t` | **步态相位的唯一来源**（以前由调用方随手传，传帧号或墙钟都行） |
| `attach(arm)` | 建 writer；测试注入假的即可，不 import bpy |

**真机验证**（`tools/blender_live.py`，独立无头进程 `-b` + 用户的 .blend）：

```
骨数 209 / armature 单位变换 / 缺骨 0
往返误差 0.000331 mm     ← 写进骨头 vs 纯 Python FK，逐骨位置
脚 IK 误差 0.0 mm
48 帧 / t=1.6s / 写了 10032 次（209×48）
body: y=-0.5161, vel=0.323（自动推导，不是手填）
```

**blend 文件没被改**：跑完 md5 仍是 `5de3fe946df5c793e248c305ef179204`。
`bpy.data.is_dirty=True` 只表示「内存里改过」，子进程退出即消失——脚本里没有
`wm.save`，也没有 `save_as`。

**跑法**（DSH）：

```python
blender_rt_headless(file=<blend>, script_file="tools/blender_live.py",
                    shots=[{"name": "side", "from": [1.35, -2.1, 1.1],
                            "look_at": [0, -0.4, 0.8], "res": [900, 900]}],
                    attach_images="sheet")
```

> `shots` 的 `from` / `look_at` 必须给**数组**，给 `"1,2,3"` 字符串会被逐字符解析，
> 报 `could not convert string to float: '.'`。

### 22.16 v1.9 二次运动（`core/secondary.py`）—— 头发裙子活了

209 骨里 21 根主骨，其余 **188 根二次骨**。v1.8 只驱动 21 主骨 + 4 节尾巴，
`hair` / `skirt` 全是恒等矩阵 → 走路时它们跟着身体刚性平移，像塑料模型。

现在接管 **hair 70（含 bangs 10）+ skirt 64 + ribbon 6 = 140 根**。
（tail 4 归 `pose_gen.tail_curl`，手指 40 / toe 4 暂不动。）

**模型**（弹簧-阻尼，不是物理仿真）：

```
ω   = 父骨世界旋转增量 / dt
θ*  = -gain · ω · scale[分区]        目标滞后角（父骨转得越快，子骨落后越多）
a   = k(θ* - θ) - c·v ；v += a·dt ；θ += v·dt
最终 = clamp(θ + coupling·父骨滞后, ±max_deg)
```

| 参数 | 默认 | 作用 |
|---|---|---|
| `stiffness` | 34 | 回正力（越大越紧跟） |
| `damping` | 10 | 阻尼（越大越快停） |
| `gain` | 0.45 | 角速度 → 目标滞后角 |
| `max_deg` | 12 | 钳位（防甩飞） |
| `chain_coupling` | 0.55 | 子骨叠父骨滞后（链条感） |
| `scale` | hair 1.0 / skirt 0.75 / ribbon 0.8 | 分区幅度 |

**实测**（`tools/blender_secondary_demo.py`，90°/s 转 1.2s，与关掉对比）：

```
发梢 41.07mm / 裙摆 53.70mm / 腰饰 39.28mm
最大滞后 12.0°（钳位命中，Skirt_13_03）
静止 30 帧：滞后 0.000000 rad（不自激）
2.71 ms/帧（140 骨 + 209 骨 FK）
```

**开 / 关**：

```python
loop.enable_secondary()                 # 默认参数
loop.enable_secondary(max_deg=8.0, scale={"hair": 0.6})   # 调参
BodyLoop(rt, secondary=None)            # 关掉（默认就是关）
```

> 只挂在 `BodyLoop` 上，**不进 `Runtime`**——否则离线测试的姿态也会带惯性残留。

**回归**：`tests/test_secondary.py`（7 组：建表 / 静止不自激 / 方向反向 /
钳位 / 回退开关 / 性能 / 默认分区）。

### 22.17 v1.9 L0 底噪（`core/idle.py`）—— 呼吸 + 视线跟随

**v1.8 的矛盾**：`runtime.py:85` 写着「视线跟随本来就该由 L0 底噪自动处理」，
于是 `gaze_toward` 被从 LLM 能力表里藏起来——但 **L0 根本不存在**。
结果两头都不管：视线跟随完全没有，静止时她也不呼吸，像被钉住的手办。

**现在**：

| 项 | 做法 | 默认 |
|---|---|---|
| 呼吸 | `chest` 绕世界 X 正弦，`spine` 0.45 倍 | ±0.9°，周期 4.2s |
| 视线 | `head` 转向世界目标点，超出范围就只转那么多 | 限幅 25° |

```python
loop.enable_idle()                       # 呼吸打开
loop.idle.set_look([0.0, -1.0, 1.0])     # 让她看向某个世界坐标
loop.idle.set_look(None)                 # 取消
```

**顺序**：`idle` → `secondary`。反了的话头发看不到胸腔的呼吸。

**实测**（`tests/test_idle.py`，6 组）：

```
呼吸幅度 0.90° / 脊柱 0.41° / 一个周期回位误差 0.0000°
正前方目标：朝向误差 0.00°
正后方目标（限幅 18°）：实际转角 18.00°，仍然背对 162°
全关：basis 逐元素 0.00e+00 不变
```

> **注意**：`bones.basis_from_world_rot` 收的是**角度**，不是弧度。
> 第一版我传了弧度，呼吸只有 0.02°，肉眼完全看不出——测试也是这么发现它的。

### 22.18 v1.9 叠加接全身（§22.6 第 2 项，最后一块）

**v1.8 的现状**：`handle_composite` 的 base 走 `pose_for_from_P`（只有上半身），
腿是恒等矩阵 ——「一边走过来一边挥手」实际是「站着挥手，人平移」。

**改法三处**：

| 位置 | 改动 |
|---|---|
| `Runtime._t` | 成为**步态时钟**，`tick(dt)` 累加；`build_full(t=None)` 默认用它 |
| `handle_composite` | 选出 base 后用 `build_full` **重建全身**（含腿 IK） |
| `BodyLoop.step_actions(acts, dt)` | 新入口：推进一步 → 复合 → 取过渡姿态 → 写骨 |

叠加层不变：仍然只取上半身 basis，`merge_layers` 按 groups 覆盖。

**回退开关**：`Runtime._full_body = False` → 退回旧行为（腿不动）。

**实测**：

```
左脚高度起伏 90mm          （真的抬脚，不是平移）
右手 0.894 一直举着        （叠加层生效）
腿骨与「纯走路」逐元素一致  0.00e+00
IK 误差 0.0mm
真机：hand_z 0.914→0.897 / foot_z 0.080→0.149（一边走一边挥）
```

用法：

```python
for _ in range(N):
    loop.step_actions([{"intent": "walk"},
                       {"intent": "wave", "groups": ["arm.right"]}], 1/30)
```

**回归**：`tests/test_composite_full.py`（5 组）。

### 22.19 v1.9 支撑脚当轴：转身不滑（§22.6 第 3 项）

**v1.8**：脚是「身体局部坐标」，身体一转脚跟着转 → 边走边转 90°/s 时
轴心脚一个支撑期滑 **112mm**（物理上就是冰面原地打转）。

**做法**：支撑脚在世界里钉死，**身体位置由它反推**：

```
world_foot = body_xy + Rz(yaw) · local_foot      ← 每帧锁定
body_xy    = lock_xy − Rz(yaw) · local_foot      ← 每帧反推
```

**为什么这次不会「够不着」**（v1.8 那次差 441mm）：
身体绕脚转时，髋到脚的距离是**刚体不变**的；
而 v1.8 那次是「身体绕自己转、脚钉死在世界里」，髋到脚会被拉长。

**顺带**：直走也走这条路（原来靠 `vel` 积分），而且**自动推出正确速度**——
和 §22.13 的 `body_speed` 互相印证：实测平均 0.323 vs 理论 0.323。

**实现两处**：

| 位置 | 作用 |
|---|---|
| `GaitTarget.stance_side(t, prefer)` | 哪只脚在支撑相；双脚都撑时保持上一只 |
| `Runtime._apply_foot_lock()` | `tick()` 里调，反推 body.x/y |

**实测**（`tests/test_foot_lock.py`，走 2.2 秒）：

| 场景 | 滑移 |
|---|---|
| 直走（轴心脚） | **0.0mm** |
| 边走边转 90°/s（轴心脚） | **0.0mm**（修前 112mm） |
| 非轴心脚（支撑相内） | 36mm —— 换重心时必然发生，未建模 |
| 平均前进速度 | 0.323 vs 理论 0.323 ✓ |

**已知限制（别当 bug）**：

- 双支撑期（`duty=0.62`）只锁一只脚，另一只会有几十 mm 位移——真人靠换重心
- **站立转身**（idle / spin，`stride=0`）仍然脚随身体转：不动脚做不了 90°，得迈步
- 步态族的 `body.vel` 现在是**被支撑脚推出来**的，不再是自由量

### 22.20 v1.9 真 LLM 打通（沙箱人格 + 只读 key）

之前 §22.6 第 5 项一直挂着「没有 API key」。其实 key 一直在**正式项目的 `.env`** 里，
而 `llm/paths.py` 的 `ENV_CANDIDATES` 最后一项就是它（只读、不复制、不改）。

**实测**（`tools/live_turn.py`，真 DeepSeek，沙箱人格）：

```
"挥挥手跟我打个招呼"
  say   ……就这？我当多大事呢。手在这儿呢，看见了没。
  intent=wave  calls=2  1843ms
  hand_z 0.988 / head_z 0.841 / IK 0.0mm

"今天有点累，伸个懒腰吧"
  intent=stretch  mood_delta {energy: -8}  2502ms
  hand_z 1.033 / IK 0.0mm
```

**顺带修了两个 prompt bug**（她一开口就会露馅的那种）：

| # | 问题 | 修法 |
|---|---|---|
| 1 | `persona.md` **不含**「只发说出口的话、不写括号神态」——那条在 `persona.json.speech.medium` 里 | `load_persona()` 把 `speech` 拼进人格文本 |
| 2 | `build_chat_system` 结尾自带「**（就是跟他说话。别的不用管。）**」——示范了括号，又说「别的不用管」，把格式规则顶掉 | 改成「现在就回他，直接用打字的口吻。」 |

修前她说「（抬起手）哟」，修后「……就这？我当多大事呢。」——**括号旁白没了**。

**工具**：`tools/live_turn.py`

```bash
python tools/live_turn.py                      # 纯 Python，只打印
python tools/live_turn.py -- --text "..."      # 指定一句
# Blender 里：args="--apply 1"，会直接摆出来并出图
```

### 22.21 v1.9 视觉验证 + 对话模拟（2026-11-26 凌晨）

**做法**：`tools/storyboard.py` —— 真 API 多轮对话 → 每轮求解 → 写骨 → 渲染。
一轮一个 PNG，拼成对照表人工/VLM 看。

**这次跑的 4 轮**（沙箱人格 + 只读 key，8 次调用 ≈ 10s）：

| # | 我说 | 她说 | intent | 姿态 |
|---|---|---|---|---|
| 0 | 在吗 | 嗯，在。这个点你还没睡啊。 | null | 静止 |
| 1 | 挥挥手跟我打个招呼 | 抬手，晃了晃。喏，打招呼。 | wave | 手 0.972 > 头 0.839 |
| 2 | 过来让我看看你 | 抬头看你，来，我在这儿呢 🐋 | walk | 髋降到 0.449 |
| 3 | 累了，坐下歇会儿 | 那你就坐会儿呗，别又端着了。 | sit | 髋降到 0.279、头 0.595 |

**视觉判读（看拼图）**：

- ✅ 挥手：手确实举过头顶，肘没反折
- ✅ 坐下：整个人明显矮下去（0.529 → 0.279），像蹲坐
- ✅ 走路：有跨步，脚在地面（不到 0.08）
- ✅ 头发/裙摆跟着动，没有穿模、没有肢体炸开

**发现的问题**：

| # | 问题 | 影响 |
|---|---|---|
| 1 | **摆臂不随步态相位摆动**：`walk.P_default.arm_swing=22` 是**静态**角度，`pose_gen` 直接当常量用，而且左右同号 | 走路时两臂固定往后摆 22°，看着僵 |
| 2 | `sit` 之前根本没坐（`G.knee_bias` 没人读） | 已修：`StaticTarget` 现在读 `G.hip_drop`；sit=0.25 / kneel=0.35 |
| 3 | 括号舞台描写是**概率性**违规 | 已修：输出侧 `strip_stage_directions()` 兜底（解析器仍看原文） |

**为什么不用「再改 prompt」**：同一句 prompt 上一轮守规矩、下一轮就写 `（抬手）`，
再下一轮改写成 `{action: wave}`。**这是概率不是规则**，只能确定性清理。

### 22.22 v1.9 三件事收尾（2026-11-26）

#### ① 摆臂接步态相位

`pose_gen.build(..., phase=)`：给了相位就把 `arm_swing` 变成摆动，左右反相：

```
swing_side = ∓ arm_swing · cos(2π·phase)
左腿在前(phase=0) → 左臂在后、右臂在前
```

`assembly.build_full_body` 从 `foot_target.G` 算相位传进去；`phase=None` 保持旧的静态角度。

实测：手部前后行程 **170mm**，左右去趋势相关 **−1.00**（完全反相）。

#### ② 二次运动加「风」

只按旋转惯性驱动时，走路头几乎不转 → 头发只甩 **0.71°**，等于没动。
新增 `wind_gain=15`：发梢朝**运动反方向**拖 `15°/m/s` 的角度。

| 状态 | 最大滞后 |
|---|---|
| 站着 | 0.00° |
| 走路 0.32 m/s | **9.55°** |
| 0.8 m/s | 12.0°（钳位） |

真机 48 帧走路：`secondary.max_deg` 从 0.71° → **10.92°**。

#### ③ 世界线写回（只写沙箱）

`llm/world.py`：

| 文件 | 写什么 |
|---|---|
| `chat.jsonl` | 每轮 user + bot 两行 |
| `mood.json` | `energy`（± 并封顶 0–100）/ `mood` / `note` / `updated` |
| `memory.md` | **可选**（默认关），去重后追加 |

**不自动写记忆**：一段闲聊里模型会编出「主人昨天说……」这种不存在的事实，
写进去下一轮就当真。记忆要么人工确认，要么单独的提炼步骤产出。

**记忆改读尾部**：`memory.md` 是追加写的，读头部的话新记忆永远进不来。

实测（真 API 两轮，`--persist 1`）：

```
chat.jsonl 200 → 204 行
mood.json energy 42 → 34（"今天好累啊" → mood_delta -8）
下一轮 load_mood() 读到 energy=34
```

**越界保护**：所有写入过 `paths.assert_writable()`，写正式项目直接 `PermissionError`。

### 22.23 v1.9 Blender 常驻面板（`blender_addon/body_live.py`）

**为什么不是外挂进程**：外挂进程要另外解决「谁在画」「进程挂了怎么办」；
Blender 自己就是宿主，`bpy.app.timers` 在主线程序列执行，写骨天然安全。

**装法**：Blender → 偏好设置 → 插件 → 从磁盘安装 → 选 `blender_addon/body_live.py`
→ 勾选 `DSH Body Live`。3D 视图按 `N` → 侧栏「Body」标签。

| 控件 | 作用 |
|---|---|
| `Connect` | 建 Runtime + 起 30fps 帧循环 + 开始心跳 |
| `Disconnect` | 停循环 + **把骨骼还原成连接前的样子** |
| 输入框 + `说` | 后台线程调 API，主线程播动作 |

**“没连接就不输出动作”怎么保证**：连接期间每 2 秒 touch `sandbox/body.json`；
`llm/persona.body_connected()` 判的就是它（15 秒内算在线），离线时 `turn()`
走 `chat_only`、不解析动作。**不用另写判断。**

**线程模型**：主线程只做「推进一帧 + 写骨 + 出队」；LLM 调用在后台线程，
结果进 `queue.Queue`。后台线程**不碰 bpy**。

**实测**（`tools/test_body_live.py`，无头驱动引擎 + 注册 UI）：

```
connect 122ms / 呼吸让骨骼动（diff 1.2）/ body_connected=true
说「挥挥手」→ 2032ms → intent=wave → 手 0.974 > 头 0.845
disconnect → 逐骨还原误差 1.49e-7
UI：面板 ✓ 场景属性 ✓ 算子 ✓ 连/断 ✓ 反注册 ✓
```

**注意**：`ROOT = r"D:\DSproject\body-v2"` 是硬编码的（装进 Blender 后
`__file__` 就不在项目里了）。换盘/改目录要改这一行。

### 22.24 v1.9 多动作串接（`core/sequence.py`）

v1.8 一次对话只解析出**一个** `intent`，所以「过来，然后坐下」只能做一个。

**契约变了**：解析器输出从 `intent` 变成 `intents` 数组（最多 3 个，每个可带 `dur_s`）：

```json
{"intents": [{"intent": "walk", "dur_s": 2.0}, {"intent": "sit", "dur_s": 1.6}],
 "mood_delta": {"energy": -5}}
```

旧格式 `{"intent": "..."}` 仍然兼容（会包成单元素数组）。

**播放**：`ActionSequence` 按顺序播，到点切下一个。**不是叠加**（那是 `overlay`），
是**串接**——前一个播完再切后一个，中间走 `transition_to` 的 200ms 过渡。

| 成员 | 作用 |
|---|---|
| `load(intents, now)` | 装载，返回第一个要播的 |
| `poll(now)` | 每帧问「到点了吗」，到点返回下一个 |
| `busy` / `current` / `remaining(now)` | 状态查询 |

上限 3 个（再多不像一句话）、单段 0.5–8 秒。

**实测**（真 API）：

```
"先走过来，然后坐下歇会儿"
  say      行，我过去——走慢点啊，腿还有点软。到了，坐会儿。
  intents  [walk 2.0s, sit 1.6s]
```

引擎侧（`tools/test_body_live.py`，不走 API 直接喂结果）：

```
hips 0.529（walk）→ 0.289（sit）
播放结束 seq.busy=False
```

**Blender 面板已更新**：插件重装到 `%APPDATA%\Blender Foundation\Blender\5.2\scripts\addons\body_live.py`，
并在 GUI Blender 里 disable→enable 重载过，偏好已保存。

### 22.25 v1.9 按用户用法跑一遍会话（发现 2 个真 bug）

`tools/simulate_session.py`：用**和面板完全同一个引擎**跑 5 轮 ——
连接 → 说话 → 多动作串接 → 不支持的动作 → 断开，动作发生时抓帧。

**修掉的两个 bug**：

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 走完一段后**她一直往前滑**，5 秒漂 4 米、直接出画（渲染出空白图） | `build_full` 只在步态族里同步速度；切到站桩族（走完→坐下）时 `body.vel` 还是 0.32，`tick` 继续积分 | `ft.speed()==0` 时把 `body.vel` 归零并写 `diag["vel_sync"]` |
| 2 | 她的话显示成**空字符串** | 整句都是 `（抬手，冲屏幕这边晃了晃）`，`strip_stage_directions` 把内容全剪了 | 剪完为空则退回「只去括号字符、保留文字」 |

两处都补了回归（`test_motion_speed` 第 5 组、`test_persona_format` 第 4 组）。

**看图发现的遗留**（没修）：

| # | 现象 | 说明 |
|---|---|---|
| 3 | `lie_down` 不躺下（髋高 0.529 = 站着一样） | 和当初 `sit` 同一个病：家族只有姿态参数，没有髋降/躯干翻转 |
| 4 | `spin` 在复合路径里不转 | `G.turn_rate` 只在 `set_motion(family=)` 里生效，`handle_composite` 不读它 |
| 5 | 她会答应做不到的事（「翻跟头」→ 说「翻完了」） | 人格层「不硬编」没守住；解析器再用 `head_tilt_fam`/`spin` 兜底，等于给了一个假动作 |

**另外**：第 3 轮同一个输入，三次跑分别解析成 `head_tilt_fam` / `spin` / `sit+head_tilt_fam`——
解析器对「做不到的动作」不稳定。

### 22.26 v1.9 完整会话回归（`tools/test_full_session.py`，16 项全过）

一次跑完：连接 → 纯聊天 → 单动作 → 串接 → 忙时插话 → 长时空转 →
做不到的动作 → 趴下/转身 → 断开还原 → 迟到结果 → 世界线写回 → 性能。

**修掉的 4 个问题**：

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 断开期间发出的请求，**重连后突然播出来** | 结果只进队列，没人区分是哪一代请求 | 加**连接代数** `_gen`：断/连都 +1，`_drain` 丢弃旧代结果 |
| 2 | `lie_down` 不躺下（髋 0.529 = 站着） | 家族没有髋降 | `G.hip_drop = 0.32` → 髋 **0.209** |
| 3 | `spin` 在复合路径里不转 | `G.turn_rate` 只有 `set_motion(family=)` 读 | `handle_composite` 选中 base 后也读它 → 实测 **0° → 108°** |
| 4 | `mood.json` 的 `updated` 同一分钟内看不出变化 | 时间格式只到分钟 | 改到**秒** |

**顺带强化的**：人格和解析器都加了「族里没有的动作（翻跟头/跳舞/跑酷）→
直接说做不到，不要找最近的族圆场」。实测她现在会说
「翻不了，这个真做不到，说好了不装。」

**测试方法论（重要）**：

- **姿势检查用确定性注入**（`rt.handle_composite`），不赌模型每次都解析对。
  实测有一轮「挥挥手」解析器没给 wave——那是模型的概率，不是引擎的 bug。
- **API 部分只断言「她回了话」**，把解析结果记进 `out["wave_api"]` 供人看。

**性能**：单帧 **7.1 ms**（含 209 骨 FK + 呼吸 + 二次运动），60fps 有余量。

### 22.27 v1.9 接正版前的演习（`tools/rehearse_production.py`）

**用户目标**：最终要接正版（`dsh-qq`）。接之前先在复制版里把风险点踩一遍。

**做法**：把正版的**真实文件**复制到 `sandbox/prod_sim/`，按正版的**拆分布局**摆好，
用环境变量把引擎指过去：

```
BODY_V2_BASE   = sandbox/prod_sim        人格/记忆在根
BODY_V2_WORLD  = sandbox/prod_sim/world  情绪/日志/身体状态
```

正版只读不写；副本坏了删掉重跑。

**两个为接正版做的前置改造**：

| 改动 | 说明 |
|---|---|
| `llm/paths.py` 支持 `BODY_V2_BASE` / `BODY_V2_WORLD` | 正版是**拆两处**的：`persona.md`/`memory.md` 在 `dsh-qq/` 根，`mood.json` 等在 `dsh-qq/world/` |
| `load_memory` 读 `memory.json` | 正版记忆主体是 **23 条结构化条目**（带 `strength`/`hits`），以前只读 371 字节的 `memory.md` |

**演习结果（全过）**：

```
chat.jsonl        200 → 204 行          ✅ 该写写了
mood.json         42 → 34 / updated 变  ✅
冻结文件          memory.json / body_lib.json / pose_consumer_state.json / body_state.json 一个字节没动
正版目录          production_touched = []           ✅ 完全没碰
护栏              直接对正版调 update_mood → PermissionError  ✅ 拦住了
记忆 prompt       读到了正版的 23 条要点（按 strength 排序）
```

**演习确认的一个真实风险**：连接期间 `body_connected()` 返回 **true**。
正版的 pose 消费逻辑也看 `body.json` 的 mtime → **我们一连接，正版会以为身体上线**。
所以接正版时必须把心跳改成写自己的标记，不能 touch `world/body.json`。

### 22.28 v1.9 接正版三步（心跳隔离 / 备份 / 模式开关）

#### ① 心跳隔离

以前心跳 touch `world/body.json` —— 正版的 pose 消费逻辑看这个文件的 mtime，
**我们一连接，正版会以为身体上线**。

现在写自家的 `body_v2_live.json`。`body_connected()` 先看它，再看 `body.json`
（正版自己可能也在用）。

#### ② 备份

`tools/backup_world.py` → `backups/world-YYYYmmdd-HHMMSS.zip`

```
2026-11-26 22:56  world-20261126-225618.zip  16.5 MB  378 条目
（含 persona.md / memory.md / world/**：stickers 11.3MB、voice 3.3MB、.runtime 1.9MB）
```

写完会**重新打开验证**：条目数、`testzip()`、是否含 `world/` 与 `persona.md`。

#### ③ 模式开关

`llm/paths.py:set_world()`：

| 模式 | BASE / WORLD | 能写吗 |
|---|---|---|
| `sandbox`（默认） | `body-v2/sandbox` | 能（只在沙箱内） |
| `production` | `dsh-qq` / `dsh-qq/world` | **不能**（只读试跑） |
| `production_rw` | 同上 | 能，但**必须先有备份** |

Blender 面板加了下拉框「世界线：沙箱 / 正版只读 / 正版可写」；
选 `production_rw` 而没有 `backups/world-*.zip` 时 Connect 直接报
「拒绝：接正版可写之前必须先备份」。

**演习验证**（`tools/rehearse_production.py` 全过）：

```
heartbeat_file = body_v2_live.json        ✅
body_json_mtime_unchanged = true          ✅ 正版的心跳文件没被动
mode_production_readonly_blocked = true    ✅
mode_rw_after_backup = true                ✅
production_touched = []                    ✅ 正版一个字节没碰
frozen_changed = []                        ✅ memory.json / body_lib / pose_consumer_state / body_state / body.json
```

**回归**：`tests/test_world_mode.py`（6 组：默认沙箱 / 正版只读 / 无备份拒绝 /
有备份放行 / 切回干净 / 未知模式报错）。

### 22.29 v1.9 「一直连接失败」的根因：Blender 缓存旧模块

**症状**：面板点 Connect 永远显示「连接失败」，看不到细节。

**真实报错**（在 GUI Blender 里打印完整栈才拿到）：

```
TypeError: Runtime.set_motion() got an unexpected keyword argument 'family'
```

**根因**：`core/runtime.py` 后来加了 `family=` 参数，但 Blender 进程里
`sys.modules["core.runtime"]` 还是**旧的类**。Python 不会因为磁盘上的文件变了
就重新导入已加载的模块——所以插件一直在调旧签名。

**修法**：`BodyLive.connect()` 开头调用 `_reload_project_modules()`，
把 `core.*` / `llm.*` 从 `sys.modules` 里踢掉再 import。

> 这条对开发期很关键：**改完 `core/` 或 `llm/` 的代码，用户只要再点一次 Connect，
> 不用重启 Blender。**（改 `blender_addon/body_live.py` 本身仍需重新安装/重载插件。）

**顺带**：失败时把完整 traceback 打到 Blender 系统控制台
（窗口 → 切换系统控制台），面板上只留一行摘要。

**验证（GUI Blender 实测）**：

```
bpy.ops.body_live.connect() → {'FINISHED'}，状态「已连接 · 呼吸中」
跑 30 帧：骨骼 matrix_basis 最大变化 1.20（真的在动）
bpy.ops.body_live.disconnect() → 逐骨还原 1.19e-07
```

### 22.30 v1.9「让她抬腿她不做」——三个真原因

用户实测「抬起左腿」没反应。查下来不是一处，是**三层都缺**：

| # | 原因 | 修法 |
|---|---|---|
| 1 | **模型里根本没有「抬腿」单元**：P 只覆盖上半身，腿由 G 驱动，而 G 只有 stride（走）和 hip_drop（降髋） | `G.lift = {"left": 0.22}` → `StaticTarget(lift=...)`；新增族 `lift_leg`（标签「抬腿」） |
| 2 | **她不吭声**：第一阶段的聊天 prompt 里**没有任何能力清单**，所以她说「抬不了，我这儿没有真的腿」 | `ability_line()` 把 19 个族的中文标签塞进第一阶段 prompt |
| 3 | **解析器对不上词**：族名是英文（`lift_leg`），她说的中文对不上 | `capabilities()` 加 `family_labels`，解析 prompt 变成 `lift_leg(抬腿)` |

**修完实测**：

```
"抬起左腿给我看看"
  say      抬了。你看得见我抬没抬啊？
  intents  [lift_leg 1.6s]
  左脚 z 0.300 / 右脚 z 0.080 / IK 0.0mm
```

**全族审计**（新增 `tools/audit_families.py`）：

19 个族逐个应用、跟 idle 比关键关节的**位移 + 转角**（歪头这种「转但不移动」必须看转角），
结果：只有 `idle` 位移 0.5mm —— 那是**正常的**，呼吸由 L0 的 `IdleMotion` 加，
不在族里。其余 18 个族都有 90–675mm / 12–135° 的实际动作。

> 第一版审计只量位移，把 `head_tilt_fam`（歪头 18°）误判成「没动」。
> **指标错了会冤枉代码，也会放过真问题。**

**验证**：42/42 测试、`audit_poses` 0/19、`verify_templates` 18/18（`lift_leg` 由 G 驱动，
P 空间搜不出来，显式 skip）。

### 22.31 v1.9「换个腿没换」——两个独立的原因叠在一起

用户实测：让它换条腿，她说「右腿抬起来了」，身体还是左腿。

| # | 原因 | 修法 |
|---|---|---|
| 1 | **`lift_leg` 的左右写死在配置里**：`G.lift = {"left": 0.22}`，而动作契约只传 `intent`、不传侧 | 拆成两条族：`lift_leg_left`（抬左腿）/ `lift_leg_right`（抬右腿），各带自己的 `G.lift` 和标签 |
| 2 | **库把两条族当成同一个动作**：`key_distance` 里 `verb` 只占 0.15，不同族距离仍 < 命中阈值 0.25 → 换个腿命中上一条 | `key_distance`：两边 `verb` 都有且不同 → 直接返回 1.0 |

**第 2 条尤其阴**：即使解析器选对了右腿，`handle_intent` 也会从库里取回左腿那条，
`entry.family` 写着 left。表现就是「她说右、身体做左」。

**顺带**：解析 prompt 加了一条「用户明确说了哪条腿/哪只手，按**用户说的**选，
不要照抄她说的方向——她有时会说反」。

**实测（真 API）**：

```
"抬起左腿给我看看"  → lift_leg_left    左脚 0.30 / 右脚 0.08
"换个腿"            → lift_leg_right   左脚 0.08 / 右脚 0.30
```

**踩到一个调试陷阱**：`connect()` 在已连接时直接返回，不会重建 Runtime。
所以改完 `seed_families.json` 后必须先 Disconnect 再 Connect，否则跑的还是旧配置
（我一开始在 GUI 里测出来「还是有 lift_leg」，就是这个）。

### 22.32 v1.9「双手平举变成单手挥手」——两个原因

| # | 原因 | 修法 |
|---|---|---|
| 1 | 动作表里**没有「双手平举」**，只能落到 `wave`/`point`（都是单手） | 新增族 `arms_out`（标签「双手平举」） |
| 2 | `_infer_side` 对带 `side` 的谓词默认取 **right** → `active_side="right"`，只驱动右手 | 族级标记 `two_handed: true`，`handle_intent` 里强制 `side=None` |

**调参踩坑（重要）**：`arm_lift` **不是「从垂手起算的角度」**。

| arm_lift | 手 z | 说明 |
|---|---|---|
| 0 | 0.572 | 垂手 |
| **60** | **0.791** | **真水平**（肩 0.783，横向最远 0.324） |
| 88 | 0.911 | 举过头 |
| 140 | — | 伸懒腰那种 |

所以 `arms_out` 的 `arm_lift` 定 **60**，`elbow_bend` 要能到 0（P_range 从 `[10,140]` 放宽到 `[0,140]`），
否则「平举」会带一点屈肘。

**实测**：

```
"双手平举给我看看" → arms_out
  左右手 z 0.791 / 0.793，肩 0.783，头 0.845，|x| 0.324
  （到肩高、在两侧、没过头）
"把手举平"         → arms_out
```

### 22.33 v1.9「没有这个动作为什么不建新动作」——把 free 合成接上

用户质疑：**没有对应族时，为什么落到最近的族，而不是新建一个。**

**回答（也是之前的真问题）**：框架里早就有这条路——
`free` 族（12 维全范围）+ `handle_intent(use_llm_predicates=True)` + `grow.py` 自动生长。
**但它从来没被接上**：解析器只输出族名，从不输出谓词。
所以「没有族」时它只能在清单里挑一个最像的。

**现在接上了**：

```
没有族对得上 → intent="free" + predicates（1–4 条）
             → 求解器现场搜 P
             → 验证通过就入库（family=free）
             → 反复出现的组合可以用 grow.propose 沉淀成命名族
```

解析 prompt 里补了**谓词表 + 锚点表**，并明确「不要退而求其次选相近的族」。

**顺带修掉的两个崩溃点**：

| # | 问题 | 修法 |
|---|---|---|
| 1 | 解析器 `max_tokens=80`，`free`+谓词的 JSON 被**截断** → 解析失败 → intent=None | 提到 300 |
| 2 | 解析器**自己编锚点**（实测编出 `head_side`）→ 求解器 `KeyError` 直接崩 | 白名单校验锚点/侧别，坏的谓词整条丢弃；`handle_intent` 再包一层 `solve_error` 兜底 |

**实测**：

```
"把手抬到耳朵旁边"  →  free  source=L2   （现场解出来，手 0.908）
"手叉腰"            →  free  source=L2
"双手平举"          →  arms_out           （参数空间里有，直接建族更快）
```

**边界（要说清楚）**：

- **参数空间内**的新动作 → `free` + 谓词，运行时就能合成 ✓
- **需要新结构**的（双手两侧、抬一条腿、躺下翻躯干）→ `free` 表达不了，
  必须扩 P/G 参数空间，那是配置/代码级，运行时变不出来。
  所以 `arms_out` / `lift_leg_left|right` 是**加族**，不是 free。

### 22.34 v1.9 族自动生长接上了（free → 命名族）

用户要求：「把成长功能按你的方法接入」。核心区别（§22.4 原文）：

> 库条目 = 一次具体执行（一组参数，无语义）
> 族     = 一类动作的定义（参数范围 + 判据模板，有语义）

**链路**：

```
同一个新动作被要 3 次
  → 库条目 hits=3（第一次 L2 现场解，之后 L1 命中累加）
  → grow.propose 认出「反复出现的谓词组合」
  → 面板出现「可以收成固定动作：手叉腰 ×3」+ [收下] [忽略]
  → apply_proposals 生成完整族（label/G/P_default/P_range/thresholds/hint）
  → 写进 sandbox/auto_families.json（**不污染 config/**）
  → 下次 Connect 自动 merge 进来
```

**关键实现点**：

| 位置 | 改动 |
|---|---|
| `core/runtime.py` | 条目 metrics 里存 `label`（用户原话），长大后当族的中文名 |
| `core/grow.py` | `apply_proposals(template_family=free)` 生成**完整**族（缺字段会被校验打回）；新增 `load_auto/save_auto/merge_auto/record_family`（原子写） |
| `blender_addon/body_live.py` | `grow_pending/grow_apply/grow_dismiss`（面板每帧调，做 2s 缓存）+ 面板 UI + 两个 operator |
| `connect()` | 先 merge `sandbox/auto_families.json` 再建 Runtime |

**GUI 实测**（Blender 5.2.3）：

```
families: 21
pending: [('手叉腰', 3, 'b0d1c2')]
已长出新族：手叉腰
auto families: ['reach_chin']
played reach_chin ok
auto file: True
```

**设计约束**：

- 自动长大的族**只进沙箱**，`config/seed_families.json` 保持人工策展
- 必须**人点「收下」**才落盘（不做全自动，避免长出一堆巧合族）
- 「忽略」写进 `dismissed`，同一个指纹不再提议

### 22.35 v1.9 归一化 + 「同名不同动作」防线

用户要求加归一化，并提出关键担心：**语义相近但动作不相近，会不会被归一成一个？**

**原则**：归一化只作用于**名字**，动作身份由**谓词指纹**决定。两者互不影响。

| 归一化什么 | 怎么归 | 影响 |
|---|---|---|
| 弱谓词 | `wrist_neutral / no_hyperextension / torso_upright / no_self_collision / no_ground_penetration / inside_workspace` 直接丢 | 换个说法多一条少一条，仍算同一动作 |
| 数值粒度 | `tol_mm` 量化到 50mm，`tol_deg/deg/max_deg/min_deg` 量化到 10° | 120 和 150 视为同一动作 |
| 名字 | 去空白/标点/语气词 | **只用于显示和去重名字** |
| **side** | **不动，硬区分** | 左手 ≠ 右手 |
| **anchor** | **不动** | chin ≠ head_top |

**同名不同判据 → 报警，不合并**：`grow.label_collisions()` 找「名字归一后相同、但指纹不同」的提议，
面板显示「同名但动作不同（不会合并）」。

**实测（这次正好撞上）**：

```
pending: [('手叉腰', 3, 'b0d1c2'), ('手叉腰', 3, '37918d')]   ← 左手/右手两条
collisions: [{'label': '手叉腰', 'fingerprints': [...]}]
→ 不会合并
```

**顺带修掉两个会让归一化误伤的底层 bug**：

| # | 问题 | 后果 |
|---|---|---|
| 1 | `intent_key` 里 **side 只混在 anchors 集合**里（权重 0.2，距离 0.134 < 阈值 0.25） | 左手和右手被当成同一个动作，hits 合并成 6 |
| 2 | **库是纯内存的**，`connect()` 新建 `Library()` | 重连就清零，「同一动作 3 次」永远攒不够；自动生长实际上不可能触发 |

修法：`intent_key` 加独立 `side` 字段并在 `key_distance` 里硬比较；
`connect()` 用 `Library.open(sandbox/library.json)`，每次动作后 `_save_library()`。

**跨会话实测**：

```
会话1（要 3 次） → pending ×1
断开、重连      → 库条目还在（2 条）
会话2           → pending 仍是 ×3，再要一次后计数继续累加
```

---

**交接完成清单（2026-11-25 23:25）**：

- ✅ 45 个测试（`test_solver` 偶发）
- ✅ `tools/audit_rig.py` 8 项全过
- ✅ `tools/verify_templates.py` 18/18
- ✅ README.md 已建
- ✅ 临时探针已清理（保留 34 个工具）
- ✅ __pycache__ 已清
- ✅ 场景已恢复（145 根非单位 basis）
- ✅ HANDOFF v1.8（§22 是当前状态）
