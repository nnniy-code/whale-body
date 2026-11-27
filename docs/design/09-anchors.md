# 09 · 语义锚点（anchors）

> 对应 HANDOFF §12 第 9 份、§8.6
> 产出：`config/anchors.json` + `tools/calibrate_anchors.py`
> **这是批 1 的第一份，因为所有谓词都建立在它上面。**

---

## 1. 为什么需要锚点

身体有 209 根骨，但**语义位置不都在骨骼上**：

- 有骨：`head` / `chest` / `hand.L` / `foot.L` / `WhaleTail_04`
- **没有骨**：嘴、下巴、头顶、掌心、脚底、眼睛高度

谓词要问「手到嘴的距离」，就必须先有「嘴在哪」。`axis_map.json` 只有骨骼的静止朝向向量，**不含任何语义点**（实测 991 行，结构是 `rig / bones_total / facing / rest_dir`）。

所以锚点 = **骨骼 + 局部偏移**，是谓词的坐标底座。

---

## 2. 两种锚点

| 类型 | 表示 | 例子 | 用途 |
|---|---|---|---|
| **点锚点** | `{bone, offset}` | `mouth = {head, [0,-0.09,0.02]}` | 距离类谓词 |
| **方向锚点** | `{bone, axis, dist}` | `gaze = {head, "forward", 1.0}` | 朝向类谓词（look-at 目标） |

**不要混用**。上一版 `look_at` 写成 `{bone, axis, dist}` 塞在点锚点表里，schema 不齐。

---

## 3. `anchors.json` schema

```json
{
  "v": 1,
  "calibrated_at": "2026-11-23T23:40:00+08:00",
  "rig": "deepseek_rigged.blend",
  "fk_checksum": "a1b2c3",
  "points": {
    "mouth": {
      "bone": "head",
      "offset_m": [0.0, -0.090, 0.020],
      "source": "calibrated",
      "confidence": 0.9
    },
    "chin": { "bone": "head", "offset_m": [0.0, -0.060, 0.005], "source": "calibrated", "confidence": 0.85 }
  },
  "directions": {
    "gaze": { "bone": "head", "axis": "forward", "dist_m": 1.0 }
  }
}
```

| 字段 | 说明 |
|---|---|
| `fk_checksum` | 与 `boot` 事件的 FK 校验和一致；不一致说明换过骨架，锚点失效 |
| `source` | `calibrated`（实测） / `placeholder`（占位，禁止用于验收） |
| `confidence` | 0–1，人工确认时的把握度；<0.6 的不许写进谓词的硬判据 |

---

## 4. 锚点清单（本身体可用）

### 4.1 点锚点

| 锚点 | 父骨 | 含义 | 标定方向 | 用途 |
|---|---|---|---|---|
| `mouth` | `head` | 嘴（虚拟点） | 脸朝前 `-Y`，脸区下 1/3 | `hand_near_mouth`（D1 旗舰） |
| `chin` | `head` | 下巴 | 脸区最底部 | 头部姿态参考 |
| `head_top` | `head` | 头顶 | 沿 `+Z` 到 mesh 顶 | `hand_above_head` |
| `chest` | `chest` | 胸口中心 | 骨原点 | 手贴胸、距离 |
| `belly` | `spine` | 腹部 | 骨原点前移 | 弯腰幅度 |
| `hand_center` | `hand.L/R` | 掌心 | 骨段中点前移 | 抓握、触碰 |
| `foot_sole` | `foot.L/R` | 脚底最低点 | 沿 `-Z` 到 mesh 底 | 接触判定（P2） |
| `tail_tip` | `WhaleTail_04` | 尾尖 | 骨尾 | 尾巴表达力指标 |

**没有「眼睛」锚点**：0 根眼骨、0 形态键。眼睛相关的高度用 **face 材质顶点的 z 分位点**算，不引入新锚点（见 §5.3）。

### 4.2 方向锚点

| 锚点 | 父骨 | 含义 |
|---|---|---|
| `gaze` | `head` | 头部朝向射线（整头 look-at，没有眼球） |

---

## 5. 标定流程（脚本 + 人工确认，不是纯手动）

### 5.1 为什么不用「拖 Empty 目视贴」

