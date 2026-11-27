# 身体 v2 · 日志规范

> 配套：[HANDOFF.md](../HANDOFF.md)、[WORKFLOW.md](WORKFLOW.md)
> 目的有两个：① **验收时有据可查**（每个验收判据都能对到一条日志）② **交接时新人能在 10 分钟内看出系统卡在哪**。
> 日志不是调试输出，是**资产**——它决定下一个对话能不能查清"她为什么那天不动了"。

---

## 0. 五条原则

1. **append-only**。只追加，不改写。要改就写新事件（`lib_update`），靠 `seq` 重放。
2. **每条事件自带完整语义**。不许出现"结合上文可知"。交接时只给一堆日志，别人得能看懂。
3. **每次"她想动"分配一个 `trace_id`**，从 `intent` 到 `verify` 全程贯穿。这是唯一的证据链。
4. **数值必须带单位**。`depth_mm`、`dur_s`、`ms`、`deg`。裸数字等于没记。
5. **失败必须带"为什么"**。`{"result":"fail"}` 是废日志；`{"result":"fail","reason":"foot_airborne","detail":{...}}` 才是资产。

---

## 1. 文件布局

```
logs/
  events.jsonl      ← 主事件流（意图/求解/判决/播放/异常）
  library.jsonl     ← 库的状态变更（可重放出整个库）
  health.jsonl      ← 健康指标（每 5 s 一条，用于看趋势）
  errors.jsonl      ← 错误单独一份（便于告警与统计）
  review.jsonl      ← 交付时的主观评分（人工，1–5 分）
```

**为什么分开**：`events` 会很长，`library` 需要重放，`health` 是采样的趋势数据，`errors` 要能快速 count。混在一起查询会互相拖慢。

**轮转**：单文件 > 50 MB 就 `events.2026-11-23.jsonl` 改名归档，不删。`health` 保留 7 天。

---

## 2. 公共字段

每条事件都有：

```json
{
  "v": 1,
  "ts": "2026-11-23T23:30:01.234+08:00",
  "seq": 10422,
  "type": "solve_end",
  "trace_id": "t-20261123-233001-0007",
  "pid": 32900
}
```

| 字段 | 说明 |
|---|---|
| `v` | schema 版本。字段增删时 +1，老日志仍可读 |
| `ts` | ISO 8601 带时区（**不要写 Unix 浮点数**，交接时看不了） |
| `seq` | 进程内单调递增。用于排序、去重、发现丢日志 |
| `trace_id` | 一次"她想动"的全程 ID。L0 的纯底噪事件没有，填 `null` |
| `pid` | Blender 进程号。发现"有两个进程抢着写"时靠它 |

---

## 3. 事件类型与 schema

### 3.1 `boot`

```json
{"type":"boot","blend":"deepseek_rigged.blend","bones":209,"fps":30,
 "fk_checksum":"a1b2c3","config":{"bounds":"ok","anchors":"ok","capsules":"ok",
 "collision":"ok","emotion_bias":"ok","weights":"ok"}}
```

`fk_checksum` = 21 主骨的 rest 矩阵哈希。**换骨架会变**，用来发现"blend 被偷偷换过"。

### 3.2 `golden`（FK 校验）

```json
{"type":"golden","pass":true,"cases":10,"max_err_mm":0.0008,
 "asserts":{"ik_influence_zero":true,"armature_identity":true,
            "all_scale_one":true,"unconnected_set_ok":true}}
```

**每次启动跑一次；每次改骨架/加约束后手动跑。** 失败 → 进只读模式（见 WORKFLOW §10）。

### 3.3 `intent`（LLM 输出）

```json
{"type":"intent","raw_intent":"拍拍头","mood":{"val":72,"aro":40,"close":80,"guard":20},
 "reuse":false,"dur_s":1.2,
 "predicates":[{"name":"hand_near_head","args":{"side":"right"},"tol_mm":40},
               {"name":"head_tilt","args":{"deg":12}}],
 "family":"pat_head"}
```

`dur_s` 缺失记为 `null`，由系统按族默认补。

### 3.4 `retrieve`（L1 查库）

```json
{"type":"retrieve","hit":true,"entry_id":"lib-0042","status":"verified",
 "score":0.87,"candidates":5,"ms":4.1}
```

未命中：`{"hit":false,"ms":3.2,"reason":"no_candidate_above_0.25"}`。

### 3.5 `solve_start` / `solve_end`（L2）

```json
{"type":"solve_start","family":"pat_head","budget_evals":10000,
 "P_default":[...10 个数],"G_default":[...11 个数]}
```

```json
{"type":"solve_end","result":"converged","evals":6210,
 "calc_ms":118.4,"wall_ms":412.0,"ticks":13,
 "gate":{"pass":3,"total":3,"violations":[]},
 "params":{"P":[...],"G":[...]},
 "max_deg_per_frame":8.2}
```

失败：

