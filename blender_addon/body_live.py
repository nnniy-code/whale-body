"""body_live.py — 在 Blender 里常驻运行的「大肥鱼身体」面板（v1.9 最小版）。

装法：Blender → 偏好设置 → 插件 → 从磁盘安装 → 选这个 .py → 勾选启用。
用：3D 视图按 N → 侧栏「Body」标签 → Connect。

三个按钮：
    Connect     建 Runtime + 起帧循环 + 开始心跳（她就“活着”了）
    Disconnect  停循环 + 把骨骼还回连接前的样子
    说          把输入框里的话发给她（后台线程调 API，主线程播动作）

**为什么“没连接就不输出动作”**：
  连接期间每 2 秒 touch 一次 `sandbox/body.json`。
  `llm/persona.body_connected()` 判的就是这个文件的 mtime（15 秒内算在线），
  离线时 `turn()` 直接走 `chat_only`，不解析动作。所以这条不用额外写判断。

线程模型：
  主线程（bpy.app.timers）只做「推进一帧 + 写骨 + 出队」；
  LLM 调用在后台线程，结果进 queue。**后台线程不碰 bpy**。
"""

import json
import os
import queue
import sys
import threading
import time
import traceback

import bpy
from bpy.props import EnumProperty, StringProperty

ROOT = r"D:\DSproject\body-v2"
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FPS = 30.0
MAX_DT = 0.12          # 一帧最多推进多少秒（防止卡顿后瞬移）
HEARTBEAT_S = 2.0

ONLY = None            # 首次 connect 时算


# ---------------------------------------------------------------------------
# 引擎（不依赖 UI，可被无头脚本直接驱动）
# ---------------------------------------------------------------------------

def _reload_project_modules():
    """把 `core.*` / `llm.*` 从 sys.modules 里踢掉，下次 import 拿磁盘上的新代码。

    为什么需要：Blender 里 import 过的模块**不会**自动跟着磁盘更新。
    改完 `core/runtime.py`，不重启 Blender 就一直跑旧类——
    典型症状是「明明加过参数，却报 unexpected keyword argument」。
    """
    import importlib
    for name in [n for n in sys.modules
                 if n in ("core", "llm") or n.startswith(("core.", "llm."))]:
        del sys.modules[name]
    importlib.invalidate_caches()


