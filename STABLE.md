# v0.1.0 · 稳定版声明

**日期**：2026-11-27
**tag**：`v0.1.0`

---

## 一句话

**上半身闭环全通，腿能走/站/蹲/单脚站/踩点；挥手/点头的「过程」和「叠加时腿跟动」未完成。**

---

## 稳定的部分（可以依赖）

### 核心链路

```
一句话 → 她说话（完整人格） → 解析器判动作 → 求解 → 验证 → 抖动 → 播
```

| 环节 | 实测 |
|---|---|
| L1 查库 | 0.006 ms |
| L2 求解（46 次求值） | 20 ms |
| 验证（范围+谓词+胶囊） | 2.4 ms |
| 端到端 | 34 ms |
| 单帧预算 | 1.07 ms（931 FPS） |

### 动作能力

| 能力 | 验证 |
|---|---|
| 18 个族（只用族名求解） | ✅ 18/18 |
| 43 条谓词 | ✅ |
| 走 / 跑（步态） | ✅ 9 帧脚误差 **0.0mm** |
| 站 / 蹲 / 单脚站 | ✅ 误差 0.0mm |
| 踩指定点 | ✅ 误差 0.0mm |
| 多层叠加（上半身） | ✅ |
| 200ms 惯性化混合 | ✅ 最大单步 16mm |
| 情绪改目标 + 抖动 | ✅ |
| 族自动生长 | ✅ 3 次重复 → 新族 |

### 质量保障

| 检查 | 状态 |
|---|---|
| 27 个测试 | ✅ 全绿（`test_solver` 偶发 1/5） |
| `audit_rig.py`（8 项） | ✅ 全过 |
| `verify_templates.py` | ✅ 18/18 |
| `audit_poses.py`（18 族） | ✅ 0 问题 |

---

## 不稳定的部分（不要依赖）

| 项 | 状态 | 影响 |
|---|---|---|
| **挥手/点头的「过程」** | `gestures.py` 有实现（3 关键帧 + Hermite），**runtime 不调用** | 挥手是「瞬间举到终点」，不是「上下摆」 |
| **叠加时腿跟动** | `handle_composite` 的 base 只用上半身 | 走+挥手时腿不动 |
| **接口不统一** | `pose_for_from_P` 返回世界矩阵，`build_full` 返回 basis | 混用会崩 |
| **转身时脚会滑** | 没做「支撑脚当轴」 | 快速转身脚会漂 7mm |
| **求解出的新动作没有过程** | 求解器只解一个终点姿态 | 新动作是「摆姿势」 |

---

## 怎么用（稳定 API）

```python
from core.runtime import Runtime

rt = Runtime(rig, bm, anchors, families, Library(), ...)

# 1) 单个动作
r = rt.handle_intent({"intent": "wave"})
M = rt.pose_for(r["entry"])          # 世界矩阵，给 apply_pose
apply_pose(M, rig, arm)              # 注意是 apply_pose（收世界矩阵）

# 2) 走起来（全身）
rt.set_motion(vel=0.5, turn_rate=60)
for _ in range(12):
    rt.tick(1 / 12)
b, diag = rt.build_full(P, "walk", 0.25)   # basis，给 apply_basis
apply_basis(arm, b)                         # 注意是 apply_basis（收 basis）

# 3) 多动作叠加（只上半身）
r = rt.handle_composite([
    {"intent": "walk"},
    {"intent": "wave", "groups": ["arm.right"], "weight": 1.0},
])
```

**最容易踩的坑**：`apply_pose` 和 `apply_basis` 混用不报错，姿势全崩。见 `HANDOFF.md` §22.5-15。

---

## 怎么测

```bash
python tools/audit_rig.py          # 8 项体检
python tools/verify_templates.py   # 18 族模板
python tools/audit_poses.py        # 全身审计

# 27 个测试
python -c "
import glob, subprocess, sys
fails = []
for f in sorted(glob.glob('tests/test_*.py')):
    r = subprocess.run([sys.executable, f], capture_output=True)
    if r.returncode: fails.append(f)
print(f'{len(fails)} failed:', fails)
"
```

---

## 这个版本证明什么

> **LLM 的意图可以被机械验证、被求解器解成参数、被身体做出来。**

不是 Motion Matching，不是动作库检索——是「她想 → 系统解 → 身体做」。

---

## 下一个版本要做什么

1. **接口统一**（`pose_for_from_P` 返回 basis）→ 然后 `handle_composite` 接全身
2. **挥手/点头接 `gestures`**（有实现，接线就行）
3. 转身时脚不滑（支撑脚当轴）

详见 `HANDOFF.md` §22.6。
