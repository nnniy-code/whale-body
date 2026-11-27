"""core/dispatcher.py — 四层调度（L0/L1 部分；L2/L3 留给 P7/P8）。

设计依据：docs/design/05-dispatcher.md

铁律：
  1. L0 先行，L2 分片拿剩余预算
  2. 一个播放器，不叠加
  3. 打断：非循环动作执行过半时，新意图进队列并回话
  4. 周期动作（walk/run）随时可接续

不 import bpy：写骨通过注入的 binder 回调完成，便于单测。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

GRACE_S = 0.6
FRAME_MS = 33.3


@dataclass
class DispatchResult:
    action: str                      # "play" | "deferred" | "idle"
    entry_id: str | None = None
    source: str = ""                 # "L1" | "fallback" | "idle"
    latency_ms: float | None = None
    said: str | None = None
    detail: dict = field(default_factory=dict)


class Dispatcher:
    def __init__(self, library, families: dict, player, log=None,
                 grace_s: float = GRACE_S):
        self.library = library
        self.families = families
        self.player = player
        self.log = log
        self.grace_s = grace_s
        self.queue: list[dict] = []
        self.state = "IDLE"
        self.trace_id = None
        self.current = None
        self.deferred_count = 0

    # ---- 意图入口 ----
    def on_act(self, act: dict, now: float | None = None) -> DispatchResult:
        now = time.perf_counter() if now is None else now
        self.trace_id = act.get("trace_id") or f"t-{int(now*1000)}"
        key = act.get("intent_key") or {"predicates": [], "anchors": [], "verb": act.get("intent", ""), "numeric": {}}

        # 打断判定
        if self.state == "PLAYING" and self.player.state is not None:
            if self.player.state.loop:
                pass                                   # 周期动作：直接接续
            else:
                rem = self.player.remaining_s(now)
                if rem > self.grace_s:
                    self.deferred_count += 1
                    self.queue.append(act)
                    label = getattr(self.player.state, "label", "这个动作")
                    if self.log:
                        self.log.emit("intent_deferred", trace_id=self.trace_id,
                                      deferred_intent=act.get("intent"),
                                      running_action=self.current,
                                      remaining_s=round(rem, 2))
                    return DispatchResult(action="deferred", said=f"我在做{label}，等一下",
                                          detail={"remaining_s": rem})

        # 查库（计时）
        t0 = time.perf_counter()
        hit, dist = self.library.best(key)
        ms = (time.perf_counter() - t0) * 1000

        if hit is not None:
            if self.log:
                self.log.retrieve(self.trace_id, True, ms, entry_id=hit.id,
                                  score=dist, status=hit.status)
            self._play(hit, "L1", now)
            return DispatchResult(action="play", entry_id=hit.id, source="L1",
                                  latency_ms=ms)

        # 未命中：兜底（放宽阈值），找不到就 idle
        fb, fdist = self.library.best(key, threshold=0.45)
        if self.log:
            self.log.retrieve(self.trace_id, False, ms, score=fdist,
                              reason="no_hit_in_library")
        if fb is not None:
            self._play(fb, "fallback", now, blend_ms=200)
            return DispatchResult(action="play", entry_id=fb.id, source="fallback",
                                  latency_ms=ms, detail={"score": fdist})

        self.state = "IDLE"
        return DispatchResult(action="idle", source="idle", latency_ms=ms)

    def _play(self, entry, source: str, now: float, blend_ms: int = 0):
        self.current = entry.id
        self.state = "PLAYING"
        fam = self.families.get(entry.family, {})
        period = fam.get("G", {}).get("period", 1.0)
        loop = entry.family in ("walk", "run")
        self.player.play(entry, now, dur_s=entry.dur_s, loop=loop,
                         period_s=period, label=entry.family)
        if self.log:
            self.log.play(self.trace_id or "", entry.id, source,
                          blend_ms=blend_ms, family=entry.family)

    # ---- 每帧 ----
    def tick(self, now: float | None = None) -> dict:
        """L0 先行。返回本帧要渲染的 info（pose 由 caller 组装）。"""
        now = time.perf_counter() if now is None else now
        info = {"state": self.state, "phase": 0.0, "entry": None, "finished": False}

        if self.player.state is not None:
            info["phase"] = self.player.phase(now)
            info["entry"] = self.player.state.entry
            if not self.player.state.loop and self.player.finished(now):
                info["finished"] = True

        if info["finished"]:
            self.state = "IDLE"
            self.current = None
            self.player.stop()
            if self.queue:
                nxt = self.queue.pop(0)
                nxt.pop("_deferred", None)
                self.on_act(nxt, now)

        return info

    def next_runnable(self) -> dict | None:
        return self.queue.pop(0) if self.queue else None
