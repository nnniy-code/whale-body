"""tools/storyboard.py — 多轮对话 → 逐轮姿态 → 渲染成片（在 Blender 里跑）。

用途两件：
  1. **视觉验证**：把姿态真的渲出来，用眼睛（或 VLM）判断「像不像」
  2. **模拟对话**：用真 API + 沙箱人格跑一段多轮，看每轮她说什么、做什么

跑法：

    blender_rt_headless(file=<blend>, script_file=tools/storyboard.py,
                        args="--apply 1 --render 1")

参数（`--` 之后）：
    --lines "a||b||c"   用 || 分隔的句子；省略则用内置 4 句
    --apply 1|0         是否写骨（默认 1）
    --render 1|0        是否出图（默认 1）
    --out <dir>         图片目录（默认 blender-rt 产物目录）
    --history 1|0       是否带对话历史（默认 1）

每轮产出：一句话 + 解析出的族 + 姿态指标 + 一张图。
"""

import json
import os
import sys
import time

import bpy
import numpy as np

ROOT = r"D:\DSproject\body-v2"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402
from llm.client import DeepSeek, find_api_key, redact  # noqa: E402
from llm.persona import load_memory, load_mood, load_persona, turn  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]
DEFAULT_LINES = ["在吗", "挥挥手跟我打个招呼", "过来让我看看你", "累了，坐下歇会儿"]


def arg(name, default):
    if "--" not in sys.argv:
        return default
    a = sys.argv[sys.argv.index("--") + 1:]
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else default


