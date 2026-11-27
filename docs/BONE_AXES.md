# 骨骼轴向映射表（全向实测）

每根骨绕**世界** ('x', 'y', 'z') 轴转 ±30°，看末端骨往哪动。
**这个模型不是镜像的，左右符号可能不同——写关节角前先查这张表。**

| 骨 | 轴 | +deg 末端位移 (dx, dy, dz) mm | -deg 末端位移 mm | 结论 |
|---|---|---|---|---|
| leftShoulder | x | (0, 115, 24) | (0, -112, 37) | leftHand 向 +Y |
| leftShoulder | y | (-139, -0, -67) | (87, 0, 128) | leftHand 向 -X |
| leftShoulder | z | (-19, 99, 0) | (-33, -95, 0) | leftHand 向 +Y |
| leftUpperArm | x | (0, 109, 22) | (0, -106, 36) | leftHand 向 +Y |
| leftUpperArm | y | (-127, -0, -45) | (88, 0, 103) | leftHand 向 -X |
| leftUpperArm | z | (-13, 76, 0) | (-26, -72, 0) | leftHand 向 +Y |
| leftLowerArm | x | (0, 48, 8) | (0, -46, 17) | leftHand 向 +Y |
| leftLowerArm | y | (-56, 0, -20) | (38, 0, 45) | leftHand 向 -X |
| leftLowerArm | z | (-4, 34, 0) | (-14, -31, 0) | leftHand 向 +Y |
| rightShoulder | x | (0, 115, 24) | (0, -112, 37) | rightHand 向 +Y |
| rightShoulder | y | (-87, 0, 129) | (140, 0, -68) | rightHand 向 +Z |
| rightShoulder | z | (33, -97, 0) | (20, 100, 0) | rightHand 向 -Y |
| rightUpperArm | x | (0, 110, 23) | (0, -106, 35) | rightHand 向 +Y |
| rightUpperArm | y | (-88, 0, 104) | (128, 0, -46) | rightHand 向 +Z |
| rightUpperArm | z | (27, -73, -0) | (14, 77, -0) | rightHand 向 -Y |
| rightLowerArm | x | (0, 49, 8) | (0, -46, 17) | rightHand 向 +Y |
| rightLowerArm | y | (-38, 0, 47) | (57, 0, -21) | rightHand 向 +Z |
| rightLowerArm | z | (14, -33, 0) | (5, 35, 0) | rightHand 向 -Y |
| leftUpperLeg | x | (0, 211, 65) | (0, -215, 49) | leftFoot 向 +Y |
| leftUpperLeg | y | (-216, -0, 43) | (209, -0, 71) | leftFoot 向 -X |
| leftUpperLeg | z | (-11, 12, 0) | (4, -16, 0) | leftFoot 向 +Y |
| leftLowerLeg | x | (0, 113, 39) | (0, -117, 23) | leftFoot 向 +Y |
| leftLowerLeg | y | (-117, -0, 23) | (113, 0, 39) | leftFoot 向 -X |
| leftLowerLeg | z | (-10, 6, 0) | (6, -10, 0) | leftFoot 向 -X |
| rightUpperLeg | x | (0, 211, 65) | (0, -215, 49) | rightFoot 向 +Y |
| rightUpperLeg | y | (-209, -0, 71) | (216, -0, 43) | rightFoot 向 -X |
| rightUpperLeg | z | (-4, -16, 0) | (11, 12, 0) | rightFoot 向 -Y |
| rightLowerLeg | x | (0, 113, 39) | (0, -117, 23) | rightFoot 向 +Y |
| rightLowerLeg | y | (-113, 0, 39) | (117, 0, 23) | rightFoot 向 -X |
| rightLowerLeg | z | (-6, -10, -0) | (10, 6, 0) | rightFoot 向 -Y |
| spine | x | (0, -144, -26) | (0, 137, -49) | head 向 -Y |
| spine | y | (141, -0, -38) | (-141, -0, -38) | head 向 +X |
| spine | z | (-12, -3, 0) | (12, -3, 0) | head 向 -X |
| chest | x | (0, -107, -16) | (-0, 100, -39) | head 向 -Y |
| chest | y | (103, 0, -28) | (-103, -0, -28) | head 向 +X |
| chest | z | (-12, -3, 0) | (12, -3, 0) | head 向 -X |
| upperChest | x | (0, -70, -10) | (-0, 66, -26) | head 向 -Y |
| upperChest | y | (68, 0, -18) | (-68, 0, -18) | head 向 +X |
| upperChest | z | (-8, -2, 0) | (8, -2, 0) | head 向 -X |
| neck | x | (0, -21, -8) | (0, 22, -4) | head 向 -Y |
| neck | y | (21, 0, -6) | (-21, 0, -6) | head 向 +X |
| neck | z | (2, 1, 0) | (-2, 1, 0) | head 向 +X |
| head | x | (-0, -50, -18) | (0, 52, -9) | mixamorig:HeadTop_End 向 -Y |
| head | y | (51, 0, -14) | (-51, 0, -14) | mixamorig:HeadTop_End 向 +X |
| head | z | (5, 1, 0) | (-5, 1, 0) | mixamorig:HeadTop_End 向 +X |
| hips | x | (0, -161, -33) | (0, 156, -52) | head 向 -Y |
| hips | y | (158, -0, -42) | (-158, -0, -42) | head 向 +X |
| hips | z | (-10, -3, 0) | (10, -3, 0) | head 向 -X |
| WhaleTail_01 | x | (-0, 112, 155) | (-0, -174, -79) | WhaleTail_04 向 +Z |
| WhaleTail_01 | y | (-133, 0, 76) | (153, 0, 1) | WhaleTail_04 向 -X |
| WhaleTail_01 | z | (-107, -69, 0) | (127, 6, 0) | WhaleTail_04 向 -X |