class BodyLive:
    def __init__(self):
        self.connected = False
        self.status = "未连接"
        self.last_say = ""
        self.last_intent = None
        self.rt = None
        self.loop = None
        self.arm = None
        self._bak = {}
        self._prev_t = 0.0
        self._hb = 0.0
        self._q = queue.Queue()
        self._busy = False
        self._gen = 0          # 连接代数：断开/重连会让旧请求的结果作废
        self.history = []
        self._seq = None
        self._acts = {}
        self._grow_cache = None
        self._grow_at = 0.0
        self._grow_collisions = []
        self._chat = None
        self._parser = None
        self.caps = None
        self.persona = self.memory = self.mood = None

    # ---- 生命周期 ----
    def connect(self, world_mode: str | None = None):
        if self.connected:
            return "已连接"
        _reload_project_modules()      # 每次连接都吃磁盘上的最新代码
        from llm import paths as _paths
        if world_mode:
            _paths.set_world(world_mode)      # production_rw 会要求先备份
        from core.bones import BoneMap
        from core.collision import baseline_gaps, load_table
        from core.fk import Rig
        from core.library import Library
        from core.loop import BodyLoop
        from core.runtime import Runtime
        from core.solver import MAIN21

        rigd = json.load(open(os.path.join(ROOT, "tests/fixtures/rig_data.json"),
                              encoding="utf-8"))
        rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
        only = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]
        rf = rig.subset(only)
        bm = BoneMap.load(os.path.join(ROOT, "config/bones_map.json"))
        anch = json.load(open(os.path.join(ROOT, "config/anchors.json"), encoding="utf-8"))
        fams = json.load(open(os.path.join(ROOT, "config/seed_families.json"),
                              encoding="utf-8"))["families"]
        # 自动长大的族（存在沙箱里，不污染 config/）
        try:
            from core import grow as _grow
            self._grown = _grow.merge_auto(fams, self._auto_path())
            fams = self._grown
        except Exception:
            self._grown = fams
        caps = json.load(open(os.path.join(ROOT, "config/capsules.json"),
                              encoding="utf-8"))["bones"]
        tab = load_table()
        bmj = json.load(open(os.path.join(ROOT, "config/bones_map.json"),
                             encoding="utf-8"))["bones"]
        L = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
        zero = {n: __import__("numpy").eye(4) for n in rigd["order"]}
        base = baseline_gaps(rf.compute(zero), caps, rf, L, chain_skip=3, only=only)

        self.rt = Runtime(rig, bm, anch, fams, Library(), capsules=caps,
                          collision_table=tab, lengths=L,
                          capsule_baseline=base, capsule_only=only)
        # 库要跨会话留着，否则「同一动作 3 次」永远攒不够（重连就清零）
        try:
            lib = Library.open(self._lib_path())
            if lib.entries:
                self.rt.library = lib
        except Exception:
            pass
        self.rt.set_motion(family="idle")

        arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
        if not arms:
            raise RuntimeError("场景里没有 armature")
        self.arm = max(arms, key=lambda o: len(o.pose.bones))

        # 记下连接前的姿态，断开时原样还回去（用户的场景不该被我改掉）
        self._bak = {pb.name: pb.matrix_basis.copy() for pb in self.arm.pose.bones}

        self.loop = BodyLoop(self.rt)
        self.loop.attach(self.arm)
        self.loop.enable_idle()
        self.loop.enable_secondary()

        from core.sequence import ActionSequence
        self._seq = ActionSequence()

        # 先摆一个 idle 当底，否则 current_basis() 是 None，呼吸没东西可叠
        self.rt.handle_composite([{"intent": "idle"}], now=self.loop.t)

        # 人格/记忆/情绪（沙箱）
        from llm.persona import load_memory, load_mood, load_persona
        self.persona = load_persona()
        self.memory = load_memory()
        self.mood = load_mood()
        self.caps = self.rt.capabilities()

        from llm.client import DeepSeek, find_api_key
        key = find_api_key()
        if key:
            self._chat = DeepSeek(key, temperature=1.15, max_tokens=300, timeout_s=60)
            self._parser = DeepSeek(key, temperature=0.1, max_tokens=300, timeout_s=60)
        else:
            self._chat = self._parser = None

        self.connected = True
        self._gen += 1
        self._q = queue.Queue()      # 清掉上一代残留在队列里的结果
        self._prev_t = time.perf_counter()
        self._hb = 0.0
        self.status = "已连接 · 呼吸中"
        self._touch_heartbeat(force=True)
        return "ok"

    def disconnect(self, restore=True):
        if not self.connected:
            return "未连接"
        self.connected = False
        self._gen += 1               # 让正在飞的那次请求作废
        self._q = queue.Queue()
        self._busy = False
        if restore and self.arm is not None:
            for pb in self.arm.pose.bones:
                m = self._bak.get(pb.name)
                if m is not None:
                    pb.matrix_basis = m
            bpy.context.view_layer.update()
        self.status = "未连接"
        self._bak = {}
        if self._seq is not None:
            self._seq.clear()
        return "ok"

    # ---- 每帧 ----
    def tick(self, dt: float | None = None):
        """主线程调用。返回下一帧间隔（秒）。"""
        if not self.connected:
            return None
        now = time.perf_counter()
        if dt is None:
            dt = min(MAX_DT, max(0.0, now - self._prev_t))
        self._prev_t = now

        self._drain()

        try:
            self.loop.step(dt)
            bpy.context.view_layer.update()
        except Exception:
            self.status = "出错：" + traceback.format_exc(limit=1).splitlines()[-1][:60]
            return 1.0 / FPS

        # 多动作串接：到点切下一个
        if self._seq is not None and self._seq.busy:
            nxt = self._seq.poll(self.loop.t)
            if nxt:
                self._play(nxt)
            elif self._seq.busy:
                self.status = f"已连接 · {self._seq.current}（还有 {len(self._seq.items) - self._seq.idx - 1} 个）"

        self._touch_heartbeat()
        return 1.0 / FPS

    def _auto_path(self) -> str:
        return os.path.join(ROOT, "sandbox", "auto_families.json")

    def _lib_path(self) -> str:
        return os.path.join(ROOT, "sandbox", "library.json")

    def _save_library(self):
        try:
            if self.rt is not None:
                self.rt.library.save(self._lib_path())
        except Exception:
            pass

    # ---- 族自动生长 ----
    def grow_pending(self, min_count: int = 3) -> list[dict]:
        """反复出现的 free 组合 → 待确认的新族提议。（面板每帧都会调，做 2s 缓存）"""
        if self.rt is None:
            return []
        now = time.time()
        if self._grow_cache is not None and (now - self._grow_at) < 2.0:
            return self._grow_cache
        try:
            from core import grow as _grow
            props = _grow.merge_labels(_grow.propose(self.rt.library, min_count=min_count))
            data = _grow.load_auto(self._auto_path())
            dismissed = set(data.get("dismissed") or [])
            grown_fps = {(v or {}).get("fingerprint") for v in (data.get("families") or {}).values()}
            pending = [p for p in props
                       if p["fingerprint"] not in dismissed and p["fingerprint"] not in grown_fps]
            self._grow_collisions = _grow.label_collisions(pending)
        except Exception:
            pending = []
        self._grow_cache = pending
        self._grow_at = now
        return pending

    def grow_collisions(self) -> list[dict]:
        """同名但判据不同——只警告，绝不自动合并。"""
        self.grow_pending()
        return self._grow_collisions

    def grow_apply(self, fingerprint: str, min_count: int = 3) -> str:
        """收下一个提议：变成命名族，写进沙箱，立即可用。"""
        if self.rt is None:
            return "未连接"
        from core import grow as _grow
        props = [p for p in _grow.propose(self.rt.library, min_count=min_count)
                 if p["fingerprint"] == fingerprint]
        if not props:
            return "提议已失效"
        fams, added = _grow.apply_proposals(
            self.rt.families, props, min_count=min_count,
            template_family=self.rt.families.get("free"))
        if not added:
            return "没变化"
        name = added[0]["name"]
        fam = dict(fams[name])
        fam["name_hint"] = name
        self.rt.families = fams
        self._grown = fams
        _grow.record_family(self._auto_path(), fam)
        self._grow_cache = None
        self.status = f"已长出新族：{fam.get('label') or name}"
        return self.status

    def grow_dismiss(self, fingerprint: str) -> str:
        from core import grow as _grow
        data = _grow.load_auto(self._auto_path())
        if fingerprint not in data["dismissed"]:
            data["dismissed"].append(fingerprint)
            _grow.save_auto(self._auto_path(), data)
        self._grow_cache = None
        return "已忽略"

    def _play(self, intent: str):
        act = (self._acts or {}).get(intent) or {"intent": intent}
        try:
            self.rt.handle_composite([act], now=self.loop.t)
            self.status = f"已连接 · {intent}"
        except Exception:
            self.status = "动作失败"

    def _touch_heartbeat(self, force=False):
        """写**我们自己的**心跳，绝不 touch 正版的 body.json。"""
        now = time.perf_counter()
        if not force and (now - self._hb) < HEARTBEAT_S:
            return
        self._hb = now
        try:
            from llm import paths as _paths
            p = _paths.heartbeat_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"alive": True, "by": "body_live", "ts": time.time()}, f)
        except Exception:
            pass

    # ---- 说话 ----
    def say(self, text: str):
        text = (text or "").strip()
        if not text or not self.connected:
            return "未连接或空输入"
        if self._chat is None:
            self.status = "没有可用 API key"
            return self.status
        if self._busy:
            self.status = "还在想上一句…"
            return self.status
        self._busy = True
        self.status = "思考中…"
        threading.Thread(target=self._worker, args=(text,), daemon=True).start()
        return "ok"

    def _worker(self, text):
        gen = self._gen
        try:
            from llm.persona import turn
            r = turn(text, self._chat, self.caps, self.persona, self.memory,
                     self.mood, body=True, parser_llm=self._parser,
                     history=self.history or None)
            self._q.put((gen, "ok", text, r))
        except Exception:
            self._q.put((gen, "err", text, traceback.format_exc(limit=1)))

    def _drain(self):
        while True:
            try:
                gen, kind, text, payload = self._q.get_nowait()
            except queue.Empty:
                return
            if gen != self._gen:
                # 断开/重连之前发出的请求：结果直接丢掉，别在重连后突然播
                continue
            self._busy = False
            if kind == "err":
                self.status = "API 出错"
                continue
            self._apply(text, payload)

    def _apply(self, text, r):
        say = (r.get("say") or "").strip()
        intent = r.get("intent")
        self.last_say = say
        self.last_intent = intent

        # 一句多动作：解析器可能给 1–3 个，按顺序播
        from llm.persona import acts_from_turn
        acts = acts_from_turn(r)
        for a in acts:
            a.setdefault("user_text", text)      # 长大后给族起中文名用
        self._acts = {a["intent"]: a for a in acts}
        first = self._seq.load([{"intent": a["intent"]} for a in acts],
                               self.loop.t) if self._seq is not None else None
        if first:
            self._play(first)
        else:
            self.status = "已连接 · 呼吸中"
        self._grow_cache = None     # 计数变了，提议要重算
        self._save_library()

        # 写回沙箱世界线（只写 sandbox/）
        try:
            from llm.paths import BASE, WORLD
            from llm.world import persist_turn
            persist_turn(BASE, text, say, intent, r.get("mood_delta"), world=WORLD)
            from llm.persona import load_mood
            self.mood = load_mood()
        except Exception:
            pass

        self.history.append({"role": "user", "text": text})
        self.history.append({"role": "她", "text": say.replace("\n", " ")[:120]})
        self.history = self.history[-8:]

    def snapshot(self):
        return {"connected": self.connected, "status": self.status,
                "say": self.last_say, "intent": self.last_intent,
                "frames": (self.loop.frames if self.loop else 0)}


