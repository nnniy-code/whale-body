# 身体 v2 · 工作流程

> 配套：[HANDOFF.md](../HANDOFF.md)、[LOGGING.md](LOGGING.md)
> 本文回答一个问题：**一次「她想动」从发生到结束，中间经过哪些步骤、每步的输入输出是什么、怎么验。**
> 写代码前必读 §4.5；写代码时按本文的顺序实现。

---

## 0. 总览：一次动作的生命周期

```
用户说话 / 环境事件
      │
      ▼
① LLM 意图编译          输出 {say, act}
      │                    say → 立刻 TTS（不等动作）
      ▼
② dispatcher 分派
      │
      ├─► L1 查库（30 ms）──命中──► ③ play（30–200 ms 起播）
      │                      │
      │                      └─ 未命中
      ▼                      ▼
   ④ 播兜底（两级）     ⑤ L2 分片搜索（P → G → 精修，55–330 ms 纯计算）
                              │
                              ├─ 解出 → ⑥ 门判决（谓词全过）→ 入库 provisional → 替换兜底
                              └─ 无解 → ⑦ family_infeasible 回传 LLM
                                                │
      ┌─────────────────────────────────────────┘
      ▼
⑧ L3 夜间：BVH 精确复检 → verified / rejected（附归因）
      │
      └─ rejected → 罚项回灌重搜（离线，不限轮次）→ 再复检
```

**四条铁律**（违反任何一条都会退化成上一代）：

1. **说话不等动作**（TTS 先出）
2. **L0 先行**：每个 tick 先把底噪跑完，再给 L2 分预算
3. **失败就是失败**：不可达 → rejected / family_infeasible，不 clamp
4. **每帧从 base_rest 重建**（D10），禁止累加

---

## 1. 启动序列

| 步 | 动作 | 验收 | 失败表现 |
|---|---|---|---|
| 1 | 加载 `deepseek_rigged.blend` | 209 骨、0 action、帧 1–905 | 加载错 blend → 骨骼数不对 |
| 2 | 读 `config/*.json`（bounds / anchors / capsules / collision / emotion_bias / weights） | 全部存在且通过 schema 校验 | 缺文件直接拒绝启动（不要用默认值兜底） |
| 3 | 建 FK 缓存：`rest[]`、`parent[]`、`prelocal[parent⁻¹@rest]`、`connected[]` | 与 P0.5 golden test 一致 | FK 与 Blender 求值差 >0.01mm 就停 |
| 4 | 建 BVH（夜间用，启动时可延迟） | `mathutils.bvhtree` 可用 | 不可用则 L3 禁用并记 error |
| 5 | 注册 `bpy.app.timers`（主循环）+ 读一次 `emotion.json` 建低通初值 | 首个 tick 在 1 秒内触发 | 无 tick = 插件未挂上 |
| 6 | 写 `boot` 事件到 `logs/events.jsonl` | 有且只有一条 | 重复 boot = 有两个进程抢 |

**不要**在启动时打开 `render_daemon`——它按需启，只在 L3 时用。

---

## 2. 主循环（每个 tick）

```python
def tick():
    t0 = perf_counter()

    # ── ① L0：必须完成，不可跳过 ──────────────────────
    now   = perf_counter()
    phase = player.phase(now)                   # 由 player 维护（可暂停/变速），不是纯时间函数
    pose  = build_pose(base_rest, G, P, emotion(now), K)   # 纯 FK，0.018 ms

    # ── ② 只在渲染帧写骨 ──────────────────────────────
    if frame_changed() or pose_delta > 0.01:    # 别每 tick 都 update
        binder.write(pose)                      # 0.06 ms
        view_layer.update()                     # 1.3–1.7 ms（整个 tick 唯一一次）
        redraw()

    # ── ③ L2 分片（可中断）────────────────────────────
    if solver.active:
        spent  = (perf_counter() - t0) * 1000
        budget = clamp(FRAME_MS - spent - REDRAW_EST, 4, 12)
        solver.step(budget_ms=budget)           # 内部用 perf_counter 判边界
        if solver.done:
            on_solve_done(solver.best)
```

**关键约束**：

