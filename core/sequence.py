"""core/sequence.py — 一句话触发多个动作的播放队列（v1.9）。

以前一次对话只能解析出**一个** `intent`，所以「过来，然后坐下」只能做一个。

现在解析器可以给 1–3 个动作，这里按顺序播放：每个动作播它的时长，
到点自动切下一个。**不是叠加**（那是 overlay 的事），是**串接**。

    过来(1.6s) → 坐下(1.6s)

纯 Python、无状态依赖，可单独测。
"""

from __future__ import annotations

DEFAULT_DUR = 1.6      # 单个动作默认播多久（秒）
MIN_DUR = 0.5
MAX_DUR = 8.0
MAX_ITEMS = 3          # 一句话最多串 3 个动作，再多不像一句话


class ActionSequence:
    """按顺序播放的队列。`load()` 起头，`poll()` 每帧问「该切了吗」。"""

    def __init__(self, default_dur: float = DEFAULT_DUR,
                 min_dur: float = MIN_DUR, max_items: int = MAX_ITEMS):
        self.default_dur = float(default_dur)
        self.min_dur = float(min_dur)
        self.max_items = int(max_items)
        self.clear()

    # ---- 装载 ----
    def load(self, intents, now: float) -> str | None:
        """装载一串动作，返回**第一个**要播的族名（没有就 None）。"""
        self.items = self._norm(intents)
        self.idx = 0
        self.until = 0.0
        if not self.items:
            return None
        self.until = now + self.items[0][1]
        return self.items[0][0]

    def _norm(self, intents) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []
        for it in intents or []:
            if isinstance(it, str):
                name, dur = it, self.default_dur
            elif isinstance(it, dict):
                name = it.get("intent")
                try:
                    dur = float(it.get("dur_s") or self.default_dur)
                except (TypeError, ValueError):
                    dur = self.default_dur
            else:
                continue
            if not name:
                continue
            out.append((str(name), max(self.min_dur, min(MAX_DUR, dur))))
            if len(out) >= self.max_items:
                break
        return out

    # ---- 每帧 ----
    def poll(self, now: float) -> str | None:
        """到点了就返回下一个族名；没到/播完返回 None。"""
        if not self.items or self.idx >= len(self.items):
            return None
        if now < self.until:
            return None
        self.idx += 1
        if self.idx >= len(self.items):
            self.clear()
            return None
        self.until = now + self.items[self.idx][1]
        return self.items[self.idx][0]

    def clear(self) -> None:
        self.items: list[tuple[str, float]] = []
        self.idx = 0
        self.until = 0.0

    # ---- 查询 ----
    @property
    def busy(self) -> bool:
        return bool(self.items) and self.idx < len(self.items)

    @property
    def current(self) -> str | None:
        if not self.busy:
            return None
        return self.items[self.idx][0]

    def remaining(self, now: float) -> float:
        return max(0.0, self.until - now) if self.busy else 0.0

    def snapshot(self) -> dict:
        return {"items": [n for n, _d in self.items], "idx": self.idx,
                "busy": self.busy, "current": self.current}
