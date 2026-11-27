# 骨骼映射表

> 自动生成：`python tools/gen_bones_doc.py`
> 数据源：`config/bones_map.json`（Blender 端扫描 209 根骨）

## 0. 摘要

| 项 | 值 |
|---|---|
| 骨数 | 209 |
| 根骨 | hips（唯一） |
| 最大链深 | 11 |
| 命名前缀 | 166 无前缀 + 43 带 `mixamorig:` —— **混用** |
| 零长度骨 | 0 |
| 左右不对称 | 0 |
| 权重 >0.5 的顶点为零 | 43 根（含 13 根完全无 VG 组） |

## 1. 链分类

| 链 | 数量 | 用途 | 进求解？ |
|---|---|---|---|
| `skirt` | 64 | 裙摆 | ❌ 二次运动 |
| `hair` | 60 | 头发 | ❌ 二次运动 |
| `finger` | 40 | 手指 | ❌ 静态跟随 |
| `main` | 21 | 主链（躯干 + 四肢） | ✅ 进 L2 求解 |
| `bangs` | 10 | 刘海 | ❌ 二次运动 |
| `ribbon` | 6 | 腰饰 | ❌ 二次运动 |
| `toe` | 4 | 脚趾 | ❌ 静态跟随 |
| `tail` | 4 | 鲸鱼尾巴 | ❌ 二次运动 |

合计：209 = 209

## 2. 局部轴 → 世界（**最容易踩的坑**）

骨的 `matrix_basis` 旋转是在**骨骼局部坐标系**里定义的。这具骨架的局部轴与世界轴**不一致**：

| 骨 | 局部 X 指向 | 局部 Y 指向 | 局部 Z 指向 |
|---|---|---|---|
| `hips` | -Y | +Z | -X |
| `spine` | -Y | +Z | -X |
| `head` | -Y | +Z | -X |
| `leftUpperArm` | -Y | -Z | +X |
| `leftFoot` | -X | -Y | ~+X([0.65, -0.49, 0.57]) |
| `WhaleTail_01` | -Y | ~-Z([0.24, 0.68, -0.69]) | +X |

**由此推出的操作映射**（**仅适用于躯干骨** `hips/spine/chest/upperChest/neck/head`）：

| 想要 | 世界轴 | 作用到躯干骨局部 |
|---|---|---|
| 前倾 / 后仰 | 绕世界 **X** | 局部 Z **负**转 / 正转 |
| 侧倾 | 绕世界 **Y** | 局部 X **负**转 |
| 转头 / 扭腰 | 绕世界 **Z** | 局部 Y |

> ⚠️ **四肢骨不能套上表**。实测 `leftUpperArm` 是 `x→-Y, y→-Z, z→+X`（和躯干不同），
> `leftFoot` 更是倾斜的（局部 Z = `[0.65,-0.49,0.57]`）。
> **一律用通用公式**：`R_local = R_rest.T @ R_world @ R_rest`（rest 正交，inv = T）。
> 参考实现：`tests/test_proprio.py::world_rot()`。**所有「语义角度 → basis」都要过这一步。**

## 3. 主链（21 根，进 L2 求解）