- `FRAME_MS` = **33.3**（场景 fps = 30）。不要用 16.7。
- `phase` 由 `player` 维护（**状态**），`pose` 每帧从 `base_rest` 重建（**无状态**）。两者不矛盾：状态记的是"播到哪了"，无状态记的是"这一帧怎么算"。**不要**把 phase 写成 `phase += dt` 再拿去累加姿态——那就退化成上一代的逐帧写绝对姿态。
- `view_layer.update()` 每 tick **最多一次**，且只在真正要显示时。L2 搜索期间**绝不调**。
- `budget` 收缩后要有回弹：连续 30 tick 没超时 → `budget *= 1.1`（上限 12）。

**验收**：

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 连续跑 10 秒，统计 `tick_ms` 中位数 | < 3 ms（无 L2 时） | `REDRAW_EST` |
| 连续跑 10 秒，`gap_ms` 抖动 | 90 分位 < 35 ms | `FRAME_MS` / timer 周期 |
| 强制 L2 跑 10000 次，抓单帧最大耗时 | ≤ 12 ms + 一次 update | `budget` 上限 |
| 固定 P 跑 1000 次，末次 vs 首次 | **差异 = 0**（D10 判据） | `build_pose` 是否有状态残留 |

---

## 3. L0 底噪

**输入**：`emotion(now)`（低通后的四轴）、`G.phase`、当前 `K`（若在动作中）
**输出**：完整的 21 骨姿态（`pose`）
**耗时**：0.018 ms（纯计算） + 0.06 ms（写骨，仅在渲染帧）

组成：

| 分量 | 来源 | 说明 |
|---|---|---|
| 呼吸 | 正弦，周期 3–5 s，作用于 `spine/chest`，**不碰 hips**（坑 #1） | 幅度受情绪 `aro` 调制 |
| 情绪偏置 | `emotion_bias.json` 的映射 | 见 §7 |
| 头部朝向 | 整头 look-at 目标（无眼骨，不做眼球） | 目标点由 K 或默认前方 1 m |

**验收**：

- 呼吸轨迹导出 CSV 画速度曲线，**没有平台**（P0 的连带验收；运行时不 bake Action，所以没有 Graph Editor 可看）
- 改 `emotion.json` 的 `val`，1 秒内 `spine` 姿态变化，且**变化是连续过渡**（不是跳变）

### 3.1 二次骨（188 根）怎么动

hair 60 / skirt 64 / ribbon 6 / tail 4 / 手指 40 / toe 4。这些**不进 CMA-ES 搜索**（HANDOFF §8.5 硬约束 1），但每帧要动——**它们才是「她活了」的主要视觉来源**。

| 路 | 做法 | 成本 | 风险 |
|---|---|---|---|
| a) Blender 物理 / 约束 | Jiggle 或自带 spring | 不占 Python | **不可复现**（顺序依赖），A/B 对照失效 |
| **b) Python 弹簧（固定 120 Hz 子步）** | 每帧对 188 骨积分 | 209 骨 FK 0.352 ms + 写 ~0.2 ms | 必须固定 dt（坑 #8） |
| c) 预烘焙跟随 | 由主骨运动查表 | 最便宜 | 不灵活，换动作要重烘 |

**推荐 b**：可复现、和 FK 同一套数学、能进 golden test。每帧多 ~0.5 ms（30 fps 占 1.6%）。

具体：每个 tick 把 `dt` 切成 `⌈dt / (1/120)⌉` 个子步再积分，**不要拿可变 dt 直接积**（库里的 VRM XPBD 就是这么做的；坑 #8 就是可变 dt 的后果）。

---

## 4. L1 查库

**耗预算 30 ms**，超时即视为未命中。

```python
def retrieve(intent, predicates, family):
    key = build_intent_key(predicates, family)   # 谓词 one-hot + 动词标签 + 锚点
    cands = library.query(key, k=5)              # 加权 Jaccard + 数值归一差
    for c in cands:
        if c.status == "failed":     continue    # 失败条目直接跳过
        if c.status == "rejected":   continue
        if not compatible(c, family): continue
        return c                                  # provisional / verified 都可以用
    return None
```

**为什么 provisional 也可以播**：L2 解出来当场就播，不等夜间复检（见 §6）。provisional 和 verified 的区别是"敢不敢长期用"，不是"能不能播"。

