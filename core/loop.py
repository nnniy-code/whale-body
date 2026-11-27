"""core/loop.py — 常驻帧循环：把「算出来的姿态」真正推到骨骼上（v1.9）。

**为什么要有这个文件**：
  v1.8 里 `Runtime` 能算姿态、`binder` 能写骨、`Dispatcher` 能排队，
  但**没有任何模块把这三件事串成一条帧循环**——「她真的动了」全靠临时脚本。
  所有能力都困在测试里，图上不了屏。

**设计边界**：
  - 不 import bpy。写骨交给注入的 `writer(basis) -> int`
      · 真机：`loop.arm_writer(arm)`（内部才 import binder/bpy）
      · 测试：一个假的 writer（只记调用）
  - 不管 LLM、不管队列。它只负责「一帧」：推进时钟与身体 → 取姿态 → 写骨

**为什么时钟在 loop 里**：
  步态相位要一个单调递增的 `t`。以前 `build_full(P, family, t)` 的 `t`
  由调用方随手传，传成帧号或墙钟都行——相位会跳。现在只此一个。

用法（真机）：

    loop = BodyLoop(rt, writer=None)
    loop.attach(arm)                       # 建 writer
    for _ in range(48):
        loop.step(1/30, P=P_walk, family="walk")
        bpy.context.view_layer.update()
"""

from __future__ import annotations

import time


class BodyLoop:
    """一帧的执行器。纯 Python，可脱离 bpy 单测。"""

    def __init__(self, runtime, writer=None, clock=None, secondary=None, idle=None):
        self.rt = runtime
        self.writer = writer          # writer(basis) -> 写入骨数
        self.clock = clock or time.perf_counter
        self.secondary = secondary    # core.secondary.SecondaryMotion | None
        self.idle = idle              # core.idle.IdleMotion | None（L0 底噪）
        self.t = 0.0                  # 全局时钟（秒），步态相位唯一来源
        self.frames = 0
        self.written = 0
        self.last_basis = None
        self.last_diag: dict = {}

    def enable_secondary(self, **kw):
        """开二次运动（头发/裙摆惯性）。参数见 core/secondary.py。"""
        from core.secondary import SecondaryMotion
        self.secondary = SecondaryMotion(self.rt.rig, **kw)
        return self.secondary

    def enable_idle(self, **kw):
        """开 L0 底噪（呼吸 + 视线跟随）。参数见 core/idle.py。"""
        from core.idle import IdleMotion
        self.idle = IdleMotion(self.rt.rig, self.rt.bm, **kw)
        return self.idle

    # ---- 绑定骨骼 ----
    def attach(self, arm, writer=None):
        """绑定 armature，之后 step() 会自动写骨。"""
        self.writer = writer or self.arm_writer(arm)
        return self.writer

    @staticmethod
    def arm_writer(arm):
        """真机 writer：basis → pose_bone.matrix_basis。"""
        from core.binder import apply_basis

        def _write(basis):
            return apply_basis(arm, basis)

        return _write

    # ---- 一帧 ----
    def _finish(self, basis, dt: float, write: bool):
        """收尾：L0 底噪 → 二次运动 → 写骨。"""
        if self.idle is not None and basis is not None and dt > 0:
            basis = self.idle.step(basis, dt)
        if self.secondary is not None and basis is not None and dt > 0:
            basis = self.secondary.step(basis, dt, getattr(self.rt, "body", None))
        self.last_basis = basis
        self.frames += 1
        if write and basis is not None and self.writer is not None:
            self.written += int(self.writer(basis) or 0)
        return basis

    def step(self, dt: float, P: dict | None = None, family: str | None = None,
             active_side: str | None = None, write: bool = True):
        """推进一帧。

        P + family 给了 → 全身装配（含腿 IK，`build_full`）
        没给          → 用当前过渡中的姿态（`current_basis`，下半身不动）
        """
        dt = max(0.0, float(dt))
        self.t += dt
        self.rt.tick(dt)

        if P is not None and family is not None:
            basis, diag = self.rt.build_full(P, family, self.t,
                                             active_side=active_side)
            self.last_diag = diag
        else:
            basis = self.rt.current_basis()
            self.last_diag = {}
        return self._finish(basis, dt, write), self.last_diag

    def step_actions(self, acts, dt: float, now: float | None = None,
                     write: bool = True):
        """一帧：推进 → 多动作叠加（base 全身）→ 取过渡后的姿态 → 写骨。"""
        dt = max(0.0, float(dt))
        self.t += dt
        self.rt.tick(dt)
        r = self.rt.handle_composite(acts, now=self.t if now is None else now)
        self.last_diag = {"composite": r.get("report"),
                          "full_body": r.get("full_body"),
                          "sources": r.get("sources")}
        basis = self.rt.current_basis()
        return self._finish(basis, dt, write), self.last_diag

    # ---- 动作 ----
    def play(self, acts, now: float | None = None, **kw):
        """走 Runtime 的多动作叠加（含 200ms 过渡）。"""
        return self.rt.handle_composite(acts, now=self.clock() if now is None else now, **kw)

    # ---- 批量（测试 / 无头回放）----
    def run(self, frames: int, dt: float = 1 / 30, **kw):
        """连续跑 N 帧，返回每帧的 (hand_z, 最大脚误差)。"""
        trace = []
        for _ in range(int(frames)):
            basis, diag = self.step(dt, **kw)
            if basis is None:
                continue
            M = self.rt.rig.compute(basis)
            hand = float(M["rightHand"][2, 3]) if "rightHand" in M else 0.0
            foot_err = max(diag["err_mm"].values()) if diag.get("err_mm") else 0.0
            trace.append((round(hand, 4), round(float(foot_err), 2)))
        return trace

    def snapshot(self) -> dict:
        return {"t": round(self.t, 3), "frames": self.frames,
                "written": self.written, "has_basis": self.last_basis is not None,
                "body": self.rt.body.as_dict() if hasattr(self.rt, "body") else None}

    def reset_clock(self, t: float = 0.0) -> None:
        self.t = float(t)
