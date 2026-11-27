# 13 · 精确碰撞与归因（collision）

> 对应 HANDOFF §12 第 13 份、§7 P8
> 产出：`config/collision.json` + `core/collision.py` + `tools/l3_verify.py`
> 用途：**L3 夜间入库闸门**。不做整流形，只做「过 / 不过 + 违规在哪」。

---

## 1. 为什么不是「视觉」

真实穿模是**几何问题**：BVH 三角面求交，纯计算（`mathutils.bvhtree`，331 万顶点级网格 0.5–2 s）。

「视觉伺服」在这里没有目标特征 `f*`——我们的目标不是「照这个视频做」，没有参考影像。所以 `vision/servoing.py` 已否决（HANDOFF §8.5.1）。**渲染只出证据**（静帧、A/B、给人看），不参与判定。

---

## 2. 流程

```
l3_verify.py（独立 blender -b，加载同一 blend）
  │
  ├ 对每条 status=provisional 的库条目：
  │   1. 抽 5 帧（首 / 1/4 / 1/2 / 3/4 / 末）
  │   2. 每帧：FK 出全姿态 → 建 BVH → overlap() 取相交三角形对
  │   3. 按 REGION 对查阈值 → 判违规 / 合法接触
  │   4. 有违规 → 归因（哪根骨、哪一帧、多少 mm）
  │   5. 罚项回灌 → 重搜（≤5 轮）
  │   6. 过 → verified；不过 → rejected
  └
```

**不进实时链路。** 一条 0.5–2 s，5 帧就是 2.5–10 s/条，只适合夜间/手动。

---

## 3. REGION 对阈值表（`collision.json`）

8 个 REGION：`arm / body / hair / head / leg / ribbon / skirt / tail`。
两两组合 **28 对** + 一条默认值。

```json
{
  "v": 1,
  "regions": ["arm","body","hair","head","leg","ribbon","skirt","tail"],
  "pairs": {
    "arm|body":    { "allowed_mm": 5 },
    "arm|head":    { "allowed_mm": 0 },
    "arm|hair":    { "allowed_mm": 0 },
    "arm|leg":     { "allowed_mm": 2 },
    "arm|skirt":   { "allowed_mm": 2 },
    "arm|tail":    { "allowed_mm": 0 },
    "body|head":   { "allowed_mm": 0 },
    "body|hair":   { "allowed_mm": 3 },
    "body|skirt":  { "allowed_mm": 8 },
    "body|tail":   { "allowed_mm": 4 },
    "body|ribbon": { "allowed_mm": 2 },
    "head|hair":   { "allowed_mm": 3, "mode": "delta" },
    "head|skirt":  { "allowed_mm": 0 },
    "head|tail":   { "allowed_mm": 0 },
    "leg|skirt":   { "allowed_mm": 8 },
    "leg|leg":     { "allowed_mm": 2 },
    "leg|tail":    { "allowed_mm": 0 },
    "skirt|tail":  { "allowed_mm": 0 },
    "hair|skirt":  { "allowed_mm": 0 }
  },
  "default": { "allowed_mm": 2 }
}
```

**没列出的对用 `default`**——不要假设「没写就是 0」，否则一个漏配的就会一直误报。

上表列了 **19 个高频对**；其余 9 对（`arm|ribbon`、`body|leg`、`hair|leg`、`hair|ribbon`、`hair|tail`、`head|ribbon`、`leg|ribbon`、`ribbon|skirt`、`ribbon|tail`）走 `default: 2mm`。**如果实测发现哪对误报多，再单独提到表里。**

### 3.1 `head|hair` 必须用增量（`mode: delta`）

头发**本来就贴在头皮上**，静止姿态下两者的绝对穿插深度必然 > 0。用绝对深度判会大面积误报。

增量定义：

```
delta = depth(当前帧) - depth(静止姿态)
违规条件：delta > allowed_mm
```

静止姿态的基线由 `gen_capsules.py` 或 `l3_verify.py --baseline` 一次性算好，存进 `collision.json` 的 `baseline` 字段。

