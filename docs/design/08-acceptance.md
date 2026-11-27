# 08 · 模块验收判据（acceptance）

> 对应 HANDOFF §12 第 8 份
> 产出：`tests/` 下的验收脚本清单 + `tools/run_acceptance.py`
> 一句话：**每个阶段交付前跑一遍，红的不许往下走。**

---

## 1. 一键入口

```bash
python tools/run_acceptance.py            # 跑当前阶段
python tools/run_acceptance.py --all      # 跑所有已达的 P
python tools/run_acceptance.py --stage P3 # 只跑某阶段
```

输出格式：

```
[P3] gate-violation-detection ......... PASS  (5/5 cases)
[P3] predicate-selfcheck .............. PASS  (6/6)
[P3] regression-7 ..................... PASS  (7/7)
------------------------------------------------
RESULT: PASS
```

**任何一个 FAIL 返回非 0 退出码**，方便接 CI。

---

## 2. 全阶段清单

| 阶段 | 脚本 | 判据 | 失败动作 |
|---|---|---|---|
| **P0** | `test_trajectory.py` | 端点位置/速度误差 <1e-4；接缝速度跳变 <1e-6；**无 0 速平台**（导出 CSV 检查） | 停，改 Hermite |
| **P0** | `test_no_power_ease.py` | 全代码库 grep 不到 `**0.55` / `t**` 缓动 | 停 |
| **P0.5** | `test_fk_golden.py` | 10 姿态 × 209 骨，位置 <0.01mm，旋转 <0.01°；6 条断言 | **停，进只读模式** |
| **P1** | `test_proprio.py` | 任意姿态输出关节/末端/质心/支撑面；读 FK 不读 bpy | 停 |
| **P1.5** | `test_emotion.py` | `val` 20→90 时 1 s 内变化；`hand_near_mouth` 偏差 <1mm；`big_steps=0` | 停 |
| **P2** | `test_contact.py` | 抬脚/落脚 100% 正确（20 组） | 停 |
| **P3** | `test_predicates.py` | 6 条谓词自检（02-predicates §5） | 停 |
| **P3** | `test_gate.py` | 5 种故意造错全抓到；`worst_frame` 正确；`verdict` 与 `pass/total` 一致 | 停 |
| **P4** | `test_library.py` | 30 ms 内检索；重放 == 内存；去重生效 | 停 |
| **P5** | `test_dispatcher.py` | 起播 ≤200 ms；L2 期间不僵；打断回话 + 排队 | 停 |
| **P6** | `test_gait.py` | walk 滑移 <3mm、离地 <35mm、转 90° 不抽搐 | 停 |
| **P7** | `test_solver.py` | 基准集族内 ≥90%；calc/wall 分离；墙钟 <1 s | 记录，继续 |
| **P8** | `test_l3.py` | 10 族 verified ≥90%；rejected 带归因 | 记录，继续 |
| **全程** | `test_logging.py` | 3 条自检 + 完整性 | 停 |
| **全程** | `test_regression_7.py` | 旧项目 7 条用例（迁移后） | 停 |

**「停」vs「记录」的区别**：地基类（P0–P6、日志、回归）失败就停；求解质量类（P7/P8）失败只记录，因为它们是**研究性指标**，可能永远到不了 90%。

---

## 3. 每个脚本的「三件套」要求

每个测试文件开头必须写：

```python
"""
怎么测：python tests/test_fk_golden.py
预期现象：[golden] ALL PASS (10 cases, worst 0.0003mm)
调参位置：core/fk.py 的 prelocal 预计算
"""
```

这是用户反复要的「怎么测 / 预期现象 / 调参位置」。**没有这三行的测试不算交付。**

---

## 4. 回归门（P8 的硬要求）

新增 10 族之后，旧 7 条用例必须**一条不破**。

```python
def test_regression_7():
    for case in OLD_SELFTEST_CASES:     # 从 embodied-body/interface/selftest.py 迁移
        result = run_case(case)
        assert result.verdict == "PASS", f"{case.name} 退化"
```

旧用例在 `D:\DSproject\embodied-body\interface\selftest.py`（只读）。迁移时**只搬用例，不搬实现**（HANDOFF §6）。

---

## 5. 验收的元规则

1. **判据先自检**（坑 #3）：每个检查器都要先证明它能抓到「故意做错」的情况。`test_gate.py` 的 5 种错就是干这个的。
2. **阈值写进文件，不写进代码**：所有阈值来自 `config/*.json`，改阈值不改代码。
3. **失败要能定位**：任何 FAIL 都要打印「期望 vs 实际 + 哪一帧 + 哪根骨」。
4. **不许 `assert x is not None` 充数**：那不算判据。

---

## 6. 风险

1. **P7/P8 的「记录」可能被当成「过」**——必须在交付报告里明确写「P7 当前 62%，未达 90%」，不能只说「已跑通」。
2. **测试依赖真实 blend**：CI 上跑不了。建议 `tests/fixtures/` 放一份 209 骨的轻量替身（可以裁掉 mesh，只留骨架）。
