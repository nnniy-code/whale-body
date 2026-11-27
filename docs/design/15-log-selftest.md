# 15 · 日志与验收自检（log selftest）

> 对应 HANDOFF §12 第 15 份、[LOGGING.md](../LOGGING.md)
> 产出：`tests/test_logging.py`
> 一句话：**日志本身也要被验收，否则它就是一堆没人敢信的文本。**

---

## 1. 三条硬自检

### ① `verdict` 与 `pass/total` 必须一致

```python
for ev in events(type="gate"):
    assert (ev["verdict"] == "PASS") == (ev["pass"] == ev["total"])
```

**这是上一代缺陷 #7 的复发检测**：`mode:"pass"` + `ok:true` + `verdict:"FAIL"` 曾经同时存在。

### ② `library.jsonl` 重放 == 内存库

```python
lib_replayed = replay(load_jsonl("logs/library.jsonl"))
lib_memory   = library.dump()
assert deep_equal(lib_replayed, lib_memory)
```

重放规则：

- `lib_put` 按 `seq` 顺序插入
- `lib_status` 更新状态
- 后者覆盖前者的同名字段

**失败含义**：要么日志丢条，要么库被绕过了日志直接改。后一种是架构违规。

### ③ 每个 `trace_id` 的事件链完整

```python
for tid in trace_ids:
    types = {e["type"] for e in events(trace_id=tid)}
    assert "intent" in types
    assert ("play" in types) or ("solve_end" in types)
```

有 `intent` 就必须有结局事件。缺了说明**链路断了**（比如求解崩了、dispatcher 没接）。

---

## 2. 一条数据完整性检查

```python
# seq 必须严格递增且无空洞
seqs = [e["seq"] for e in events()]
assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))
```

有空洞 = 写日志失败被吞了。这比「日志多一条」严重，因为它意味着**有一刻系统在裸奔**。

---

## 3. 单位与字段完整性

| 检查 | 规则 |
|---|---|
| 数值字段带单位 | 以 `_mm` / `_deg` / `_s` / `_ms` 结尾的字段必须存在且是数字 |
| `detail` 非空 | `gate.violations[].detail` 不能是 `{}` |
| `ts` 可解析 | ISO 8601 带时区 |

```python
for v in events(type="gate"):
    for viol in v["violations"]:
        assert viol["detail"], f"violation 缺 detail: {viol}"
```

---

## 4. 查询示例要能跑通

[LOGGING.md §5](../LOGGING.md) 里的 7 条 `jq` 查询，写进 `tests/test_log_queries.py`，**对着样本日志跑一遍**，确保语法和字段名都对。

这是防「文档里的字段名和实际写的不一致」——那类错只有真跑才发现。

---

## 5. 验收

| 怎么测 | 预期 |
|---|---|
| 造一条矛盾 `gate`（verdict=PASS 但 pass<total） | 自检 ① 抓到 |
| 手改一条 `library` 状态 | 自检 ② 抓到 |
| 删一条 `play` | 自检 ③ 抓到 |
| 造一个 seq 空洞 | 完整性检查抓到 |
| 跑 7 条 jq 查询 | 全部返回 |

---

## 6. 风险

1. **重放要求 `library.jsonl` 本身完整**——如果某次写盘失败，重放会掉条。所以 `library` 事件必须 `flush` 后才算提交。
2. **自检不能依赖真实运行**：用样本日志（`tests/fixtures/logs/`），否则 CI 上跑不了。
