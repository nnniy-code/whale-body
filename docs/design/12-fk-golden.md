# 12 · FK golden test

> 对应 HANDOFF §12 第 12 份、P0.5
> 产出：`tests/test_fk_golden.py`
> 为什么必须有：D9（纯 FK 求值）是整个 L2 的地基。**FK 和 Blender 差 1 mm，后面所有「预测通过、实际歪了」都查不出来。**

---

## 1. 被测对象

```python
# core/fk.py
def compute(pose_basis: dict[str, Matrix]) -> dict[str, Matrix]:
    """返回 209 骨在 armature 空间的 pose matrix。纯 Python，不调 bpy。"""
```

对照真值：Blender 的 `pose_bones[name].matrix`（在 `view_layer.update()` 之后）。

---

## 2. 公式（必须逐字复现 Blender）

```
对骨骼 b（拓扑序）：
  if b.parent is None:      M = rest[b] @ basis[b]
  else:                     M = M[b.parent] @ (rest[p]⁻¹ @ rest[b]) @ basis[b]

其中：
  rest[b]  = b.matrix_local          （armature 空间，rest pose）
  basis[b] = pose_bone.matrix_basis  （含 loc/rot/scale）

若 b.use_connect == True：
  basis[b] 的 translation 必须清零（Blender 对 connected 骨忽略 location）
```

`(rest[p]⁻¹ @ rest[b])` 可以预计算成 `prelocal[b]`，每帧只做 2 次矩阵乘。

---

## 3. 测试用例（10 组）

| # | 生成方式 | 覆盖什么 |
|---|---|---|
| 1 | 全零 basis（rest pose） | 基线 |
| 2 | 每骨随机旋转 ±30° | 常规 |
| 3 | 每骨随机旋转 ±90° | 大角度（四元数分支） |
| 4 | 只有 21 主骨旋转，二次骨零 | 求解循环的真实输入 |
| 5 | **hips 的 location 非零**（±50 mm） | 位置通道 |
| 6 | **spine / neck / head 的 location 非零** | connected 骨必须忽略 location 的验证 |
| 7 | **左右手 location + 旋转** | 末端 |
| 8 | 全部骨 scale = 1（显式设） | 断言前提 |
| 9 | 手指全弯 | 40 根末端链 |
| 10 | 随机 + 固定种子（可复现） | 回归 |

**第 5、6 组是必须的**：只测旋转的 golden test 挡不住「有人把 basis 简化成 `to_quaternion()`」这类 bug——那会静默丢掉 hips 的 26.9 mm 平移。

---

## 4. 断言清单

| # | 断言 | 阈值 | 失败说明 |
|---|---|---|---|
| 1 | 每骨世界位置误差 | **< 0.01 mm** | FK 公式错 |
| 2 | 每骨旋转误差（四元数夹角） | **< 0.01°** | 同上 |
| 3 | 4 条 IK 约束 `influence == 0.0` | 必须 | 若有人打开，FK 与真实不一致 |
| 4 | `armature.matrix_world` 单位矩阵 | 必须 | 否则世界坐标要换算 |
| 5 | 所有骨 `pose_bone.scale == (1,1,1)` | 必须 | 缩放会让四元数 FK 漏 |
| 6 | `use_connect == True` 的骨数 | **152**（实测 2026-11-24） | 骨架被改过 |
| 7 | unconnected 集合 | **57 根**（209−152），构成见 §4.1 | 骨架被改过 |
| 8 | 姿态重建：用 FK 输出写回 bpy，再读 `pose_bones.matrix` | 差异 <0.01 mm | 端到端闭环 |
| 9 | **connected 骨的 location 被忽略** | 对任意 connected 骨设 location，FK 与 Blender 一致 <0.01 mm | 规则没实现 |

**第 8 条是最高价值的一条**：它证明「FK 算的」和「Blender 显示的」是同一条路，而不只是公式对。

**第 9 条是 2026-11-24 补的**：设计初稿以为「这具身体全 False」，实测 152 根 connected。不测这条，「connected 骨 location 清零」这条规则写错了也发现不了。

