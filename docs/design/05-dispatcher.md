# 05 · 调度（dispatcher）

> 对应 HANDOFF §12 第 5 份、§16.1
> 产出：`core/dispatcher.py`
> 一句话：**她反应快，是因为 90% 是查库，不是因为算得快。**

---

## 1. 一个播放器，四个层

| 层 | 触发 | 耗时 | 谁做 |
|---|---|---|---|
| **L0** | 每帧，永远 | 0.018 ms 算 + 1.4 ms 写/update | 本模块（主循环） |
| **L1** | 收到 act | 30 ms 查库 + 起播 | library |
| **L2** | L1 未命中 | 3000–10000 次求值，分片 | solver |
| **L3** | 夜间，离线 | 0.5–2 s/条×5 帧 | 独立进程 `l3_verify.py` |

**只有一个播放器。** 不允许 L0 播一套、L1 播一套——那会变成两套驱动抢骨头（HANDOFF §8.5.1）。

姿态每帧的合成：

```
pose = base_rest ⊕ (正在播的动作的 G,P) ⊕ emotion_bias
```

「正在播的动作」要么是库条目（L1），要么是兜底（固定 idle 动作）。**没有第二个播放器。**

---

## 2. 状态机

```
            ┌──────────────────────────────┐
            │            IDLE              │◄──────── 动作结束
            └──────────────┬───────────────┘
                           │ act 到达
                           ▼
                    ┌──────────────┐
              ┌─────│  L1_LOOKUP   │  30 ms
              │     └──────┬───────┘
      命中    │            │  未命中
              ▼            ▼
        ┌──────────┐  ┌─────────────────────┐
        │ PLAYING  │  │ PLAYING(fallback)   │
        │ (L1)     │  │  + SOLVING(L2)      │
        └────┬─────┘  └──────────┬──────────┘
             │                   │ 解出
             │                   ▼
             │            ┌──────────────┐
             │            │ PLAYING(L2)  │ 200 ms 混合切换
             │            └──────┬───────┘
             └──────────┬────────┘
                        ▼
                      结束 → IDLE
```

**`SOLVING` 失败**：保持兜底，记 `failed`，回传 `family_infeasible` 给 LLM。**不要停在半成品上。**

---

## 3. 主循环里的顺序（**L0 必须先行**）

```python
def tick():
    t0 = perf_counter()

    # ① L0：永远先跑，不可跳过
    pose = build_pose(base_rest, current_action, emotion.now(), phase=player.phase(t0))

    # ② 渲染帧才写骨（不是每个 tick 都写）
    if frame_changed():
        binder.write(pose)
        view_layer.update()
        redraw()

    # ③ 剩余预算给 L2
    if state == "SOLVING":
        budget = clamp(FRAME_MS - (perf_counter()-t0)*1000 - REDRAW_EST, 4, 12)
        solver.step(budget_ms=budget)
        if solver.done:
            _on_solved(solver.best)
```

**为什么 L0 先行**：如果先跑 L2，183 ms 连续计算会让「呼吸」在整段求解期间停摆——她在那半秒里是死的。L0 先行保证**无论求解多慢，她一直在呼吸**。

**`FRAME_MS` = 33.3**（fps 30）。不是 16.7。

---

## 4. 兜底（两级，L2 启动前就选好）

```python
def pick_fallback(intent_key) -> (Entry | None, int):
    # 一级：库最近邻（宽松阈值）
    e = library.best(intent_key, threshold=0.45)
    if e: return e, 1
    # 二级：通用 idle + 情绪偏置
    return None, 2
```

| 级 | 内容 | 何时用 |
|---|---|---|
| 1 | 库中距离最近的条目（阈值放宽到 0.45） | 有「大致相关」的动作 |
| 2 | 通用 idle（呼吸 + 情绪偏置 + 头部朝向） | 完全无关 |

**为什么阈值放宽**：兜底只需要「不僵住」，不需要「对」。放宽到 0.45 能捡到很多「有点像」的动作。

**兜底必须在 L2 启动前选好**，不能等 L2 跑完才想「这期间播什么」。