| 骨 | 父 | conn | 长度 mm | VG 顶点 | 局部轴 X/Y/Z |
|---|---|---|---|---|---|
| `hips` | — | ❌ | 46.8 | 0 | -Y/+Z/-X |
| `spine` | hips | ❌ | 74.18 | 557 | -Y/+Z/-X |
| `chest` | spine | ✅ | 70.71 | 1653 | -Y/+Z/-X |
| `upperChest` | chest | ✅ | 83.51 | 1112 | -Y/+Z/-X |
| `neck` | upperChest | ❌ | 43.12 | 350 | -Y/+Z/-X |
| `head` | neck | ❌ | 102.81 | 4607 | -Y/+Z/-X |
| `mixamorig:HeadTop_End` | head | ✅ | 102.81 | 0 | -Y/+Z/-X |
| `leftShoulder` | upperChest | ❌ | 48.29 | 0 | -Y/+X/+Z |
| `leftUpperArm` | leftShoulder | ✅ | 146.24 | 1325 | -Y/-Z/+X |
| `leftLowerArm` | leftUpperArm | ✅ | 114.97 | 653 | -Y/-Z/+X |
| `leftHand` | leftLowerArm | ✅ | 47.27 | 160 | -Y/-Z/+X |
| `rightShoulder` | upperChest | ❌ | 48.29 | 0 | -Y/-X/-Z |
| `rightUpperArm` | rightShoulder | ✅ | 146.24 | 1324 | -Y/-Z/+X |
| `rightLowerArm` | rightUpperArm | ✅ | 117.08 | 615 | -Y/-Z/+X |
| `rightHand` | rightLowerArm | ✅ | 46.26 | 191 | -Y/-Z/+X |
| `leftUpperLeg` | hips | ❌ | 195.57 | 119 | -Y/-Z/+X |
| `leftLowerLeg` | leftUpperLeg | ✅ | 231.41 | 228 | -Y/-Z/+X |
| `leftFoot` | leftLowerLeg | ✅ | 77.5 | 422 | -X/-Y/~+X([0.65, -0.49, 0.57]) |
| `rightUpperLeg` | hips | ❌ | 195.57 | 122 | -Y/-Z/+X |
| `rightLowerLeg` | rightUpperLeg | ✅ | 231.41 | 218 | -Y/-Z/+X |
| `rightFoot` | rightLowerLeg | ✅ | 77.5 | 436 | -X/-Y/~+X([0.65, -0.49, 0.57]) |

## 4. 异常与注意

### 4.1 权重 >0.5 的顶点为零：43 根

其中 13 根完全没有 VG 组（`*_End`、手指 tip），其余是「有组但权重都 <0.5」。
**影响**：胶囊半径和质心会漏掉这些骨 → 用父骨半径 × 0.5 或跳过。

<details><summary>名单</summary>

`hips`, `mixamorig:LeftToe_End`, `mixamorig:RightToe_End`, `leftShoulder`, `mixamorig:LeftHandIndex4`, `mixamorig:LeftHandMiddle4`, `mixamorig:LeftHandPinky4`, `mixamorig:LeftHandRing4`, `mixamorig:LeftHandThumb4`, `mixamorig:HeadTop_End`, `Hair_00_01`, `Hair_00_02`, `Hair_00_03`, `Hair_00_04`, `Hair_00_05`, `Hair_01_01`, `Hair_01_04`, `Hair_01_05`, `Hair_02_03`, `Hair_02_04`, `Hair_02_05`, `Hair_03_05`, `Hair_09_05`, `Hair_10_02`, `Hair_10_03`, `Hair_10_04`, `Hair_10_05`, `Hair_11_01`, `Hair_11_04`, `Hair_11_05`, `Bangs_00_01`, `Bangs_04_01`, `rightShoulder`, `mixamorig:RightHandIndex4`, `mixamorig:RightHandMiddle4`, `mixamorig:RightHandPinky4`, `mixamorig:RightHandRing4`, `mixamorig:RightHandThumb4`, `Skirt_03_04`, `Skirt_04_04`, `Skirt_12_04`, `Skirt_13_04`, `WaistRibbon_0_03`

</details>

### 4.3 左右对称

**全部对称**（rest 头位置镜像误差 <5mm，0 对异常）。可以放心用 `.L/.R` 对称生成。

## 5. 语义锚点 → 骨骼

| 锚点 | 骨骼 | 备注 |
|---|---|---|
| `mouth` | `head` | 虚拟点，需标定（09-anchors） |
| `chin` | `head` | 虚拟点 |
| `head_top` | `mixamorig:HeadTop_End` | 有骨，直接用 |
| `chest` | `chest` | 有骨 |
| `belly` | `spine` | 有骨 |
| `hand_center` | `leftHand / rightHand` | 有骨 |
| `foot_sole` | `leftFoot / rightFoot` | 虚拟点（脚底 40 采样点） |
| `tail_tip` | `WhaleTail_04` | 有骨 |

**没有眼睛/脸/嘴骨**——mouth 只能做空间锚点，不能做表情（见 HANDOFF §8.6）。

## 6. 后续代码必须遵守

1. **不许硬编码骨名**：`connected`、`parent`、`axes` 全从 `bones_map.json` 读。
2. **不许硬编码 unconnected 名单**（实测 57 根，不是初稿的 8 根）。
3. **语义角度先转局部**，再写 basis。
4. **43 根无权重骨**：胶囊用父骨 ×0.5，质心跳过。
5. **前缀混用**（166 无前缀 + 43 `mixamorig:`）：匹配骨名时要做归一化。