```json
{"type":"solve_end","result":"family_infeasible","evals":10000,
 "calc_ms":301.0,"wall_ms":980.0,
 "unsatisfied":["world_airborne","torso_inverted"],
 "reason":"trajectory_base_cannot_represent"}
```

**`calc_ms` 与 `wall_ms` 必须分开记。** 前者是纯计算，后者含分片与帧等待。混在一起会得出"L2 很快"的错误结论（v1.2 就犯过）。

### 3.6 `gate`（判决）

```json
{"type":"gate","verdict":"FAIL","pass":2,"total":3,
 "violations":[{"predicate":"foot_airborne","err":0.42,"err_unit":"normalized",
                "detail":{"left_mm":64.5,"right_mm":99.0,"tol_mm":35}}],
 "max_deg_per_frame":29.44,"max_deg_budget":12}
```

**`verdict` 与 `pass/total` 必须一致。** 上一代出现过 `mode:"pass"` + `ok:true` + `verdict:"FAIL"` 同时存在的情况（缺陷 #7）——任一字段矛盾，验收直接判失败。

### 3.7 `play`

```json
{"type":"play","entry_id":"lib-0042","source":"L1","fallback_used":false,
 "blend_ms":0,"start_latency_ms":78}
```

兜底播放：`{"source":"fallback","fallback_level":1,"entry_id":"lib-0031","blend_ms":200}`。

替换兜底：`{"source":"L2","prev":"fallback","blend_ms":200}`。

### 3.8 `emotion`

只在**低通后的值变化超过 2%** 时记一条（否则 30 fps 会刷爆）。

```json
{"type":"emotion","axes":{"val":71.2,"aro":40.5,"close":80.1,"guard":19.8},
 "label":"开心","delta":{"val":12.4},"bias_applied":{"lean_deg":1.8,"period_s":-0.03}}
```

### 3.9 `library`（库变更，写进 `library.jsonl`）

```json
{"type":"lib_put","entry_id":"lib-0042","status":"provisional",
 "family":"pat_head","parent":"lib-0011",
 "intent_key":"pat_head|right_hand|head",
 "params":{"P":[...],"G":[...]},
 "metrics":{"max_deg_per_frame":8.2,"evals":6210}}
```

```json
{"type":"lib_status","entry_id":"lib-0042","from":"provisional","to":"rejected",
 "violations":[{"frame":14,"bone":"WhaleTail_03","region":"leg","depth_mm":9.1}],
 "verify_ms":1420}
```

**`library.jsonl` 能重放出整个库**：从头按 `seq` 重放 `lib_put` + `lib_status`，必须等于当前库。这是一条硬性自检（见 §6）。

### 3.10 `health`（写进 `health.jsonl`）

每 5 秒一条，采样窗口内统计：

```json
{"type":"health","window_s":5,"ticks":150,
 "tick_ms":{"p50":2.1,"p95":3.4,"max":12.0},
 "gap_ms":{"p50":31.0,"p95":34.2},
 "big_steps":0,"spring_deg":0.16,"l2_active":false,"solver_budget_ms":12}
```

字段对齐旧 `body_health.json` 的名字（`tick_ms` / `gap_ms` / `big_steps` / `spring_deg`），这样 A/B 对照能直接比。

### 3.11 `error` / `stall`

```json
{"type":"error","code":"fk_mismatch","detail":"max_err 0.34mm > 0.01mm","fatal":true}
{"type":"stall","ms":214,"during":"L2_slice","action":"budget_shrunk"}
```

`stall` 仅在**非 L2** 的卡顿记（L2 分片本来就占预算），连续 3 次 → 记 `error`。

### 3.12 `review`（写进 `review.jsonl`，人工）

```json
{"type":"review","stage":"P6","scores":{"start":4,"blend":3,"secondary":5,"idle":2,"overall":3},
 "note":"停顿像雕像，呼吸幅度太均匀","health_window":"2026-11-23T22:00–23:00"}
```

`health_window` 指向同期 `health.jsonl` 的时间范围。评估时**必须把两者并排看**：指标绿 + 分低 = 指标选错了（HANDOFF §15）。

---

## 4. 采样策略（别把磁盘写满）

| 事件 | 频率 | 处理 |
|---|---|---|
| `boot` / `golden` | 每次启动 | 全记 |
| `intent` / `retrieve` / `solve_end` / `gate` / `play` | 每次动作 | 全记（量级：每天几千条） |
| `emotion` | 变化 >2% 才记 | 限流 |
| `health` | 5 s | 全记（每天 17280 条） |
| `tick` | 30/s | **不落盘**，只在内存环形缓冲；异常时把最近 60 条 dump 到 `errors.jsonl` 的 `snapshot` 字段 |
| `error` | 按需 | 全记，同 code 每分钟最多 10 条（防止刷屏） |

写入用 **buffered append**，每 20 条或每 1 s flush 一次。**不要在 L0 路径里同步写盘。**

---