ENGINE = BodyLive()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _timer():
    if not ENGINE.connected:
        return None
    return ENGINE.tick()


class BODY_OT_connect(bpy.types.Operator):
    bl_idname = "body_live.connect"
    bl_label = "Connect"
    bl_description = "连接身体：起帧循环 + 开始心跳"

    def execute(self, context):
        try:
            mode = getattr(context.scene, "body_live_world", "sandbox")
            ENGINE.connect(mode)
            if not bpy.app.timers.is_registered(_timer):
                bpy.app.timers.register(_timer, first_interval=0.05, persistent=True)
        except PermissionError as e:
            ENGINE.status = "拒绝：" + str(e).splitlines()[0][:56]
            print("[body_live] 拒绝：", e)
        except Exception:
            import traceback as _tb
            ENGINE.status = "连接失败：" + _tb.format_exc(limit=1).splitlines()[-1][:52]
            _tb.print_exc()          # 完整栈打到 Blender 系统控制台
        return {"FINISHED"}


class BODY_OT_disconnect(bpy.types.Operator):
    bl_idname = "body_live.disconnect"
    bl_label = "Disconnect"
    bl_description = "断开：停循环，把骨骼还原成连接前的样子"

    def execute(self, context):
        if bpy.app.timers.is_registered(_timer):
            bpy.app.timers.unregister(_timer)
        ENGINE.disconnect()
        return {"FINISHED"}


