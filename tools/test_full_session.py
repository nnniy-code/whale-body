"""tools/test_full_session.py — 按真实用法做一次完整回归。

覆盖：
  1 连接 / 断开 / 重连
  2 纯聊天（无动作）
  3 单动作（挥手）
  4 多动作串接（走 → 坐）
  5 「她还在想的时候再发一句」
  6 做不到的动作
  7 长时空转（10 秒，检查漂移）
  8 断言：断开后姿态还原、迟到的结果不会在重连后突然播
  9 世界线写回（chat / mood）
 10 单帧耗时

跑法：
    blender_rt_headless(file=<blend>, script_file=tools/test_full_session.py)
"""

import importlib.util
import json
import os
import sys
import time
import traceback

import bpy

ROOT = r"D:\DSproject\body-v2"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
ADDON = os.path.join(ROOT, "blender_addon", "body_live.py")

from llm.paths import SANDBOX  # noqa: E402

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append({"name": name, "ok": bool(cond), "detail": str(detail)})


def basis_of(arm):
    return {pb.name: [list(r) for r in pb.matrix_basis] for pb in arm.pose.bones}


def max_diff(a, b):
    w = 0.0
    for k in a:
        if k in b:
            for i in range(4):
                for j in range(4):
                    w = max(w, abs(a[k][i][j] - b[k][i][j]))
    return w


def count_lines(p):
    try:
        return sum(1 for _ in open(p, encoding="utf-8"))
    except OSError:
        return 0


def settle(eng, timeout=30.0):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        eng.tick(1 / 30)
        time.sleep(1 / 90)
        if not eng._busy and not (eng._seq and eng._seq.busy):
            return True
    return False


