"""tools/simulate_session.py — 像用户那样用面板：连上，说几句，抓帧，断开。

跑法：

    blender_rt_headless(file=<blend>, script_file=tools/simulate_session.py,
                        args="--render 1")

做的事（和面板里点按钮完全同一个引擎）：
  1. Connect（起引擎、心跳）
  2. 连续说几句话，每句等结果 → 播动作
  3. 其中一句会触发**多动作串接**，在 walk 和 sit 各抓一帧
  4. 测一次「她还在想的时候再发一句」会怎样
  5. Disconnect（还原姿态）

不改项目文件，不存 .blend。
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
OUTDIR = r"C:\Users\LENOVO\AppData\Local\dsh-blender-rt\session"

LINES = [
    "在吗",
    "挥挥手跟我打个招呼",
    "先走过来，然后坐下歇会儿",
    "翻个跟头给我看看",
    "那就歇着吧，别忙了",
]


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


def setup_cam():
    scene = bpy.context.scene
    cam = bpy.data.objects.get("SESSION_CAM")
    if cam is None:
        cd = bpy.data.cameras.new("SESSION_CAM")
        cam = bpy.data.objects.new("SESSION_CAM", cd)
        scene.collection.objects.link(cam)
    loc = (1.35, -2.15, 1.15)
    tgt = (0.0, -0.35, 0.82)
    cam.location = loc
    from mathutils import Vector
    d = Vector((tgt[0] - loc[0], tgt[1] - loc[1], tgt[2] - loc[2]))
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = 62
    scene.camera = cam
    scene.render.resolution_x = 640
    scene.render.resolution_y = 640
    scene.render.image_settings.file_format = "PNG"
    try:
        scene.eevee.taa_render_samples = 16
    except Exception:
        pass


def shoot(name):
    p = os.path.join(OUTDIR, name + ".png")
    bpy.context.scene.render.filepath = p
    bpy.ops.render.render(write_still=True)
    return p


def tick_until(eng, cond, timeout=25.0, dt=1 / 30):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        eng.tick(dt)
        time.sleep(1 / 90)
        if cond():
            return True
    return False


def main():
    do_render = arg("--render", "1") == "1"
    out = {"ok": False, "turns": [], "images": []}
    mod = load_engine()
    eng = mod.ENGINE

    if do_render:
        os.makedirs(OUTDIR, exist_ok=True)
        setup_cam()

    t0 = time.perf_counter()
    eng.connect()
    out["connect_ms"] = round((time.perf_counter() - t0) * 1000)
    out["status_after_connect"] = eng.status

    # 先跑 20 帧让她呼吸
    for _ in range(20):
        eng.tick(1 / 30)
        time.sleep(1 / 90)

    arm = eng.arm
    for i, text in enumerate(LINES):
        rec = {"i": i, "ask": text}
        t1 = time.perf_counter()
        eng.say(text)
        # 「还在想的时候再发一句」：只在第一句测一次
        if i == 0:
            rec["second_say_while_busy"] = eng.say("喂？")
        ok = tick_until(eng, lambda: not eng._busy, timeout=30)
        rec["got_result"] = ok
        rec["ms"] = round((time.perf_counter() - t1) * 1000)
        rec["say"] = eng.last_say
        rec["intents"] = [{"intent": n, "dur_s": d} for n, d in (eng._seq.items if eng._seq else [])]
        rec["status"] = eng.status

        # 多动作：先把第一段（walk）抓下来，播完再抓最后一段（sit）
        if do_render and len(rec["intents"]) > 1:
            rec["img_start"] = shoot(f"s{i}_a")
            out["images"].append(rec["img_start"])

        # 让动作播完（最多 5 秒）
        tick_until(eng, lambda: not (eng._seq and eng._seq.busy), timeout=6)
        bpy.context.view_layer.update()
        rec["hips_z"] = round(float(arm.pose.bones["hips"].matrix.translation.z), 3)
        rec["hand_z"] = round(float(arm.pose.bones["rightHand"].matrix.translation.z), 3)
        rec["body"] = eng.rt.body.as_dict() if eng.rt else None

        if do_render and rec["intents"]:
            rec["img"] = shoot(f"s{i}")
            out["images"].append(rec["img"])
        out["turns"].append(rec)
        print(f"[{i}] {text} -> {rec['say']!r} intents={rec['intents']}", flush=True)

    eng.disconnect()
    out["status_after_disconnect"] = eng.status
    out["frames"] = eng.loop.frames if eng.loop else 0
    out["ok"] = bool(all(t["got_result"] for t in out["turns"]))
    print("HEADLESS " + json.dumps(out, ensure_ascii=False))


main()