**验收**：

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 库里有 10 条，查已知意图 | 30 ms 内返回 | `library.query` 的索引 |
| 库里没有的组合 | 返回 None，进兜底 + L2 | — |
| 命中 failed 条目 | 不返回，继续查 | `status` 过滤 |
| 同一意图连续查 100 次 | 全部命中同一条（确定性） | 排序的 tie-break |

---

## 5. L2 求解（核心）

### 5.0 族不存在时怎么起步

LLM 第一次说一个新族（`pat_head`），库里没有它的 G 默认值。规则：

```
G0 = family.G_default            # 族已存在
     或 nearest_family.G_default # 意图余弦 > 0.5
     或 GLOBAL_STANCE_DEFAULT    # 兜底：站桩（duty 0.5 / stride 0 / period 1.0）
```

`GLOBAL_STANCE_DEFAULT` 写在 `bounds.json` 的 `_global` 键里。**保证任何新意图都有起点**，不会因为「族不存在」直接失败。

### 5.1 三段搜索

```python
def solve(family, predicates, budget_total=10000):
    # 阶段 1：搜 P（10 维）
    P1 = cma(dims=P, init=family.P_default, bounds=family.P_range,
             evaluate=lambda p: evaluate(family.G_default, p, predicates),
             budget=budget_total * 0.30)

    # 阶段 2：搜 G（11 维），P 固定为 P1
    G1 = cma(dims=G, init=family.G_default, bounds=family.G_range,
             evaluate=lambda g: evaluate(g, P1, predicates),
             budget=budget_total * 0.50)

    # 阶段 3：联合精修（21 维，步长收窄 50%）
    P2, G2 = cma_joint(init=(P1, G1), bound_scale=0.5,
                       evaluate=evaluate, budget=budget_total * 0.20)

    return (G2, P2), gate.evaluate(evaluate_raw(G2, P2, predicates))
```

**`evaluate` 的全部成本必须 < 0.02 ms**，否则分段预算失效。它内部只允许调 `fk.py` + `predicates.py`，**不许碰 bpy**。

### 5.2 分片边界

```python
solver.step(budget_ms):
    t0 = perf_counter()
    while not done:
        next_gen()                     # 一代 = popsize 次求值 ≈ 30 × 0.018 = 0.54 ms
        if (perf_counter() - t0) * 1000 > budget_ms:
            return                     # 让出
```

**不要按次数切**——GC 抖动会让某一 tick 突然超时。

### 5.3 收敛与失败

| 结果 | 条件 | 动作 |
|---|---|---|
| `converged` | 全谓词 `err == 0` 且 `max_deg ≤ 12°/帧` | 入库 `provisional`，播 |
| `partial` | 有谓词过、但有违规 | 入库 `failed`（带未满足项），不播，回传 LLM |
| `family_infeasible` | 10000 次后仍未全过，且失败模式一致 | 回传结构化错误（见 HANDOFF §4.5） |

**验收**（对应 P7）：

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 基准集 20 条已知动作，藏参数重解 | 成功率 ≥ 90%（族内） | `popsize` / 三段比例 |
| 跨族（G 不同） | 成功率 ≥ 70% | `family.G_default` 的相似度 |
| 真新意图（谓词组合库里没有） | 成功率记录即可（不设门槛，这是研究性指标） | — |
| 10000 次总耗时 | 纯计算 55–330 ms，墙钟 < 1 s | `FRAME_MS` / `budget` |

---

## 6. L3 夜间入库闸门

**不是当场后处理**。BVH 一条 0.5–2 s，塞不进实时。

**谁来跑**：`tools/l3_verify.py`，手动或 Windows 计划任务触发。**它自己是 `blender -b` 进程、加载同一个 blend**——这样 Blender GUI 关着也能跑。不要指望"Blender 开着到夜里自动跑"，那等于永不下班。

```python
def nightly_verify(max_rounds=5):
    for e in library.where(status="provisional"):
        bad = bvh_collision(e.params)                # 抽 5 帧（首/1/4/1/2/3/4/末）
        if not bad:
            e.status = "verified";  continue
        attribution = attribute(bad)                  # 相交三角形 → 骨 → 帧 → 深度
        for _ in range(max_rounds):
            e.params = re_solve(e, penalty=attribution)   # 罚项回灌，缩短预算
            bad = bvh_collision(e.params)
            if not bad:
                e.status = "verified"; break
            attribution = attribute(bad)
        else:
            e.status = "rejected"
            e.violations = attribution
            # 联动：该族若有 failed 记录，标记可重解
```