def main():
    out = {"ok": False, "steps": []}
    spec = importlib.util.spec_from_file_location("body_live_dev", ADDON)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    eng = mod.ENGINE

    arm = max([o for o in bpy.data.objects if o.type == "ARMATURE"],
              key=lambda o: len(o.pose.bones))
    before = basis_of(arm)
    chat_path = os.path.join(SANDBOX, "chat.jsonl")
    mood_path = os.path.join(SANDBOX, "mood.json")
    chat0, mood0 = count_lines(chat_path), (json.load(open(mood_path, encoding="utf-8"))
                                            if os.path.exists(mood_path) else {})

    # ---- 1 连接 ----
    eng.connect()
    check("连接成功", eng.connected, eng.status)
    for _ in range(10):
        eng.tick(1 / 30)
        time.sleep(1 / 90)

    # ---- 2 纯聊天 ----
    eng.say("在吗")
    settle(eng)
    check("纯聊天不出动作", not (eng._seq and eng._seq.busy), eng.status)
    check("她回了话", bool(eng.last_say), repr(eng.last_say))

    # ---- 3 单动作 ----
    # 先把 API 这次解析出什么记下来（信息用，不当作断言——模型是概率的）
    eng.say("挥挥手跟我打个招呼")
    settle(eng)
    out["wave_api"] = {"say": eng.last_say, "intent": eng.last_intent}

    # 姿势用**确定性注入**验，不赌模型每次都解析对
    eng.rt.handle_composite([{"intent": "wave"}], now=eng.loop.t)
    for _ in range(12):
        eng.tick(1 / 30)
    bpy.context.view_layer.update()
    hz = float(arm.pose.bones["rightHand"].matrix.translation.z)
    hd = float(arm.pose.bones["head"].matrix.translation.z)
    check("挥手：手高过头", hz > hd, f"hand {hz:.3f} vs head {hd:.3f}")

    # ---- 5 忙的时候再发一句 ----
    eng.say("先走过来，然后坐下歇会儿")
    again = eng.say("喂？")
    check("忙时拒绝并给提示", "想" in str(again), repr(again))
    settle(eng)
    out["seq_api"] = {"say": eng.last_say, "intents": eng.last_intent}

    # 串接 + 坐下的姿势：确定性验（走 → 停 → 坐）
    eng.rt.handle_composite([{"intent": "walk"}], now=eng.loop.t)
    for _ in range(20):
        eng.tick(1 / 30)
    y_walk = float(eng.rt.body.y)
    check("走路会前进", abs(y_walk) > 0.1, f"{y_walk:.3f}")
    eng.rt.handle_composite([{"intent": "sit"}], now=eng.loop.t)
    for _ in range(20):
        eng.tick(1 / 30)
    bpy.context.view_layer.update()
    hips_sit = float(arm.pose.bones["hips"].matrix.translation.z)
    y_after = float(eng.rt.body.y)
    check("坐下：髋低于站立（0.529）", hips_sit < 0.45, f"{hips_sit:.3f}")
    check("坐下后不再前进", abs(y_after - y_walk) < 1e-6, f"{y_walk:.4f} → {y_after:.4f}")

    # ---- 7 长时空转，检查漂移 ----
    for _ in range(300):                     # 10 秒 @30fps
        eng.tick(1 / 30)
        time.sleep(1 / 300)
    y_idle = float(eng.rt.body.y)
    check("空转 10 秒不漂移", abs(y_idle - y_after) < 1e-6, f"{y_after:.4f} → {y_idle:.4f}")

    # ---- 6 做不到的动作 ----
    eng.say("给我翻个跟头")
    settle(eng)
    out["impossible"] = {"say": eng.last_say, "intents": eng.last_intent}

    # ---- 6b 谱系：趴下要真的矮下去；spin 要真的转 ----
    eng.rt.handle_composite([{"intent": "lie_down"}], now=eng.loop.t)
    for _ in range(20):
        eng.tick(1 / 30)
    bpy.context.view_layer.update()
    hips_lie = float(arm.pose.bones["hips"].matrix.translation.z)
    check("趴下：髋低于站立（<0.45）", hips_lie < 0.45, f"{hips_lie:.3f}")

    yaw0 = float(eng.rt.body.yaw)
    eng.rt.handle_composite([{"intent": "spin"}], now=eng.loop.t)
    for _ in range(36):
        eng.tick(1 / 30)
    yaw1 = float(eng.rt.body.yaw)
    check("spin：转了（>30°）", abs(yaw1 - yaw0) > 30, f"{yaw0:.0f}° → {yaw1:.0f}°")

    # ---- 8a 断开还原 ----
    eng.disconnect()
    after = basis_of(arm)
    check("断开后姿态还原", max_diff(before, after) < 1e-6, f"{max_diff(before, after):.2e}")

    # ---- 8b 迟到结果不能在重连后突然播 ----
    eng.connect()
    eng._q.put((eng._gen - 1, "ok", "旧的",
                {"say": "这条是断开前就该丢掉的", "intent": "wave",
                 "intents": [{"intent": "wave"}], "mood_delta": None}))
    for _ in range(5):
        eng.tick(1 / 30)
    check("断开期间的迟到结果被丢弃", not (eng._seq and eng._seq.busy),
          f"seq={eng._seq.snapshot() if eng._seq else None}")

    # ---- 9 世界线写回 ----
    chat1 = count_lines(chat_path)
    mood1 = json.load(open(mood_path, encoding="utf-8")) if os.path.exists(mood_path) else {}
    check("chat.jsonl 有新增", chat1 > chat0, f"{chat0} → {chat1}")
    check("mood.json 有更新", mood1.get("updated") != mood0.get("updated"),
          f"{mood0.get('updated')} → {mood1.get('updated')}")

    # ---- 10 单帧耗时 ----
    t0 = time.perf_counter()
    N = 60
    for _ in range(N):
        eng.tick(1 / 30)
    ms = (time.perf_counter() - t0) * 1000 / N
    check("单帧 < 16ms（60fps 有余量）", ms < 16.0, f"{ms:.1f}ms/帧")

    eng.disconnect()
    out["steps"] = CHECKS
    out["ok"] = all(c["ok"] for c in CHECKS)
    out["failed"] = [c["name"] for c in CHECKS if not c["ok"]]
    print("HEADLESS " + json.dumps(out, ensure_ascii=False))


try:
    main()
except Exception:
    print("HEADLESS " + json.dumps({"ok": False, "error": traceback.format_exc()[-800:],
                                    "steps": CHECKS}, ensure_ascii=False))
