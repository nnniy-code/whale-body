# 03 · 门（gate）

> 对应 HANDOFF §12 第 3 份
> 产出：`core/gate.py` 的接口与输出格式
> 职责：把「姿态序列 + 谓词列表」变成「通过 / 不通过 + 违规在哪」。

---

## 1. 两个入口，不能混用

| 入口 | 用途 | 输出 | 谁调 |
|---|---|---|---|
| `evaluate()` | **搜索引导** | `total_err`（连续） | CMA-ES |
| `verdict()` | **判决** | `pass/total` + violations | dispatcher、L3 |

```python
def evaluate(trajectory, predicates) -> float:
    """纯标量，用于 CMA-ES。必须有梯度，必须快（<0.02 ms）。"""

def verdict(trajectory, predicates, family: str) -> Verdict:
    """严格布尔 + 定位。family 用来注入族阈值档（见 01-bounds §5）。"""
```

**为什么分开**（HANDOFF §12 也写了）：用 `total_err` 当通过判据 = 「差不多就算过」；用 `passed` 当搜索目标 = 布尔没梯度，搜索退化。

---

## 2. 输入

```python
@dataclass(frozen=True)
class Trajectory:
    frames: list[dict[str, Transform]]   # 每帧 **21 主骨** 的世界变换（来自 fk.py）
    phase: list[float]                   # 每帧相位（G 提供）
    dt_s: float                          # 帧间隔（1/30）
    meta: dict                           # {"duty": 0.62, "family": "walk", ...}
```

`frames` 是 **fk.py 的输出**，不是 bpy 的 `pose.bones[].matrix`。**gate 里出现 `bpy` 就是错的。**

**只含 21 主骨，不含 188 根二次骨。** 求解循环用不起 209 骨 FK（0.352 ms 是 21 骨的 19 倍）。所以 `no_self_collision` 的胶囊碰撞**只覆盖 21 主骨之间**，头发/裙摆/尾巴之间的穿插由 L3 的 BVH 负责（第 13 份）。这个分工要写进谓词的 docstring，否则实现者会以为胶囊能抓「头发插进腿」。

## 3. 输出

```python
@dataclass
class Verdict:
    passed: bool                 # 全部 err == 0
    pass_count: int
    total: int
    total_err: float             # 加权和，用于比较「哪次更好」
    violations: list[Violation]  # 只含 err > 0 的
    max_deg_per_frame: float     # 冗余但必填（日志和旧指标对齐）
```

```python
@dataclass
class Violation:
    predicate: str
    err: float
    time_sem: str
    detail: dict                 # 直接来自 PredicateResult.detail
    worst_frame: int | None      # throughout 类必填
```

**`worst_frame` 对 `throughout` 是必填**。只报「foot_grounded 违规」，不报「第 14 帧最差」，等于没定位——上一代护栏就栽在这（缺陷 #7：只测量不阻止，也不说在哪）。

---

## 4. 求值流程

```
for each predicate:
    per_frame = [p.eval(frames[t]) for t in range(len(frames))]
    err, worst = 按 time_sem 合成(per_frame)       # 见 02-predicates §1.3
    weight = weights.json 或谓词默认
    收集
total_err = Σ(w×err) / Σ(w)
passed = all(err == 0)
```

### 4.1 `throughout` 的合成

```python
def merge_throughout(errs, beta=10.0):
    if not errs: return 0.0, None
    m = max(errs)
    soft = (m + math.log(sum(math.exp(beta*(e-m)) for e in errs))) / beta
    return soft, errs.index(m)     # worst_frame = 硬最大那帧
```

`worst_frame` 用**硬**最大定位（人要能指到具体帧），`err` 用**软**最大（给搜索梯度）。两者不是同一个数，不要互相替代。

### 4.2 族阈值注入

`foot_grounded` 的 `tol_mm` 随 `G.duty` 变（walk 35 mm / run 腾空段跳过）。gate 从 `trajectory.meta` 读，**不在谓词里写死**：

```python
tol = family_thresholds[meta["family"]]["foot_grounded_tol_mm"]
```

族表来自 `bounds.json`（第 1 份文档）。

---

## 5. 违规定位格式（日志直接用）

```json
{
  "predicate": "foot_grounded",
  "err": 1.0,
  "time_sem": "throughout",
  "worst_frame": 14,
  "detail": {"side": "left", "sole_z_mm": 64.5, "tol_mm": 35}
}
```

进 `logs/events.jsonl` 的 `gate` 事件时，原样作为 `violations[]`。

**一条违规必须能回答三个问题**：哪个谓词、哪一帧、差多少。缺一条就退回改 `detail`。

---

## 6. 与 L3 的接口（不要搞混）

| | L2 的 gate | L3 的 BVH |
|---|---|---|
| 判什么 | 谓词（胶囊近似） | 真实三角面穿插 |
| 成本 | 0.018 ms/次 | 0.5–2 s/次 |
| 输出 | `Verdict` | `list[{frame, bone, region, depth_mm}]` |

L3 不复用 gate，但**复用 `Violation` 的形状**（都带 `worst_frame` / `detail`），这样日志和 UI 只需要一套渲染。

---

## 7. 验收

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 5 种故意造错（穿地/离地/插躯干/出界/倾倒） | 5 种全抓到，`worst_frame` 正确 | 谓词阈值 |
| `verdict` 与 `pass/total` 一致 | 永远一致 | 不许分叉 |
| `evaluate` 单次 <0.02 ms | 达 | 别在 gate 里调 bpy |
| 造一个全部通过的姿态 | `total_err == 0` 且 `passed == True` | — |
| 造一个「差一点」的姿态 | `passed == False` 但 `total_err < 0.1` | 两者必须分开 |

**第 2 条是上一代缺陷 #7 的复发检测**：`mode:"pass"` + `ok:true` + `verdict:"FAIL"` 同时存在过。写进 `selftest_gate.py`。

---

## 8. 风险

1. **权重调错会让搜索去追假目标**——表现是「total_err 一直降，但通过率不动」。看日志里 `total_err` 曲线和 `pass_count` 曲线是否背离。
2. **`detail` 写得太省**是常见坑。规则：`detail` 必须包含**所有参与计算的数**，让看日志的人不用重算。
