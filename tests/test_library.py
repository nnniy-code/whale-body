"""tests/test_library.py — P4 验收。

怎么测：python tests/test_library.py
预期现象：[library] ALL PASS
调参位置：core/library.py 的 W_* / HIT_THRESHOLD / DEDUP_THRESHOLD
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.library import (  # noqa: E402
    DEDUP_THRESHOLD, FALLBACK_THRESHOLD, HIT_THRESHOLD, Library, intent_key, key_distance,
)
from core.log import EventLog  # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def mk(preds, verb=None):
    return intent_key(preds, verb)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="bodyv2_lib_")
    log = EventLog(tmp, echo=False)

    print("[1] 空库")
    lib = Library(log)
    e, d = lib.best(mk([{"name": "hand_above_head", "args": {"side": "right"}}]))
    check("返回 None", e is None)
    check("query 为空", lib.query({}) == [])

    print("[2] 写入与检索")
    k1 = mk([{"name": "hand_above_head", "args": {"side": "right", "margin_mm": 50}}], "wave")
    e1 = lib.put("wave", k1, {"P": {"arm_swing": 30.0}, "G": {"duty": 0.5}}, dur_s=1.2)
    check("分配 id", e1.id == "lib-0001", e1.id)
    hit, dist = lib.best(k1)
    check("同 key 命中", hit is not None and hit.id == e1.id)
    check("距离 ≈ 0", dist < 1e-9, f"{dist:.6f}")

    print("[3] 阈值语义")
    k2 = mk([{"name": "hand_above_head", "args": {"side": "right", "margin_mm": 120}}], "wave")
    d2 = key_distance(k1, k2)
    check("同谓词不同数值：距离 <0.25", d2 < HIT_THRESHOLD, f"{d2:.3f}")
    k3 = mk([{"name": "foot_grounded", "args": {"side": "left"}}], "walk")
    d3 = key_distance(k1, k3)
    check("完全不同：距离 ≥0.25", d3 >= HIT_THRESHOLD, f"{d3:.3f}")
    check("兜底阈值更宽", FALLBACK_THRESHOLD > HIT_THRESHOLD)

    print("[4] 去重（<0.05 就地更新）")
    n_before = len(lib.entries)
    e1b = lib.put("wave", k1, {"P": {"arm_swing": 31.0}, "G": {"duty": 0.5}}, dur_s=1.2)
    check("没有新建条目", len(lib.entries) == n_before, f"{n_before} -> {len(lib.entries)}")
    check("params 被更新", e1b.params["P"]["arm_swing"] == 31.0)
    check("history 记了一条", len(e1b.history) == 1, str(len(e1b.history)))

    print("[5] 状态过滤")
    e2 = lib.put("walk", k3, {"P": {}, "G": {"duty": 0.62}}, dur_s=3.0)
    lib.set_status(e2.id, "failed")
    hit2, _ = lib.best(k3)
    check("failed 不可检索", hit2 is None)
    lib.set_status(e2.id, "verified")
    hit3, _ = lib.best(k3)
    check("verified 可检索", hit3 is not None)
    lib.set_status(e2.id, "rejected")
    check("rejected 不可检索", lib.best(k3)[0] is None)
    check("rejected 不在可播放集", e2.id not in [e.id for e in lib.searchable()])

    print("[6] 30ms 内查询（100 条）")
    for i in range(98):
        lib.put("f", mk([{"name": f"p{i}", "args": {}}], f"v{i}"), {"P": {}}, dur_s=1.0,
                status="verified")
    t0 = time.perf_counter()
    for _ in range(100):
        lib.best(k1)
    ms = (time.perf_counter() - t0) * 1000 / 100
    check("单次 <30ms", ms < 30.0, f"{ms:.3f}ms")

    print("[7] 日志")
    log.close()
    lp = os.path.join(tmp, "library.jsonl")
    check("library.jsonl 存在", os.path.exists(lp))
    lines = open(lp, "r", encoding="utf-8").read().strip().splitlines()
    check("有记录", len(lines) > 3, str(len(lines)))
    types = {json.loads(x)["type"] for x in lines}
    check("含 lib_put / lib_status", {"lib_put", "lib_status"} <= types, str(types))

    print("[8] 重放 == 内存")
    replayed = Library.replay(lines)
    mem = lib.dump()
    same = set(replayed) == set(mem)
    for k in mem:
        if k in replayed:
            if replayed[k]["status"] != mem[k]["status"]:
                same = False
            if replayed[k]["family"] != mem[k]["family"]:
                same = False
    check("条目集合与状态一致", same,
          f"replay={len(replayed)} mem={len(mem)}")

    print("[9] 计数")
    c = lib.counts()
    check("总数 = 100", sum(c.values()) == 100, str(c))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
