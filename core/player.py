"""core/player.py — 播放器：管「播到哪了」，不管「这一帧长什么样」。

设计依据：docs/design/17-player.md、D10

phase 是状态（跨帧保留），pose 每帧从 base_rest 重建（无状态）。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlayerState:
    entry: object = None
    started_at: float = 0.0
    paused_at: float | None = None
    rate: float = 1.0
    dur_s: float = 1.0
    loop: bool = False
    period_s: float | None = None
    label: str = ""


class Player:
    def __init__(self):
        self.state: PlayerState | None = None
        self._on_finish = None

    # ---- 查询 ----
    def is_playing(self) -> bool:
        return self.state is not None and not self.finished(self._now())

    def elapsed(self, now: float) -> float:
        s = self.state
        if s is None:
            return 0.0
        base = (s.paused_at - s.started_at) if s.paused_at is not None else (now - s.started_at)
        return base * s.rate

    def phase(self, now: float) -> float:
        s = self.state
        if s is None:
            return 0.0
        e = self.elapsed(now)
        if s.loop and s.period_s:
            return (e / s.period_s) % 1.0
        return min(1.0, e / s.dur_s) if s.dur_s > 0 else 0.0

    def remaining_s(self, now: float) -> float:
        """周期动作返回 0——它随时可以接续，不算「正在执行」。"""
        s = self.state
        if s is None:
            return 0.0
        if s.loop:
            return 0.0
        return max(0.0, (s.dur_s - self.elapsed(now)) / (s.rate or 1.0))

    def finished(self, now: float) -> bool:
        s = self.state
        if s is None:
            return True
        if s.loop:
            return False
        return self.elapsed(now) >= s.dur_s

    # ---- 控制 ----
    def play(self, entry, now: float, dur_s: float = 1.0, loop: bool = False,
             period_s: float | None = None, rate: float = 1.0, label: str = "") -> None:
        self.state = PlayerState(entry=entry, started_at=now, rate=rate,
                                 dur_s=max(0.01, dur_s), loop=loop,
                                 period_s=period_s, label=label)

    def stop(self) -> None:
        self.state = None

    def pause(self, now: float) -> None:
        if self.state and self.state.paused_at is None:
            self.state.paused_at = now

    def resume(self, now: float) -> None:
        s = self.state
        if s and s.paused_at is not None:
            s.started_at = now - (s.paused_at - s.started_at)
            s.paused_at = None

    def set_rate(self, r: float) -> None:
        if self.state:
            self.state.rate = max(1e-3, float(r))

    def on_finished(self, callback) -> None:
        self._on_finish = callback

    @staticmethod
    def _now() -> float:
        import time
        return time.perf_counter()