def build_runtime():
    rigd = json.load(open(os.path.join(ROOT, "tests/fixtures/rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    rf = rig.subset(ONLY)
    bm = BoneMap.load(os.path.join(ROOT, "config/bones_map.json"))
    anch = json.load(open(os.path.join(ROOT, "config/anchors.json"), encoding="utf-8"))
    fams = json.load(open(os.path.join(ROOT, "config/seed_families.json"), encoding="utf-8"))["families"]
    caps = json.load(open(os.path.join(ROOT, "config/capsules.json"), encoding="utf-8"))["bones"]
    tab = load_table()
    bmj = json.load(open(os.path.join(ROOT, "config/bones_map.json"), encoding="utf-8"))["bones"]
    L = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, L, chain_skip=3, only=ONLY)
    rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps, collision_table=tab,
                 lengths=L, capsule_baseline=base, capsule_only=ONLY)
    return rt, rig


def setup_cam():
    scene = bpy.context.scene
    cam = bpy.data.objects.get("STORY_CAM")
    if cam is None:
        cd = bpy.data.cameras.new("STORY_CAM")
        cam = bpy.data.objects.new("STORY_CAM", cd)
        scene.collection.objects.link(cam)
    loc = np.array([1.35, -2.15, 1.15])
    tgt = np.array([0.0, -0.35, 0.82])
    cam.location = tuple(loc)
    from mathutils import Vector
    d = Vector(tuple(tgt - loc))
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    cam.data.lens = 62
    scene.camera = cam
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in {
        e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items
    } else "BLENDER_EEVEE"
    scene.render.resolution_x = 640
    scene.render.resolution_y = 640
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    try:
        scene.eevee.taa_render_samples = 16
    except Exception:
        pass
    return cam


def metrics(rig, basis):
    M = rig.compute(basis)
    out = {
        "hand_z": round(float(M["rightHand"][2, 3]), 3),
        "head_z": round(float(M["head"][2, 3]), 3),
        "hands_above_head": bool(float(M["rightHand"][2, 3]) > float(M["head"][2, 3])),
        "foot_z_min": round(min(float(M["leftFoot"][2, 3]), float(M["rightFoot"][2, 3])), 3),
        "foot_z_left": round(float(M["leftFoot"][2, 3]), 3),
        "foot_z_right": round(float(M["rightFoot"][2, 3]), 3),
        "hips_z": round(float(M["hips"][2, 3]), 3),
    }
    # 肘别反折：手臂末端到肩的距离不应超过臂长
    sh = np.asarray(M["rightShoulder"])[0:3, 3]
    el = np.asarray(M["rightLowerArm"])[0:3, 3]
    hd = np.asarray(M["rightHand"])[0:3, 3]
    out["arm_len_mm"] = round(float(np.linalg.norm(el - sh) + np.linalg.norm(hd - el)) * 1000, 1)
    return out


def main():
    lines = arg("--lines", "")
    texts = [x.strip() for x in lines.split("||")] if lines else DEFAULT_LINES
    apply_ = arg("--apply", "1") == "1"
    render = arg("--render", "1") == "1"
    use_hist = arg("--history", "1") == "1"
    persist = arg("--persist", "0") == "1"
    write_mem = arg("--memory", "0") == "1"
    outdir = arg("--out", r"C:\Users\LENOVO\AppData\Local\dsh-blender-rt\story")

    key = find_api_key()
    out = {"ok": True, "has_key": bool(key), "turns": [], "images": []}
    if not key:
        print("HEADLESS " + json.dumps({"ok": False, "error": "no key"}, ensure_ascii=False))
        return

    chat = DeepSeek(key, temperature=1.15, max_tokens=300, timeout_s=60)
    parser = DeepSeek(key, temperature=0.1, max_tokens=300, timeout_s=60)

    rt, rig = build_runtime()
    caps = rt.capabilities()
    persona, memory, mood = load_persona(), load_memory(), load_mood()
    arm = max([o for o in bpy.data.objects if o.type == "ARMATURE"],
              key=lambda o: len(o.pose.bones)) if apply_ else None
    if render:
        os.makedirs(outdir, exist_ok=True)
        setup_cam()

    history = []
    for i, text in enumerate(texts):
        t0 = time.perf_counter()
        try:
            r = turn(text, chat, caps, persona, memory, mood, body=True,
                     parser_llm=parser, history=(history if use_hist else None))
        except Exception as e:
            out["ok"] = False
            out["error"] = redact(str(e))[:300]
            break
        rec = {"i": i, "ask": text, "say": r.get("say"), "intent": r.get("intent"),
               "intents": r.get("intents") or [],
               "mood_delta": r.get("mood_delta"),
               "ms": round((time.perf_counter() - t0) * 1000)}
        intent = r.get("intent")
        from llm.persona import acts_from_turn
        acts = acts_from_turn(r)
        if acts:
            act = dict(acts[0])
            act["say"] = r.get("say")
            act["mood"] = r.get("mood_delta") or mood
            res = rt.handle_intent(act)
            rec["source"] = res.get("source")
            rec["gap"] = (res.get("capability_gap") or {}).get("kind")
            if res.get("entry"):
                e = res["entry"]
                side = (e.metrics or {}).get("side")
                P = res.get("play_P") or e.params.get("P", {})
                basis, diag = rt.build_full(P, e.family, rt.t, active_side=side)
                rec["pose"] = metrics(rig, basis)
                rec["ik_err_mm"] = max(diag["err_mm"].values()) if diag.get("err_mm") else None
                if arm is not None:
                    from core.binder import apply_basis
                    apply_basis(arm, basis)
                    bpy.context.view_layer.update()
        out["turns"].append(rec)

        # 写回世界线（只写 sandbox/，越界会抛）
        if persist:
            from llm.paths import SANDBOX
            from llm.world import persist_turn
            rec["persist"] = persist_turn(
                SANDBOX, text, r.get("say") or "", r.get("intent"),
                r.get("mood_delta"),
                memory_line=(f"{text} → 我说：{(r.get('say') or '')[:40]}"
                             if write_mem else None))
            out["persisted"] = True
        history.append({"role": "user", "text": text})
        history.append({"role": "她", "text": (r.get("say") or "").replace("\n", " ")[:120]})

        if render:
            path = os.path.join(outdir, f"sb_{i}.png")
            bpy.context.scene.render.filepath = path
            bpy.ops.render.render(write_still=True)
            out["images"].append(path)
        print(f"[turn {i}] {text} -> {rec.get('say')!r} intent={intent}", flush=True)

    out["api_calls"] = chat.calls + parser.calls
    print("HEADLESS " + json.dumps(out, ensure_ascii=False))


main()
