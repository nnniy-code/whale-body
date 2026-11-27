# 骨架体检报告

结论：全部通过

# 骨架体检报告


## 1. 骨骼完整性

- **PASS** 骨骼数 = 209 209
- **PASS** bones_map 覆盖全部 缺 0
- **PASS** parent 全可解析 
- **PASS** names 与 parent 键一致 

## 2. rest 矩阵健全

- **PASS** 所有旋转正交 []
- **PASS** 无 NaN 
- **PASS** 行列式 ≈ 1（无镜像） min=0.999985 max=1.000005

## 3. 主骨局部轴（世界坐标）

| 骨 | 局部X→世界 | 局部Y→世界 | 局部Z→世界 |
|---|---|---|---|
| spine | (-0.00,-1.00,+0.00) | (+0.00,-0.00,+1.00) | (-1.00,+0.00,+0.00) |
| chest | (+0.11,-0.99,+0.11) | (+0.00,+0.11,+0.99) | (-0.99,-0.11,+0.01) |
| head | (-0.09,-0.99,-0.09) | (+0.00,-0.09,+1.00) | (-1.00,+0.09,+0.01) |
| leftUpperArm | (-0.03,-1.00,+0.01) | (+0.56,-0.03,-0.83) | (+0.83,-0.01,+0.56) |
| rightUpperArm | (-0.03,-1.00,+0.05) | (-0.56,-0.03,-0.83) | (+0.83,-0.05,-0.56) |
| leftLowerArm | (-0.08,-1.00,+0.04) | (+0.57,-0.08,-0.82) | (+0.82,-0.04,+0.57) |
| rightLowerArm | (-0.08,-0.99,+0.15) | (-0.58,-0.08,-0.81) | (+0.81,-0.15,-0.57) |
| leftUpperLeg | (-0.00,-1.00,-0.00) | (+0.06,-0.00,-1.00) | (+1.00,-0.00,+0.06) |
| rightUpperLeg | (-0.00,-1.00,+0.00) | (-0.06,+0.00,-1.00) | (+1.00,-0.00,-0.06) |

## 4. 左右镜像性

- **PASS** leftUpperArm ↔ rightUpperArm 关于 X 镜像 diff=0.000
- **PASS** leftLowerArm ↔ rightLowerArm 关于 X 镜像 diff=0.017
- **PASS** leftUpperLeg ↔ rightUpperLeg 关于 X 镜像 diff=0.000
- **PASS** leftFoot ↔ rightFoot 关于 X 镜像 diff=0.000

## 5. 绕世界轴运动

| 骨 | 轴 | +30° 主方向 | 位移 mm |
|---|---|---|---|
| rightUpperArm | x | +Y | 110 |
| rightUpperArm | y | +Z | 104 |
| rightUpperArm | z | -Y | 73 |
| leftUpperArm | x | +Y | 109 |
| leftUpperArm | y | -X | 127 |
| leftUpperArm | z | +Y | 76 |
| rightLowerArm | x | +Y | 49 |
| rightLowerArm | y | +Z | 47 |
| rightLowerArm | z | -Y | 33 |
| leftLowerArm | x | +Y | 48 |
| leftLowerArm | y | -X | 56 |
| leftLowerArm | z | +Y | 34 |
| rightUpperLeg | x | +Y | 211 |
| rightUpperLeg | y | -X | 209 |
| rightUpperLeg | z | -Y | 16 |
| leftUpperLeg | x | +Y | 211 |
| leftUpperLeg | y | -X | 216 |
| leftUpperLeg | z | +Y | 12 |
| spine | x | -Y | 144 |
| spine | y | +X | 141 |
| spine | z | -X | 12 |
| head | x | -Y | 50 |
| head | y | +X | 51 |
| head | z | +X | 5 |

## 6. pose_gen 参数有效性

| 参数 | 改动 | 影响骨 | 位移 mm | 是否有效 |
|---|---|---|---|---|
| lean | +15 | head | 66.0 | ✅ |
- **PASS** lean 有影响（>1mm） 66.0mm
| side_bend | +15 | head | 73.4 | ✅ |
- **PASS** side_bend 有影响（>1mm） 73.4mm
| head_nod | +15 | mixamorig:HeadTop_End | 26.6 | ✅ |
- **PASS** head_nod 有影响（>1mm） 26.6mm
| head_tilt | +15 | mixamorig:HeadTop_End | 26.7 | ✅ |
- **PASS** head_tilt 有影响（>1mm） 26.7mm
| head_yaw | +15 | mixamorig:HeadTop_End | 2.4 | ✅ |
- **PASS** head_yaw 有影响（>1mm） 2.4mm
| arm_lift | +40 | rightHand | 169.5 | ✅ |
- **PASS** arm_lift 有影响（>1mm） 169.5mm
| arm_swing | +30 | rightHand | 102.2 | ✅ |
- **PASS** arm_swing 有影响（>1mm） 102.2mm
| arm_roll | +40 | rightHand | 115.5 | ✅ |
- **PASS** arm_roll 有影响（>1mm） 115.5mm
| elbow_bend | +40 | rightHand | 65.1 | ✅ |
- **PASS** elbow_bend 有影响（>1mm） 65.1mm
| wrist_bend | +30 | _HANDTIP_R | 16.1 | ✅ |
- **PASS** wrist_bend 有影响（>1mm） 16.1mm
| hand_bias | +25 | _HANDTIP_R | 17.3 | ✅ |
- **PASS** hand_bias 有影响（>1mm） 17.3mm
| tail_curl | +20 | WhaleTail_04 | 46.9 | ✅ |
- **PASS** tail_curl 有影响（>1mm） 46.9mm

## 7. 手臂生理合理性

- elbow_bend=0 → 实际肘角 167.2° ✅
- **PASS** elbow=0 时接近伸直（>150°） 167.2°
- elbow_bend=45 → 实际肘角 140.4° ✅
- elbow_bend=90 → 实际肘角 104.3° ✅
- elbow_bend=130 → 实际肘角 76.1° ✅
- **PASS** elbow=130 时明显弯曲（<110°） 76.1°

## 8. 左右手对称

- **PASS** 右手在右侧 x=-0.215
- **PASS** 左手在左侧 x=0.218
- **PASS** 左右关于 X 对称（<20mm） 4.0mm
- **PASS** 左右高度接近 2.7mm