不可复现、不可量化、mesh 一变要重来。上一版写的「10 分钟拖一次」在换皮后会全部作废。

### 5.2 脚本做什么

```
对每个锚点定义 (bone, dir, max_dist, expected_materials):
  1. origin = bone 世界位置（或 bone 段上参数 t 处）
  2. scene.ray_cast(depsgraph, origin, dir, distance=max_dist)
  3. 若未命中 → 翻倍距离重试一次（最多 4×max_dist）
  4. 命中后查 polygons[idx].material_index
     ├ 材质不在 expected_materials → 沿 dir 微偏移 ±(2,4,6...)mm 重打（最多 8 次）
     └ 都不行 → 该锚点标记 placeholder，报错
  5. 命中点 → 转成 bone 的局部坐标（rest 空间）
  6. 写候选 json，**不直接覆盖正式文件**
```

**为什么按材质过滤**：mesh 只有一个（`tripo_node_dca8b6a7`），头发（60 根 `Bangs_*` / `Hair_*`）的几何贴在脸前面。从 `head` 沿 -Y 打射线，**大概率命中刘海**——不滤材质，标出来的「嘴」是头发。

`expected_materials` 只接受 `Toon face 0 / Toon face 3 / Toon face 4`。

### 5.3 「嘴」的具体定位（不能用「眼睛高度」当边界）

上一版写的自检是「z 落在下巴与眼睛之间」——**循环依赖**：眼睛也没有锚点。

正确做法：从 face 材质区顶点的 z 分布直接算：

```
face_verts = 所有 material_index ∈ face 集合 的顶点（世界 z）
z_bottom = 5% 分位      # 下巴
z_top    = 95% 分位     # 额头
z_mouth  = z_bottom + 0.30 × (z_top - z_bottom)   # 脸区下 1/3
```

然后只在 `|z - z_mouth| < 8mm` 且 `|x - head.x| < 20mm` 的候选里选**最靠前（-Y 方向最远）**的那个点当 mouth。

### 5.4 自检判据（**不是「到表面 <2mm」——那条恒真**）

`tools/calibrate_anchors.py --check` 必须全过：

| ���据 | 阈值 | 为什么 |
|---|---|---|
| 命中多边形材质 ∈ 期望集合 | 必须 | 排除头发/衣服 |
| `|x| < 20mm`（中线） | 必须 | 否则标到脸颊/耳朵 |
| `z` 在 §5.3 算出的区间内 | ±8mm | 排除额头/下巴 |
| 左右对称锚点（如 `hand_center.L/R`） | \|Δ\| < 3mm | 抓左右标定不对称 |
| 重放校验：用 json 里的 offset 反算世界坐标 | 与标定时差 <0.5mm | 抓「写进了错的空间」 |
| `confidence ≥ 0.6` | 必须 | 占位符不许用于硬判据 |

### 5.5 人工确认（唯一一步不可省）

脚本给出候选点（世界坐标 + 所属材质 + 到骨骼距离），`blender_rt_see` 从正面看一眼，确认「这个点真在嘴上」。确认后 `confidence=0.9` 写入正式 json。

**人工只做确认，不做拖拽**——所以每次换皮重跑脚本，人只需看 8 个点。

---

## 6. 验收

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| `calibrate_anchors.py --check` | 6 条自检全过 | §5.3 的分位系数 |
| 手贴到 mouth 锚点 | `hand_near_mouth` 的 `d_mm < 5` | 锚点 offset |
| 换 blend 后 `fk_checksum` 变 | 脚本拒绝启动，重标 | — |
| 左右手对称点 | 误差 <3mm | 标定方向向量 |

---

## 7. 风险

1. **mesh 只有一张，头发挡脸是常态**——材质过滤必须有效，否则整份锚点全废。
2. **`head` 骨的 roll 方向**决定局部坐标轴朝向。标定脚本必须先打印 `head` 骨的 `matrix_local` 三轴，确认 `-Y` 真是脸朝向（当前实测：脸朝 `-Y`，见 HANDOFF §8.6 的射线结果）。
3. **锚点不随表情变**（没有表情），但要随头动——因为 offset 是**父骨局部坐标**，头一转自动跟随。
