# 14 · 失败条目的生命周期（failure & TTL）

> 对应 HANDOFF §12 第 14 份、04-library §4
> 产出：`core/library.py` 的 `failed` 处理 + `tools/ttl_sweep.py`
> 一句话：**一次误判不能永久封死一个意图。**

---

## 1. `failed` 条目长什么样

```json
{
  "id": "lib-0057",
  "status": "failed",
  "family": "pat_head",
  "intent_key": {...},
  "unsatisfied": ["hand_near_anchor"],
  "reason": "family_infeasible",
  "detail": {
    "evals": 10000,
    "best_total_err": 0.42,
    "best_unsatisfied": [{"name": "hand_near_anchor", "err": 0.42, "detail": {"d_mm": 121}}]
  },
  "ttl": {
    "created_fingerprint": "f-20261123-a1b2c3",
    "expires_on": ["bounds", "weights", "capsules", "collision", "rig"]
  },
  "created_at": "..."
}
```

| 字段 | 说明 |
|---|---|
| `unsatisfied` | 哪几条谓词没满足（名字列表，用于检索时快速判断） |
| `detail.best_total_err` | 最好解差多少——**这是「差一点」和「差很远」的分界线** |
| `ttl.created_fingerprint` | 创建时的配置指纹 |
| `ttl.expires_on` | 哪些输入变了就失效 |

---

## 2. 配置指纹

```python
def fingerprint() -> str:
    h = hashlib.sha256()
    for f in ["bounds.json", "weights.json", "capsules.json", "collision.json"]:
        h.update(read_bytes(f))
    h.update(bpy_checksum().encode())     # 骨架校验和（来自 boot）
    return "f-" + h.hexdigest()[:16]
```

每次启动算一次，存内存 + 写进 `boot` 事件。

**为什么用哈希而不是 mtime**：mtime 会因为「打开文件又没改」而变化，会误触发大面积重解。

---

## 3. 失效规则

| 变化 | 失效？ | 理由 |
|---|---|---|
| `bounds.json`（族范围放宽） | ✅ | 之前搜不到的现在可能搜到 |
| `weights.json`（权重变） | ✅ | 搜索目标变了 |
| `capsules.json`（半径变） | ✅ | 约束松了/紧了 |
| `collision.json` | ✅ | 同上 |
| 骨架（`fk_checksum`） | ✅ | 全废，连 `provisional` 也要重验 |
| 情绪参数（`emotion_bias.json`） | ❌ | 不影响求解 |
| 日志/文档 | ❌ | 无关 |

---

## 4. 扫描与重开

```python
def sweep(now):
    cur = fingerprint()
    for e in library.where(status="failed"):
        if e.ttl.created_fingerprint != cur:
            library.set_status(e.id, "stale")     # 不删，只是不再拦截
            queue_re_solve(e.intent_key)
```

**`stale` 是第 5 个状态**（`provisional/verified/rejected/failed/stale`）：

- 检索时**跳过**（和 `failed` 一样）
- 但**允许重解**（和 `failed` 不同）
- 重解后如果成功 → 更新为 `provisional`；再失败 → 更新 `failed` 的新 fingerprint

这样不会无限重复：同一个 fingerprint 下失败一次就不再试，配置一变才重试。

---

## 5. `rejected` **不自动失效**

```
rejected 只能靠人工清：library.clear_rejected(id) 或 --force
```

理由：`rejected` 是「BVH 判它真穿模」，不是「搜不到」。配置变化不会让一个真穿模的动作变得不穿模（除非阈值放宽，那是人为的，应该显式清）。

**但有一条联动**：如果 `collision.json` 变了，`rejected` 应该被**重新检查**，而不是直接清。因为可能是阈值太严导致的误杀。

```python
if collision_changed:
    for e in library.where(status="rejected"):
        re_verify_async(e)      # 走一次 BVH，过了就转 verified
```

---

## 6. 验收

| 怎么测 | 预期 | 调参位置 |
|---|---|---|
| 造一个必然失败的意图 | 存 `failed`，带 `unsatisfied` | — |
| 同一意图再请求 | 直接跳过，不再烧 10000 次 | `sweep` |
| 改 `bounds.json` 一个数 | 该 failed 变 `stale`，下次请求重新求解 | fingerprint |
| 连续触发 100 次同一失败意图 | 只算 1 次 | 检索时跳过 |
| 造一个必然穿模的意图（BVH 判死） | `rejected`，改配置后仍 `rejected`（除非手动清） | — |

**第 3、4 条是核心**：`failed` 的目的是**省算力**，不是惩罚。

---

## 7. 风险

1. **fingerprint 太敏感**（比如 JSON 格式化变了导致哈希变）→ 大面积重解。用稳定的规范化 JSON（排序键、去空白）再哈希。
2. **`stale` 没被处理会一直挂着**——它只是标记，需要 `sweep` 真的触发重解。如果 `sweep` 只在启动跑，长期不重启就永远不重解。建议 `sweep` 在每次配置热重载时也跑。