---

## 5. 打断策略（HANDOFF §16.1）

**动作执行到一半，用户又输入 → 默认不接受，回一句「我在做 XX，等一下」。**

```python
def on_act(act):
    # 周期动作（walk/run）：随时可以接续，不拒绝，但要混合
    if state == "PLAYING" and player.loop:
        _accept(act, blend_ms=200)
        return

    if state == "PLAYING" or state == "SOLVING":
        remaining = player.remaining_s()
        if remaining > GRACE_S:            # GRACE_S = 0.6（经验）
            log.intent_deferred(act.intent, current.id, remaining)
            queue.append(act)              # 进队列，不丢
            tts_say(f"我在做{current.label}，等一下")
            return
        # 剩余很短，让它自然结束
    _accept(act)
```

**干扰项**：`walk` 是 `loop=True`，`remaining_s()` 返回 0（见 17-player）。如果不特判，走路时用户说的每一句话都会被拒绝——那是灾难。

**为什么周期动作可以随时接续**：它的"结束"不是一次性事件，是下一个周期边界。接续只需要 200 ms 混合，不需要等它走完「这一次」。单次动作（挥手）才有"做到一半不能停"的问题。

三条都要满足（HANDOFF 原话）：

1. **不是静默吞掉**——用户会以为她死了
2. **不是立刻打断**——动作会崩
3. **进队列，动作结束后再处理**

`GRACE_S = 0.6`：如果只剩不到 0.6 秒，直接让它结束再接新的，不打断也不回话。`[经验]`

---

## 6. 多动作叠加：**当前不支持**（已知限制）

「一边走过来一边挥手」是两个动作同时。**当前只有一个播放器，做不到。**

现状：

- 收到第二个 act 时，按 §5 打断策略处理（排队或拒绝）
- 不会混合两个动作

这是 HANDOFF §16 的待定项 1。**写代码时不要偷偷用「两个 pose 加权平均」糊过去**——那会同时破坏脚锁和末端目标（平均后的姿态既不满足 A 也不满足 B）。

如果要做，正确形状是：

- 一个动作占「下半身」，一个占「上半身」，按骨链分组
- 冲突的骨链（比如两个都要动左臂）必须显式拒绝

但这需要「动作 → 占哪些骨链」的标注，当前 `library` 里没有这个字段。**先不做，别半做。**

---

## 7. 时序（一次完整对话）

```
T+0     用户说完
T+?     LLM interpret（0.5–2 s，外部）
T       act 到达
T+0     say → TTS（不等动作）
T+30    L1 查库完成
T+5     （未命中时）兜底起播 —— 注意比 L1 完成还早
T+80    （命中时）目标动作起播
T+~600  （未命中时）L2 解出，200 ms 混合替换兜底
```

**「动作不晚于语音 1 句」的验收靠的是 T+5 的兜底**，不是 L2。L2 永远来不及。

---

## 8. 验收（P5）

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 说一句话，测起播延迟 | ≤200 ms | 兜底起播时机 |
| 未命中时，L2 期间她是否还在动 | 一直在动（兜底） | `pick_fallback` |
| 动作播放中再输入 | 回话 + 排队 + `intent_deferred` 日志 | `GRACE_S` |
| L2 失败 | 保持兜底，不播半成品 | `_on_solved` |
| 连续 10 次交互 | 无动作叠加（一个接一个） | 状态机 |
| 求解期间 `big_steps` | 0 | L0 先行 + 混合 |

---

## 9. 风险

1. **`GRACE_S` 太大会让她频繁拒绝对话**，太小则动作总被打断。看 `intent_deferred` 占比调。
2. **兜底一级（最近邻）可能和意图完全不搭**——阈值 0.45 有时会捡到「正在走路」来兜「想跳舞」。如果观感差，把一级阈值降到 0.35，宁可泛 idle。
3. **状态机没有「暂停」态**：现在只有 IDLE/LOOKUP/PLAYING/SOLVING。如果以后要「她停下来听你说」，要加 PAUSED，且 L0 继续跑。现在没这个需求，不加。