#### 4.1 骨架实测（取代设计初稿的猜测）

| 项 | 实测 | 说明 |
|---|---|---|
| `use_connect == True` | **152 根** | 初稿写「全 False」是错的 |
| unconnected 构成 | 主链 9（hips / spine / neck / head / 双肩 / 双大腿 / 双 toes）+ 手指根 10 + hair 根 12 + bangs 根 5 + skirt 根 16 + ribbon 根 2 + `WhaleTail_01` + `Toe_End` 2 = **57** | **二次骨链的「根」都是 unconnected**——它们不从父骨 tail 长出来 |

**结论**：FK 必须逐骨读 `use_connect`，**不许硬编码名单**。硬编码 8 根会让 49 根骨的 location 被错误清零（或该清零的没清）。

---

## 5. 运行时机

| 时机 | 必须 |
|---|---|
| 启动时 | ✅（失败进只读模式） |
| 换 blend / 改骨架 | ✅ |
| 加/改 IK 约束 | ✅ |
| 改 `fk.py` | ✅ |
| 每个 P 阶段交付前 | ✅ |
| 每次 CI / 交付脚本 | ✅ |

**失败时不允许「先跑起来再说」**——FK 不准等于 L2 的全部预算打水漂。

---

## 6. 实现注意

1. **拓扑序**：不要用 `bpy.data.bones` 的顺序，要自己按 parent 递归排（Blender 不保证顺序）。
2. **数值类型**：用 `mathutils.Matrix`（不是 numpy 4×4），避免来回转换误差。
3. **`matrix_basis` 的备份/恢复**：测试会改姿态，用完必须恢复（用 `try/finally` + 深拷贝）。
4. **每帧 update 一次**：10 组 × 209 骨，全部算完再统一比对，不要边算边 update（慢 200 倍）。

---

## 7. 验收

一条命令：

```bash
python tests/test_fk_golden.py
```

**2026-11-24 实测（numpy 实现，9 用例 × 209 骨）：**

```
[fk] 9 用例 × 209 骨
  [PASS] rest           pos=0.000000mm rot=0.000003deg
  [PASS] rand30         pos=0.000130mm rot=0.000010deg
  [PASS] rand90         pos=0.000193mm rot=0.000014deg
  [PASS] main21         pos=0.000131mm rot=0.000007deg
  [PASS] hips_loc       pos=0.000086mm rot=0.000003deg
  [PASS] spine_loc      pos=0.000067mm rot=0.000003deg
  [PASS] hands_loc      pos=0.000000mm rot=0.000003deg
  [PASS] fingers        pos=0.000036mm rot=0.000007deg
  [PASS] connected_loc  pos=0.000000mm rot=0.000003deg
[asserts] IK 全 0 / matrix_world 偏差 2.4e-8 / connected 152 / unconnected 57 / scale≈1
[subset] 21 主骨子集与全量一致 max=0.000000000mm
RESULT: PASS
```

**精度判据（<0.01mm）实测余量 50 倍**（最差 0.000193mm）。

**性能实测（同一台机器）**：

| 实现 | FK 21 骨 | FK 209 骨 | 10000 次 21 骨 |
|---|---|---|---|
| mathutils（Blender 内） | 0.018 ms | 0.352 ms | 180 ms |
| **numpy（独立 Python）** | **0.051 ms** | **0.520 ms** | **511 ms** |

numpy 慢 2.8 倍，原因是 4×4 小矩阵的 Python 调用开销。**两者公式相同，golden test 保证一致。**
如果 P7 发现 511 ms 不可接受，再换 mathutils 实现（只在 Blender 内可用，测试仍用 numpy）。

**任何一个 `FAIL` 都不能开工。**

---

## 8. 风险

1. **浮点误差在 0.01 mm 边缘**：如果某骨链很长（手指末端），误差会累积。届时把阈值放宽到 0.05 mm，但要**写明放宽了**，不能悄悄改。
2. **测试姿态写回场景会污染用户工作**：必须在 `try/finally` 里恢复全部 `matrix_basis`，且不保存 blend。
