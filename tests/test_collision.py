"""tests/test_collision.py — P8 归因逻辑验收（纯计算）。

怎么测：python tests/test_collision.py
预期现象：[collision] ALL PASS
调参位置：config/collision.json
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.collision import (  # noqa: E402
    attribute, guilty_bones, is_violation, load_table, lookup, pair_key, penalty, summarize,
)

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    t = load_table()

    print("[1] 键规范化（对称）")
    check("(arm,head) == (head,arm)", pair_key("arm", "head") == pair_key("head", "arm"))
    check("按字母序", pair_key("head", "arm") == "arm|head")

    print("[2] 查表")
    ab = lookup(t, "arm", "body")[0]; check("arm|body 阈值可读", ab > 0, f"{ab}mm")
    check("head|hair mode=delta", lookup(t, "head", "hair")[1] == "delta")
    check("未列出的走 default 2mm", lookup(t, "ribbon", "tail")[0] == 2.0)

    print("[3] 违规判定")
    ok, over = is_violation(t, "arm", "body", 3.0)
    check("3mm 贴胸不违规", not ok, f"over={over}")
    ok, over = is_violation(t, "arm", "body", float(ab) + 8.0)
    check("超阈值违规", ok and over > 0, f"over={over}")
    ok, _ = is_violation(t, "arm", "head", float(lookup(t,"arm","head")[0]) + 5.0)
    check("手臂插脸判违规", ok)

    print("[4] hair|head 的 delta 模式")
    ok, _ = is_violation(t, "head", "hair", 5.0)
    check("无 baseline 时 5mm 违规", ok)
    t2 = dict(t)
    t2["baseline"] = {"head|hair": 4.0}
    ok, over = is_violation(t2, "head", "hair", 5.0)
    check("有 4mm baseline 时 5mm 不违规", not ok, f"over={over}")
    ok, _ = is_violation(t2, "head", "hair", 9.0)
    check("9mm（超 baseline 5mm）违规", ok)

    print("[5] 归因给动得多的那根")
    bone_map = {0: "leftHand", 1: "chest", 2: "WhaleTail_03", 3: "leftUpperLeg"}
    region = {"leftHand": "arm", "chest": "body", "WhaleTail_03": "tail",
              "leftUpperLeg": "leg"}
    move = {"leftHand": 0.05, "chest": 0.001, "WhaleTail_03": 0.20, "leftUpperLeg": 0.01}
    pairs = [(0, 1, 60.0), (2, 3, 60.0)]
    out = attribute(pairs, bone_map.get, region.get, t, frame=7, move_amount=move.get)
    check("两条都判违规", len(out) == 2, str(len(out)))
    if len(out) == 2:
        check("手贴胸归因到手", out[0]["bone"] == "leftHand", out[0]["bone"])
        check("尾巴插腿归因到尾巴", out[1]["bone"] == "WhaleTail_03", out[1]["bone"])
        check("带 frame", out[0]["frame"] == 7)
        check("带 over_mm", out[0]["over_mm"] > 0, str(out[0]["over_mm"]))

    print("[6] 聚合与罚项")
    s = summarize(out)
    check("count=2", s["count"] == 2)
    check("worst 是尾巴", s["worst_bone"] == "WhaleTail_03", str(s["worst_bone"]))
    p = penalty(out, scale_mm=10.0)
    check("罚项 > 0", p > 0, f"{p:.3f}")
    check("guilty 两根", len(guilty_bones(out)) == 2, str(guilty_bones(out)))

    print("[7] 空输入")
    check("无违规时罚项 0", penalty([]) == 0.0)
    check("summarize 空", summarize([])["count"] == 0)

    print("[8] 阈值表覆盖")
    n = len(t.get("pairs", {}))
    check("至少 19 对显式", n >= 19, str(n))
    check("有 default", "default" in t)

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())