### 6.1 穿模判定（不是单一阈值）

`config/collision.json` 存 **REGION 对 × 允许深度**：

| 对 | 允许深度 | 判法 |
|---|---|---|
| `arm × body` | 5 mm | 手贴胸合法，超过 5 mm 算穿插 |
| `hair × head` | **相对静止姿态的增量 < 3 mm** | 头发本来就贴头，绝对深度必为正，用增量 |
| `leg × skirt` | 8 mm | 裙摆贴着腿 |
| `hair × skirt` / `arm × hair` | 0 mm | 基本不许接触 |

**抽帧数**：一条轨迹抽 5 帧。若某帧违规，只在相邻 ±2 帧内加采。不要全帧跑（30 帧 × 1 s = 30 s/条）。

### 6.2 归因（没有这步罚项就是噪声）

```python
def attribute(bad_pairs):
    out = []
    for tri_a, tri_b, region_a, region_b in bad_pairs:
        bone_a = vg_of_triangle(tri_a)      # 顶点组 → 骨
        depth  = signed_distance(tri_a, tri_b)
        out.append({"frame": f, "bone": bone_a, "region": region_b, "depth_mm": depth})
    return out
```

回灌时：**只加粗被归因的那根骨的胶囊**，不整体加粗（否则解空间被无谓压缩）。

### 6.3 验收（对应 P8）

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 10 族 provisional → 跑一夜 | verified 率 ≥ 90% | `collision.json` 阈值 |
| 每条 rejected | 有 `{frame, bone, region, depth_mm}` | `attribute` |
| 故意造一条穿模轨迹 | 100% 被 rejected | 阈值别调太松 |
| 全部 verified 的库 + 原 7 条用例 | 全过 | 回归门 |

---

## 7. 情绪链路（P1.5）

```
world/emotion.json   {axes:{val,aro,close,guard} 0–100, last_label}
        │  stat mtime 变化才读（不要每帧 json.load）
        ▼
   一阶低通（时间常数 1 s）
```

**唯一来源是 `emotion.json`。** LLM 在 `act.mood` 里给的那个 `mood` **不写回情绪轴**，它只是"这次动作的心情提示"，用于选族 / 调偏置幅度。**双源会让"她到底开不开心"变成不可解释的状态。**

        │
        ▼
emotion_bias.json：四轴 → 各通道偏置系数
        │  + 幅度封顶 ±15%
        ▼
   加性叠加到 P / G 的连续通道（不改 K.targets）
```

**四轴 → 通道的初始映射**（`[经验·待校准]`，P1.5 调）：

| 轴 | 主要影响 |
|---|---|
| `val`（愉悦） | `lean`（正=前倾更积极）、`head_nod` 幅度、`tail_curl` |
| `aro`（唤醒） | 呼吸频率、`arm_swing`、`period`（步子快慢） |
| `close`（亲近） | `head_yaw` 朝向、`stance_width`（靠近/远离） |
| `guard`（戒备） | `side_bend`、`arm_lift`（护身）、重心后移 |

**硬规则**：

- 偏置**绝不作用于 `K.targets`**——否则「手到嘴边」会被情绪推漂，D1 的旗舰证据失效
- 偏置幅度封顶 **±15%**（超了就是另一个动作，A/B 对照失效）
- 低通时间常数 **1 s**：情绪跳变时参数必须连续过渡，否则又一个 `big_step`

**验收**：

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 把 `val` 从 20 改到 90 | 1 s 内姿态变化，无跳变 | 低通时间常数 |
| 同一意图 × 两种情绪 | 目标点（手到嘴）偏差 **< 1 mm** | 确保偏置没碰 targets |
| 连续改 `emotion.json` 10 次 | `big_steps` 保持 0 | 低通 + 封顶 |

---

## 8. 一次完整对话的时序

```
t=0      用户说话
t=+?      LLM interpret（模型延迟，500 ms – 2 s）
t=T      act 到达 dispatcher
t=T+0    say 送 TTS（不等动作）
t=T+30   L1 查库完成
          ├ 命中 → t=T+80 起播（≤200 ms 验收）
          └ 未命中 → t=T+5 起播兜底；L2 后台分片
