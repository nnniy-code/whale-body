"""tests/test_dispatcher.py — P5 验收（纯逻辑，不碰 Blender）。

怎么测：python tests/test_dispatcher.py
预期现象：[dispatcher] ALL PASS
调参位置：core/dispatcher.py 的 GRACE_S
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.dispatcher import GRACE_S, Dispatcher  # noqa: E402
from core.library import Library, intent_key  # noqa: E402
from core.player import Player  # noqa: E402

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    fams = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                       "config", "seed_families.json"), encoding="utf-8"))["families"]
    lib = Library()
    player = Player()
    d = Dispatcher(lib, fams, player)

    k_wave = intent_key([{"name": "hand_above_head", "args": {"side": "right"}}], "wave")
    k_walk = intent_key([{"name": "foot_grounded", "args": {"side": "left"}}], "walk")

    print("[1] 空库 → idle")
    r = d.on_act({"intent": "wave", "intent_key": k_wave}, now=0.0)
    check("action=idle", r.action == "idle", r.action)

    print("[2] 命中 → play（L1）")
    e = lib.put("wave", k_wave, {"P": fams["wave"]["P_default"], "G": fams["wave"]["G"]}, dur_s=1.0)
    r = d.on_act({"intent": "wave", "intent_key": k_wave}, now=1.0)
    check("action=play", r.action == "play")
    check("source=L1", r.source == "L1", r.source)
    check("命中条目", r.entry_id == e.id)
    check("state=PLAYING", d.state == "PLAYING")

    print("[3] 非循环动作执行过半 → 打断（进队列 + 回话）")
    r = d.on_act({"intent": "walk", "intent_key": k_walk}, now=1.2)
    check("action=deferred", r.action == "deferred", r.action)
    check("有回话", r.said is not None, str(r.said))
    check("进队列", len(d.queue) == 1, str(len(d.queue)))
    check("deferred_count=1", d.deferred_count == 1)

    print("[4] 间隔超过 grace 后可接受")
    r = d.on_act({"intent": "walk", "intent_key": k_walk}, now=1.0 + GRACE_S + 0.05)
    check("不再 deferred", r.action != "deferred", r.action)

    print("[5] 周期动作随时可接续")
    lib.put("walk", k_walk, {"P": fams["walk"]["P_default"], "G": fams["walk"]["G"]}, dur_s=3.0)
    d.on_act({"intent": "walk", "intent_key": k_walk}, now=10.0)
    check("walk 在播", player.state is not None and player.state.loop)
    r = d.on_act({"intent": "wave", "intent_key": k_wave}, now=10.5)
    check("walk 期间新意图不被拒", r.action == "play", r.action)

    print("[6] 未命中但有近邻 → fallback")
    # 近邻 = 只差数值容差；**左右不同不再算近邻**（side 是硬区分，见 core/library.key_distance）
    k_weird = intent_key([{"name": "hand_above_head",
                           "args": {"side": "right", "tol_mm": 120}}], "wave")
    lib.entries.clear()
    lib.put("wave", k_wave, {"P": fams["wave"]["P_default"], "G": fams["wave"]["G"]}, dur_s=1.0)
    r = d.on_act({"intent": "weird", "intent_key": k_weird}, now=20.0)
    check("有动作可播", r.action in ("play", "deferred"), r.action)

    print("[7] 动作结束回 IDLE")
    p2 = Player()
    d2 = Dispatcher(Library(), fams, p2)
    lib2 = d2.library
    lib2.put("wave", k_wave, {"P": {}, "G": {}}, dur_s=0.5)
    d2.on_act({"intent": "wave", "intent_key": k_wave}, now=0.0)
    d2.tick(0.1)
    check("播放中", d2.state == "PLAYING")
    info = d2.tick(0.7)
    check("结束回 IDLE", d2.state == "IDLE", d2.state)
    check("finished 标记", info["finished"] is True)

    print("[8] 队列里的动作会被执行")
    p3 = Player()
    d3 = Dispatcher(Library(), fams, p3)
    d3.library.put("wave", k_wave, {"P": {}, "G": {}}, dur_s=2.0)
    d3.on_act({"intent": "wave", "intent_key": k_wave}, now=0.0)
    d3.on_act({"intent": "wave", "intent_key": k_wave}, now=0.1)
    check("进了队列", len(d3.queue) == 1)
    d3.tick(2.1)
    check("队列被消费", len(d3.queue) == 0, str(len(d3.queue)))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())