class BODY_OT_say(bpy.types.Operator):
    bl_idname = "body_live.say"
    bl_label = "说"
    bl_description = "把输入框里的话发给她"

    def execute(self, context):
        ENGINE.say(getattr(context.scene, "body_live_text", ""))
        return {"FINISHED"}


class BODY_OT_grow_apply(bpy.types.Operator):
    bl_idname = "body_live.grow_apply"
    bl_label = "收下"
    bl_description = "把这个反复出现的动作收成固定动作（下次直接走它，更快更稳）"
    fp: StringProperty(default="")

    def execute(self, context):
        print("[body_live]", ENGINE.grow_apply(self.fp))
        return {"FINISHED"}


class BODY_OT_grow_dismiss(bpy.types.Operator):
    bl_idname = "body_live.grow_dismiss"
    bl_label = "忽略"
    bl_description = "不再提议这个动作"
    fp: StringProperty(default="")

    def execute(self, context):
        ENGINE.grow_dismiss(self.fp)
        return {"FINISHED"}


class BODY_PT_panel(bpy.types.Panel):
    bl_label = "大肥鱼 · 身体"
    bl_idname = "BODY_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Body"

    def draw(self, context):
        lay = self.layout
        e = ENGINE
        lay.label(text=f"状态：{e.status}")
        lay.prop(context.scene, "body_live_world", text="世界线")
        row = lay.row(align=True)
        if e.connected:
            row.operator("body_live.disconnect", icon="PAUSE")
        else:
            row.operator("body_live.connect", icon="PLAY")
        if e.connected:
            lay.prop(context.scene, "body_live_text", text="")
            lay.operator("body_live.say", icon="SPEAKER")
            if e.last_say:
                box = lay.box()
                col = box.column(align=True)
                for line in e.last_say.split("\n")[:4]:
                    col.label(text=line[:40])

            # 族自动生长：反复出现的新动作 → 提议收成固定动作
            pending = e.grow_pending()
            if pending:
                gbox = lay.box()
                gbox.label(text=f"可以收成固定动作（{len(pending)}）", icon="OUTLINER_DATA_GP_LAYER")
                for p in pending[:3]:
                    g = gbox.box()
                    g.label(text=f"{(p.get('label') or p['name'])[:22]}  ×{p['count']}")
                    gr = g.row(align=True)
                    gr.operator("body_live.grow_apply", text="收下",
                                icon="CHECKMARK").fp = p["fingerprint"]
                    gr.operator("body_live.grow_dismiss", text="忽略",
                                icon="X").fp = p["fingerprint"]

            # 同名但判据不同：只警告，不合并（防止「名字像、动作不像」被归一）
            coll = e.grow_collisions()
            if coll:
                cbox = lay.box()
                cbox.label(text="同名但动作不同（不会合并）", icon="ERROR")
                for c in coll[:2]:
                    cbox.label(text=f"「{c['label'][:16]}」有 {len(c['fingerprints'])} 种判据")


CLASSES = (BODY_OT_connect, BODY_OT_disconnect, BODY_OT_say,
           BODY_OT_grow_apply, BODY_OT_grow_dismiss, BODY_PT_panel)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.body_live_text = StringProperty(
        name="对她说", default="挥挥手跟我打个招呼")
    bpy.types.Scene.body_live_world = EnumProperty(
        name="世界线",
        description="沙箱 = 测试副本；正版只读 = 用她真实的人格/记忆但不写；正版可写 = 会改她的真实状态",
        items=[
            ("sandbox", "沙箱", "测试副本，写入安全（默认）"),
            ("production", "正版只读", "读真实人格/记忆/情绪，不写任何文件"),
            ("production_rw", "正版可写", "会改她的真实记忆与情绪，必须先备份"),
        ],
        default="sandbox")


def unregister():
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    ENGINE.disconnect()
    del bpy.types.Scene.body_live_text
    del bpy.types.Scene.body_live_world
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