t=T+~600 L2 解出（墙钟，视预算）
          → 门判决 → 入库 provisional
          → 与当前播放做惯性化混合（200 ms）替换兜底
t=夜间    L3 复检 → verified / rejected
```

**验收（P5）**：动作起播不晚于 TTS 的**第一句结束**。不满足就是 L1 太慢或兜底没起。

---

## 9. 库的生命周期

```
        ┌── failed（无解，带 family_infeasible，有 TTL）
        │
新建 ───┼── provisional（L2 解出、能播、未复检）
        │        │
        │        ├── verified（BVH 过）
        │        └── rejected（BVH 不过 + 回灌重搜仍不过）
        │
        └── 条目可被 L3 重解，不新开条目（params 就地更新，留 history）
```

**TTL 规则**（第 14 份设计）：

- `failed` 在下列任一变化后失效，允许重解：`bounds.json` 变、`weights.json` 变、`capsules/collision` 变、骨架变（骨数/rest 变）
- `rejected` 不自动失效，需人工清（避免反复烧预算）
- 每次状态变化写 `library` 事件（append-only，可回溯）

---

## 10. 异常与降级

| 情况 | 动作 | 日志 |
|---|---|---|
| L2 超时未收敛 | 保持兜底，`family_infeasible`，不播半成品 | `solve_end` + `reason=timeout` |
| FK 与 Blender 校验失败（golden test 红） | **停止求解**，进只读模式（L0 仍可跑） | `error` + `fatalfk` |
| BVH 不可用 | L3 禁用，provisional 全部保持 provisional | `error` |
| `emotion.json` 读失败 | 保持上一次值，不跳变 | `error`（限流） |
| blend 未保存 | 拒绝任何写盘操作 | `error` |
| 主线程卡 > 200 ms（非 L2） | 记 `stall`，连续 3 次 → 降 L2 预算 | `stall` |

---

## 11. 验收点总表

| 阶段 | 怎么测 | 预期 | 调参位置 |
|---|---|---|---|
| P0 | 播一个旧动作，看 Graph Editor 速度曲线 | 无 0 速平台；`big_steps=0` | Hermite 端点速度 |
| P0.5 | golden test | 10 姿态 × 全骨 <0.01mm | `fk.py` |
| P1 | 任意姿态读快照 | 关节/末端/质心/支撑面齐全 | `proprio.py` |
| P1.5 | 改情绪 | 1 s 内变、无跳变、targets 不动 | `emotion_bias.json` |
| P2 | 抬脚/落脚 | 判定 100% 正确 | `contact.py` 阈值 |
| P3 | 故意造 5 种错 | 5 种全抓到 | 谓词阈值 |
| P4 | 给定意图查库 | 30 ms 出参数 | `library.query` |
| P5 | 对话 | 动作不晚于语音 1 句 | 兜底起播时机 |
| P6 | walk 一段 | 滑移 <3mm、离地 <35mm、转 90° 不抽搐 | G 参数 + 脚锁 |
| P7 | 基准集重解 | 族内 ≥90% | popsize / 三段比例 |
| P8 | 夜间批处理 | verified ≥90%，rejected 有归因 | `collision.json` |

---

## 12. 交付时的主观评分（不能自动化）

技术指标全绿但「她像提线木偶」是这个项目最可能的失败形态，而系统自己发现不了。所以每次阶段交付做一次 30 秒的盲评，写进 `logs/review.jsonl`（schema 见 LOGGING）。

看三个动作（挥手 / 走近 / 停顿），各打 1–5 分：

| 项 | 1 | 3 | 5 |
|---|---|---|---|
| 起播自然度 | 明显等一下才动 | 轻微延迟 | 说完就动 |
| 动作连接 | 一顿一顿 | 偶尔生硬 | 看不出接缝 |
| 二次运动 | 头发裙摆死板 | 有跟随但僵 | 自然甩 |
| 停顿状态 | 像雕像 | 有呼吸但机械 | 会换重心、歪头、小动作 |
| 整体 | 一看就是程序 | 像早期 3D | 像有人在那儿 |

**和 `health.jsonl` 并排看**：

- 指标绿 + 分低 → **指标选错了**（比改代码优先）
- 指标红 + 分高 → 阈值太严
- 都低 → 真问题

这条是唯一能发现「指标与目标脱节」的机制。
