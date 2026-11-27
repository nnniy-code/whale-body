"""tests/test_sequence.py — 一句多动作的串接队列（v1.9）。

设计依据：v1.8 一次对话只能解析出一个 intent

怎么测：python tests/test_sequence.py
预期现象：[sequence] ALL PASS
调参位置：core/sequence.py（DEFAULT_DUR / MAX_ITEMS）
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.sequence import DEFAULT_DUR, MAX_ITEMS, ActionSequence  # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    print("[1] 装载返回第一个，按时间切下一个")
    seq = ActionSequence(default_dur=0.5)
    first = seq.load(["walk", "sit"], now=0.0)
    check("load 返回第一个", first == "walk", str(first))
    check("busy", seq.busy)
    check("current=walk", seq.current == "walk", str(seq.current))
    check("没到点不切", seq.poll(0.3) is None)
    second = seq.poll(0.5)
    check("到点切到第二个", second == "sit", str(second))
    check("播完清空", seq.poll(1.0) is None and not seq.busy)

    print("[2] 支持 dict 形式与自定义时长")
    seq = ActionSequence(default_dur=1.0)
    seq.load([{"intent": "wave", "dur_s": 0.6}, {"intent": "stretch", "dur_s": 0.9}], now=0.0)
    check("0.5 还没到（0.6 才切）", seq.poll(0.5) is None)
    got = seq.poll(0.6)
    check("0.6 切", got == "stretch", str(got))
    seq = ActionSequence(default_dur=1.0)
    seq.load([{"intent": "wave", "dur_s": 0.6}, {"intent": "stretch", "dur_s": 0.9}], now=0.0)
    seq.poll(0.6)
    check("第二段也按自己的时长", seq.poll(1.4) is None and seq.poll(1.5) is None)

    print("[3] 上限与下限")
    seq = ActionSequence(default_dur=1.0, max_items=3)
    seq.load([{"intent": f"a{i}"} for i in range(6)], now=0.0)
    check("最多 3 个", len(seq.items) == 3, str(len(seq.items)))
    check("常量一致", MAX_ITEMS == 3)
    seq = ActionSequence(default_dur=1.0, min_dur=0.5)
    seq.load([{"intent": "x", "dur_s": 0.01}], now=0.0)
    check("太短的被抬到下限", abs(seq.items[0][1] - 0.5) < 1e-9, str(seq.items[0][1]))

    print("[4] 空输入")
    seq = ActionSequence()
    check("空列表返回 None", seq.load([], now=0.0) is None)
    check("空列表不 busy", not seq.busy)
    check("poll 不炸", seq.poll(0.0) is None)
    check("过滤掉非法项", seq.load([None, "", {"intent": None}, "wave"], now=0.0) == "wave")

    print("[5] 单个动作也走队列（行为一致）")
    seq = ActionSequence(default_dur=1.0)
    check("load 单动作", seq.load(["idle"], now=5.0) == "idle")
    check("到点结束", seq.poll(6.0) is None)
    check("结束不 busy", not seq.busy)

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