## 5. 查询示例（验收时直接跑）

设 `E=logs/events.jsonl`，`L=logs/library.jsonl`。

**① 有没有瞬移（P0）**

```bash
jq -r 'select(.type=="gate" and .max_deg_per_frame>12) | "\(.ts) \(.max_deg_per_frame)"' $E | wc -l
```

预期：`0`。

**② L2 的真实开销（P7）**

```bash
jq -r 'select(.type=="solve_end") | [.calc_ms,.wall_ms,.ticks,.evals] | @tsv' $E
```

预期：`calc_ms` 55–330，`wall_ms` < 1000，且 `wall_ms > calc_ms`（分片有帧等待——如果相等，说明分片没生效）。

**③ 求解成功率（P7）**

```bash
jq -r 'select(.type=="solve_end") | .result' $E | sort | uniq -c
```

预期：`converged` 占比 ≥ 90%（族内基准集）。

**④ 库的 verified 率（P8）**

```bash
jq -r 'select(.type=="lib_put" or .type=="lib_status") | .entry_id' $L | sort | uniq -c | sort -rn | head
jq -r 'select(.type=="lib_status") | "\(.entry_id) \(.to)"' $L | awk '{print $2}' | sort | uniq -c
```

**⑤ 动作起播延迟（P5）**

```bash
jq -r 'select(.type=="play") | .start_latency_ms' $E | sort -n | tail -1
```

预期：`≤ 200`。

**⑥ 情绪有没有推漂目标点（P1.5）**

```bash
jq -r 'select(.type=="gate") | .violations[]? | select(.predicate|startswith("hand_near")) | .detail.d_mm' $E
```

预期：同一意图在两种情绪下差值 **< 1 mm**。

**⑦ 按 trace 串起一次动作（交接时最有用）**

```bash
jq -r 'select(.trace_id=="t-20261123-233001-0007") | "\(.seq) \(.type) \(.result // .verdict // .status // "")"' $E
```

输出应该是一条完整链：`intent → retrieve → solve_start → solve_end → gate → play`。

---

## 6. 三条硬性自检

写进 `selftest_log*`：

1. **`verdict` 与 `pass/total` 一致**——任一事件里 `verdict=="PASS"` 但 `pass<total`，或反之，判失败。（上一代缺陷 #7 的复发检测）
2. **`library.jsonl` 重放结果 == 当前库**——从 `lib_put` + `lib_status` 重放，逐字段比对。
3. **每个 `trace_id` 的事件链完整**——有 `intent` 就必须有 `play` 或 `solve_end`（失败也要有），否则说明链路断了。

---

## 7. 交接时的读法（给下一个对话）

按顺序看，10 分钟能定位问题：

1. `tail -1 logs/errors.jsonl` —— 有没有在报错
2. `tail -1 logs/health.jsonl` —— `tick_ms` 和 `gap_ms` 是否正常（对照旧基线 2.2 / 24.6）
3. `grep '"type":"golden"' logs/events.jsonl | tail -1` —— FK 还准不准
4. `jq -r 'select(.type=="solve_end").result' logs/events.jsonl | sort | uniq -c` —— 求解是不是大面积失败
5. 取最近一条失败的 `trace_id`，用 §5 的 ⑦ 串出来看断在哪

**判断"她活着还是僵着"看三个数**：`big_steps`（瞬移）、`spring_deg`（二次运动有没有动）、`play.start_latency_ms`（反应快不快）。

---

## 8. 验收指标 → 日志字段 映射表

| 阶段 | 验收判据 | 看哪条日志 | 字段 |
|---|---|---|---|
| P0 | `big_steps` 16 → 0 | `health` | `big_steps` |
| P0 | 速度曲线无 0 速平台 | 需 Graph Editor 人看 | — |
| P0.5 | FK <0.01mm | `golden` | `max_err_mm` |
| P1 | 快照完整 | 无日志（单测） | — |
| P1.5 | 情绪 0 延迟 | `emotion` | `delta` / `bias_applied` |
| P1.5 | 不推漂 targets | `gate` | `violations` 里 hand 类谓词 |
| P2 | 接触判定 100% | 单测 | — |
| P3 | 5 种错全抓到 | `gate` | `violations` |
| P4 | 30ms 出参数 | `retrieve` | `ms` |
| P5 | 不晚于语音 1 句 | `play` | `start_latency_ms` |
| P6 | 滑移 <3mm / 离地 <35mm | `gate` | 对应 `detail` |
| P7 | 族内 ≥90% | `solve_end` | `result` 分布 |
| P7 | calc/wall 分离 | `solve_end` | `calc_ms` vs `wall_ms` |
| P8 | verified ≥90% | `library` | `lib_status.to` 分布 |
| P8 | rejected 带归因 | `lib_status` | `violations[].{frame,bone,depth_mm}` |
| 交付 | 观感 | `review` | `scores.overall`（**必须和 `health` 并排看**） |
