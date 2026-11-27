"""tools/test_body_live.py — 无头验证 Blender 常驻面板的引擎（不碰 UI）。

跑法：

    blender_rt_headless(file=<blend>, script_file=tools/test_body_live.py,
                        args="--say 挥挥手跟我打个招呼")

验证四件事：
  1. Connect 之后骨骼开始动（呼吸）
  2. 心跳把 body_connected() 变成 True
  3. 说一句 → 后台线程拿回动作 → 手抬起来
  4. Disconnect 之后骨骼**逐骨回到连接前**（用户的场景不能被改掉）

不注册 UI，也不写用户场景。
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


def arg(name, default):
    if "--" not in sys.argv:
        return default
    a = sys.argv[sys.argv.index("--") + 1:]
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default


def load_engine():
    spec = importlib.util.spec_from_file_location("body_live_dev", ADDON)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def basis_of(arm):
    return {pb.name: [list(r) for r in pb.matrix_basis] for pb in arm.pose.bones}


def max_diff(a, b):
    worst = 0.0
    for k in a:
        if k not in b:
            continue
        for i in range(4):
            for j in range(4):
                worst = max(worst, abs(a[k][i][j] - b[k][i][j]))
    return worst


def main():
    out = {"ok": False}
    mod = load_engine()
    eng = mod.ENGINE

    arm = max([o for o in bpy.data.objects if o.type == "ARMATURE"],
              key=lambda o: len(o.pose.bones))
    before = basis_of(arm)
    out["armature"] = arm.name
    out["bones"] = len(arm.pose.bones)

    # 1) 连接
    t0 = time.perf_counter()
    eng.connect()
    out["connect_ms"] = round((time.perf_counter() - t0) * 1000)
    out["status_after_connect"] = eng.status

    # 2) 空跑 30 帧（呼吸）
    for _ in range(30):
        eng.tick(1 / 30)
        time.sleep(1 / 90)
    mid = basis_of(arm)
    out["breath_moved"] = round(max_diff(before, mid), 6)

    # 3) 心跳
    from llm.persona import body_connected
    out["body_connected"] = bool(body_connected())

    # 4) 说一句
    text = arg("--say", "挥挥手跟我打个招呼")
    eng.say(text)
    t1 = time.perf_counter()
    while eng._busy and (time.perf_counter() - t1) < 25:
        eng.tick(1 / 30)
        time.sleep(1 / 90)
    out["say_text"] = text
    out["say"] = eng.last_say
    out["intent"] = eng.last_intent
    out["say_ms"] = round((time.perf_counter() - t1) * 1000)

    # 抬没抬手：直接读 Blender 里的骨骼
    bpy.context.view_layer.update()
    hz = arm.pose.bones["rightHand"].matrix.translation.z
    hd = arm.pose.bones["head"].matrix.translation.z
    out["hand_z"] = round(float(hz), 3)
    out["head_z"] = round(float(hd), 3)
    out["hand_up"] = bool(float(hz) > float(hd))

    # 5b) 多动作串接：直接喂一个两段结果（不走 API），看它是不是按顺序播
    eng._apply("x", {"say": "走过去坐下", "intent": "walk",
                     "intents": [{"intent": "walk", "dur_s": 0.4},
                                 {"intent": "sit", "dur_s": 0.6}],
                     "mood_delta": None})
    bpy.context.view_layer.update()
    hips_walk = float(arm.pose.bones["hips"].matrix.translation.z)
    for _ in range(20):                      # ~0.67s > 0.4s，应该切到 sit
        eng.tick(1 / 30)
        time.sleep(1 / 90)
    bpy.context.view_layer.update()
    hips_sit = float(arm.pose.bones["hips"].matrix.translation.z)
    for _ in range(40):                      # 播完
        eng.tick(1 / 30)
        time.sleep(1 / 90)
    out["seq_busy_after"] = bool(eng._seq.busy if eng._seq else True)
    out["hips_walk"] = round(hips_walk, 3)
    out["hips_sit"] = round(hips_sit, 3)
    out["seq_worked"] = bool(hips_sit < hips_walk - 0.05 and not out["seq_busy_after"])

    # 5) 断开并还原
    eng.disconnect()
    after = basis_of(arm)
    out["restore_diff"] = round(max_diff(before, after), 9)
    out["status_after_disconnect"] = eng.status
    out["frames"] = eng.loop.frames if eng.loop else 0

    out["ok"] = bool(out["breath_moved"] > 1e-6 and out["restore_diff"] < 1e-6
                     and out["body_connected"] and out.get("seq_worked"))

    # 6) UI 层：注册 → 面板块存在 → 两个按钮能真的连/断
    try:
        mod.register()
        out["ui_panel"] = hasattr(bpy.types, "BODY_PT_panel")
        out["ui_scene_prop"] = hasattr(bpy.types.Scene, "body_live_text")
        out["ui_connect_op"] = hasattr(bpy.ops.body_live, "connect")
        bpy.ops.body_live.connect()
        out["ui_connected"] = bool(eng.connected)
        bpy.ops.body_live.disconnect()
        out["ui_disconnected"] = bool(not eng.connected)
        mod.unregister()
        out["ui_unregister_ok"] = True
        out["ok"] = out["ok"] and out["ui_panel"] and out["ui_connected"] and out["ui_disconnected"]
    except Exception:
        out["ui_error"] = traceback.format_exc(limit=2)[-200:]

    print("HEADLESS " + json.dumps(out, ensure_ascii=False))


main()
