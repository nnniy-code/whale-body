# 17 · 播放器契约（player）

> 对应 HANDOFF §12 第 17 份、D10、§8.5.1
> 产出：`core/player.py`
> 一句话：**player 管「播到哪了」，不管「这一帧长什么样」。**

---

## 1. 为什么需要它

D10 要求「每帧从 `base_rest` 重建」，但动作有**时间**——挥手挥到一半、走路走到第 3 个周期。这些必须有人记。

player 记的就是这个：`phase` / `elapsed` / 是否暂停 / 播放速率。

**它不产生姿态。** 姿态由 `build_pose(base_rest, entry.params, phase, …)` 算。

这个分工必须守住，否则又会回到「逐帧写绝对姿态」的老路。

---

## 2. 状态

```python
@dataclass
class PlayerState:
    entry: Entry | None      # 正在播的库条目（None = idle/兜底）
    started_at: float        # perf_counter 起点
    paused_at: float | None  # 暂停时刻
    rate: float              # 播放速率（0.5 = 半速）
    dur_s: float             # 总时长（周期动作为「持续时长」）
    loop: bool               # 周期动作 True
    period_s: float | None   # 周期动作的 G.period
```

---

## 3. 关键量

```python
def elapsed(self, now) -> float:
    if self.paused_at is not None:
        base = self.paused_at - self.started_at
    else:
        base = now - self.started_at
    return base * self.rate

def phase(self, now) -> float:
    e = self.elapsed(now)
    if self.loop and self.period_s:
        return (e / self.period_s) % 1.0     # 周期动作：0..1 循环
    return min(1.0, e / self.dur_s) if self.dur_s > 0 else 0.0

def remaining_s(self, now) -> float:
    """单次动作的剩余时长。**周期动作返回 0**——它随时可以接续，不算「正在执行」。"""
    if self.loop:
        return 0.0                          # ← 关键：不是 inf
    return max(0.0, self.dur_s / self.rate - self.elapsed(now) / self.rate)

def finished(self, now) -> bool:
    if self.loop: return False
    return self.elapsed(now) >= self.dur_s
```

**`loop=True` 的动作不自动结束**——`walk` 一直走，直到有新动作替换它。这是 dispatcher 的职责（收到新 act 时替换）。

---

## 4. 接口

```python
def play(entry, now, rate=1.0) -> None        # 起播（替换当前）
def pause(now) / resume(now) -> None
def set_rate(r) -> None                       # 变速，不改 started_at
def stop(now) -> None                         # 回到 idle
def on_finished(callback) -> None             # 单次动作结束时回调
```

**`play` 是替换语义，不是叠加。** 多动作叠加当前不支持（05-dispatcher §6）。

---

## 5. 与 D10 的关系（最容易搞错的地方）

| 是 | 不是 |
|---|---|
| `phase` 是**状态**，跨帧保留 | `pose` 不是状态，每帧重算 |
| `elapsed` 累加 | `pose[t] = pose[t-1] + delta` ❌ |

正确写法：

```python
# 每帧
ph = player.phase(now)                       # 状态
pose = build_pose(base_rest, entry.params, ph, emotion(now))   # 无状态重建
```

错误写法：

```python
# 每帧
pose = pose + delta                         # 累加 → 240 拍后偏 386mm
```

**验收判据**：固定 `entry.params` 和固定的几个 `phase` 值，连续调用 `build_pose` 1000 次，每次输出必须**逐位相同**（没有任何历史依赖）。

---

## 6. 结束处理

单次动作结束 → 调 `on_finished` → dispatcher 回 idle（或播队列里的下一个）。

**不要**在 `finished()` 返回 True 的那个 tick 里立刻切换——那一帧的姿态是「最后一帧」，下一帧才是 idle。中间要过 200 ms 混合（05-dispatcher）。

---

## 7. 验收

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 播一个 1.2 s 的动作 | 1.2 s 后 `finished()` 为 True | `dur_s` |
| 周期动作 | 不自动结束；`phase` 在 0..1 循环 | `loop` / `period_s` |
| `rate=2.0` | 一半时间播完 | `set_rate` |
| pause 后 `phase` 不变 | 达 | `paused_at` |
| 同一 params 同一 phase 调 1000 次 | 逐位相同 | `build_pose` 有无状态 |
| 连续 `play` 两次 | 第二次替换第一次（不叠加） | `play` 语义 |

---

## 8. 风险

1. **`perf_counter` 在 Blender 里的精度**：够用（微秒级），但**不要用 `time.time()`**（系统时钟跳变会导致 phase 倒退）。
2. **暂停后恢复**：`paused_at` 必须记，否则 `elapsed` 会把暂停时间算进去。
3. **rate 变化时 `elapsed` 不连续**：`set_rate` 只改之后的速度，已过去的部分不变成新 rate。如果发现「变速后动作跳了一下」，说明实现把 `started_at` 重算了。
