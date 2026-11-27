# body-v2 · 大肥鱼的身体

**当前状态**：上半身闭环全通，腿能走/站/蹲/单脚站/踩点，「叠加 + 全身」还没合到一起。

## 先读这三个

| 文件 | 什么时候读 |
|---|---|
| `HANDOFF.md` §22 | **先读这节**（v1.8 交接，当前状态 + 下一步 + 20 条坑） |
| `docs/DEVLOG.md` | 按时间倒序看最近做了什么 |
| `docs/RIG_AUDIT.md` | 骨架体检报告 |

## 数字

```
测试        45 个全绿
族          21 个
谓词        43 条
P 维度      12（上半身）
腿长        427 mm（硬约束）
求解        46 次 / 20 ms
L1 查库     0.006 ms
单帧预算    1.07 ms（931 FPS）
```

## 开工清单

```bash
python tools/audit_rig.py          # 8 项体检，必须全过
python tools/verify_templates.py   # 18 个族模板，必须 18/18
python tools/audit_poses.py        # 全身审计

# 全量测试
python -c "
import glob, subprocess, sys
fails = 0
for f in sorted(glob.glob('tests/test_*.py')):
    r = subprocess.run([sys.executable, f], capture_output=True)
    if r.returncode: fails += 1; print('FAIL', f)
print(f'{fails} failed')
"
```

## 目录

```
core/    33 个模块（FK → 感知 → 谓词 → 求解 → 腿 → 复合 → 运行时）
llm/     6 个（DeepSeek 客户端 + 大肥鱼人格 + 两阶段解析）
blender_addon/  1 个（Blender 常驻面板：Connect / Disconnect / 说话）
config/  7 个（骨骼/情绪/族/锚点/胶囊/碰撞/库）
tests/   45 个
tools/   44 个（体检/探针/基准/模拟）
docs/    7 个 md + design/18 + images/20+
sandbox/ 大肥鱼的只读人格副本
```

## 五个最容易踩的坑

1. **`apply_pose(M)` 收世界矩阵，`apply_basis(arm, basis)` 收 basis** —— 混用不报错，姿势崩
2. **腿长 427mm 是硬约束** —— stride/抬脚变大要同步降髋；髋不在中轴，余量只有 29mm
3. **角度是标量，方向要单独判** —— `knee_angle` 抓不到反折
4. **步态族的 `vel` 不是自由量** —— 唯一真值是 `v = stride/(duty×period)`；
   填错脚会滑（vel=0.5 时一个支撑期滑 118mm）。用 `set_motion(family="walk")`
5. **朝向只有一个来源：`BodyState.yaw`** —— `G.turn_rate` 只影响脚目标，
   且 `GaitTarget` 里已被清零；别再往骨架里塞第二个朝向

完整清单见 `HANDOFF.md` §22.5 和 §22.13。
