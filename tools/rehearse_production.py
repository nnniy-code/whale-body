"""tools/rehearse_production.py — 在沙箱里演习「接正版」。

做法：把正版的**真实文件**复制到 `sandbox/prod_sim/`，按正版的目录形态摆好：

    sandbox/prod_sim/            ← BODY_V2_BASE（人格/记忆）
        persona.md  memory.md  self.md
        world/                   ← BODY_V2_WORLD（情绪/日志/身体状态）
            mood.json  memory.json  emotion.json  body.json
            chat.jsonl  body_lib.json  pose_consumer_state.json  body_state.json

然后用 `BODY_V2_BASE` / `BODY_V2_WORLD` 把引擎指过去跑会话，
最后检查三件事：

  1 该改的改了（mood.json 的 updated、chat.jsonl 增长）
  2 **不该碰的一个字节都没动**（memory.json / body_lib.json / pose_consumer_state.json）
  3 **正版目录完全没被碰**（md5 前后一致）

正版只读不写，副本坏了重跑就恢复。
"""

import hashlib
import itertools
import json
import os
import shutil
import sys
import time
import traceback

import bpy

ROOT = r"D:\DSproject\body-v2"
PROD = r"D:\DSproject\dsh-qq"
SIM = os.path.join(ROOT, "sandbox", "prod_sim")
SIM_WORLD = os.path.join(SIM, "world")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def md5(p):
    try:
        return hashlib.md5(open(p, "rb").read()).hexdigest()
    except OSError:
        return "MISSING"


def tail_lines(src, dst, n=200):
    try:
        lines = open(src, encoding="utf-8", errors="ignore").read().splitlines()[-n:]
        with open(dst, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def build_sim():
    if os.path.isdir(SIM):
        shutil.rmtree(SIM)
    os.makedirs(SIM_WORLD)
    pairs = [
        (os.path.join(PROD, "persona.md"), os.path.join(SIM, "persona.md")),
        (os.path.join(PROD, "memory.md"), os.path.join(SIM, "memory.md")),
        (os.path.join(PROD, "world", "self.md"), os.path.join(SIM, "self.md")),
    ]
    for name in ("mood.json", "memory.json", "emotion.json", "body.json",
                 "body_lib.json", "pose_consumer_state.json", "body_state.json"):
        pairs.append((os.path.join(PROD, "world", name), os.path.join(SIM_WORLD, name)))
    for src, dst in pairs:
        if os.path.exists(src):
            shutil.copy2(src, dst)
    tail_lines(os.path.join(PROD, "world", "chat.jsonl"),
               os.path.join(SIM_WORLD, "chat.jsonl"))


def main():
    out = {"ok": False}
    build_sim()

    # 正版侧的指纹（演习全程不该变）
    prod_files = [os.path.join(PROD, "world", n) for n in
                  ("mood.json", "chat.jsonl", "memory.json", "body.json")]
    prod_before = {p: md5(p) for p in prod_files}

    # 副本里「不该被改」的文件（body.json 现在也不能碰：正版拿它判身体在线）
    frozen = {n: md5(os.path.join(SIM_WORLD, n)) for n in
              ("memory.json", "body_lib.json", "pose_consumer_state.json",
               "body_state.json", "body.json")}
    body_json_mtime = os.path.getmtime(os.path.join(SIM_WORLD, "body.json"))

    # 必须在 import 引擎**之前**设好
    os.environ["BODY_V2_BASE"] = SIM
    os.environ["BODY_V2_WORLD"] = SIM_WORLD

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "body_live_prod", os.path.join(ROOT, "blender_addon", "body_live.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    eng = mod.ENGINE

    from llm import paths
    out["layout"] = {"base": paths.BASE, "world": paths.WORLD}
    out["is_production"] = paths.is_production(paths.WORLD)
    out["assert_writable_ok"] = True

    chat0 = sum(1 for _ in open(os.path.join(SIM_WORLD, "chat.jsonl"), encoding="utf-8"))
    mood0 = json.load(open(os.path.join(SIM_WORLD, "mood.json"), encoding="utf-8"))

    eng.connect()
    out["connected"] = eng.connected
    for _ in range(10):
        eng.tick(1 / 30)
        time.sleep(1 / 90)

    from llm.persona import body_connected, load_memory, load_mood
    out["memory_prompt"] = load_memory()[:400]
    out["mood_before"] = {"energy": mood0.get("energy"), "updated": mood0.get("updated")}
    out["body_connected_while_live"] = bool(body_connected())
    # 心跳写的是我们自己的文件，不能是 body.json
    out["heartbeat_file"] = os.path.basename(paths.heartbeat_path())
    out["heartbeat_exists"] = os.path.exists(paths.heartbeat_path())
    out["body_json_mtime_unchanged"] = (
        abs(os.path.getmtime(os.path.join(SIM_WORLD, "body.json")) - body_json_mtime) < 1e-6)

    for text in ("在吗", "今天有点累"):
        eng.say(text)
        t0 = time.perf_counter()
        while eng._busy and time.perf_counter() - t0 < 30:
            eng.tick(1 / 30)
            time.sleep(1 / 90)
        for _ in range(12):
            eng.tick(1 / 30)
    out["say"] = eng.last_say

    eng.disconnect()

    # 1 该改的改了
    chat1 = sum(1 for _ in open(os.path.join(SIM_WORLD, "chat.jsonl"), encoding="utf-8"))
    mood1 = load_mood()
    out["chat"] = f"{chat0} → {chat1}"
    out["mood_after"] = {"energy": mood1.get("energy"), "updated": mood1.get("updated")}
    out["chat_grew"] = chat1 > chat0
    out["mood_changed"] = mood1.get("updated") != mood0.get("updated")

    # 2 冻结文件没被动
    changed = [n for n, h in frozen.items() if md5(os.path.join(SIM_WORLD, n)) != h]
    out["frozen_changed"] = changed

    # 3 正版一个字节没动
    prod_after = {p: md5(p) for p in prod_files}
    out["production_touched"] = [os.path.basename(p) for p in prod_files
                                 if prod_before[p] != prod_after[p]]

    # 4 世界线模式：正版只读必须拦住；可写必须靠备份换
    paths.set_world("production")
    out["mode_production_readonly_blocked"] = not paths.writable(
        os.path.join(paths.PRODUCTION_WORLD, "mood.json"))
    try:
        paths.set_world("production_rw")
        out["mode_rw_after_backup"] = paths.writable(
            os.path.join(paths.PRODUCTION_WORLD, "mood.json"))
    except PermissionError:
        out["mode_rw_after_backup"] = "需要备份"
    paths.set_world("sandbox")
    out["mode_back_to_sandbox"] = (paths.MODE, paths.WORLD == paths.SANDBOX_DIR)

    # 5 如果直接把写入指到正版，护栏必须拦住
    try:
        from llm.world import update_mood
        update_mood(paths.PRODUCTION_WORLD, {"energy": 0})
        out["prod_write_blocked"] = False
    except PermissionError:
        out["prod_write_blocked"] = True
    except Exception as e:
        out["prod_write_blocked"] = False
        out["prod_write_error"] = repr(e)[:120]

    out["ok"] = bool(out["chat_grew"] and out["mood_changed"]
                     and not changed and not out["production_touched"]
                     and out["connected"] and out["prod_write_blocked"]
                     and out["heartbeat_exists"] and out["body_json_mtime_unchanged"])
    print("HEADLESS " + json.dumps(out, ensure_ascii=False))


try:
    main()
except Exception:
    print("HEADLESS " + json.dumps({"ok": False, "error": traceback.format_exc()[-900:]},
                                   ensure_ascii=False))