### 3.2 合法接触 vs 违规

「相交」不等于「违规」。**手贴胸**是 `arm|body` 的浅接触（≤5 mm），合法；**手臂戳穿躯干**是深的，违规。

所以判定是：`depth - allowed_mm > 0` 才算违规。**不是「有相交就算」**。

---

## 4. 归因（没有这步罚项就是噪声）

`overlap()` 返回的是三角形对 `(tri_a, tri_b)`。直接把它当罚项，CMA-ES 只知道「这组不好」，不知道改哪。

归因三步：

```python
def attribute(tri_pairs, frame_idx):
    out = []
    for a, b in tri_pairs:
        bone_a = dominant_bone(a)     # 三角形 3 顶点里权重最大的 VG 对应的骨
        bone_b = dominant_bone(b)
        depth  = penetration_depth(a, b)
        region_a, region_b = region_of(bone_a), region_of(bone_b)
        allowed = lookup(region_a, region_b)
        if depth - allowed > 0:
            out.append({
                "frame": frame_idx,
                "bone": bone_a,        # 归因给「动得多的那根」
                "other": bone_b,
                "regions": [region_a, region_b],
                "depth_mm": depth,
                "allowed_mm": allowed,
            })
    return out
```

**归因给哪根骨**：取「该帧位移更大」的那根。因为要动的是它，不是被撞的那个。

```python
bone = bone_a if move_amount(bone_a, frame) > move_amount(bone_b, frame) else bone_b
```

---

## 5. 罚项回灌（不是重写目标函数）

```
round 1：BVH 发现 WhaleTail_03 × leftUpperLeg 深 9 mm（允许 0）
        → 把 WhaleTail_03 的胶囊半径 += 9 mm（临时，仅本轮）
        → 重搜（预算缩到 3000 次）
round 2：再 BVH
```

**只加粗被归因的那根骨**，不整体加粗——整体加粗会无谓压缩解空间，导致「为了避一个 9 mm 的插，把整条尾巴收起来」。

最多 5 轮。5 轮还不过 → `rejected`。

---

## 6. 抽帧策略

**不要全帧跑**（30 帧 × 1 s = 30 s/条）。抽 5 帧：`0, N/4, N/2, 3N/4, N-1`。

但穿模可能发生在中间帧。所以：

```
if 任一抽中帧违规:
    在它 ±2 帧内补采（最多再 4 帧）
    递归一次，不递归第二次
```

最坏 9 帧 ≈ 9 s/条，可接受。

---

## 7. 验收（P8）

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 10 族 provisional → 跑一夜 | verified ≥ 90% | 阈值表 |
| 故意造一条「尾巴穿大腿」 | 100% 判 rejected | `leg|tail` |
| 故意造「手贴胸 3 mm」 | **不判违规** | `arm|body` = 5 |
| 故意造「手插进脸」 | 判违规 | `arm|head` = 0 |
| 每条 rejected | 带 `{frame, bone, regions, depth_mm}` | `attribute` |
| `head|hair` 静止姿态 | `delta` ≈ 0，不报 | `baseline` |
| 换 blend 后 `baseline` 未重算 | 首次跑必须报错，不许静默 | `fk_checksum` + mesh 哈希 |

第 3 条是「假阳性」的关键：**合法接触不能报**。假阳性太多会把好动作全判死，比漏报更伤（用户看到「她做什么都说不行」）。

---

## 8. 风险

1. **`overlap()` 在单 mesh 自相交上的行为**：BVH 是对整个 mesh 建的，`overlap()` 会返回网格与自身的相交——需要先 `bvh.overlap(bvh)`，再按三角形索引去重（`(a,b)` 和 `(b,a)` 是同一对）。**这一步容易写成 O(n²)**，实测时先量。
2. **归因的 `dominant_bone` 在关节处会错**：一个三角形三个顶点分属两根骨时，取最大权重可能归到静态骨。建议取「该帧位移更大」的那根（见 §4）。
3. **阈值全是 `[经验]`**。第 3、4 条验收就是校准手段，不要跳过。
