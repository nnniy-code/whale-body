"""core/trajectory.py — Hermite 轨迹插值。

设计依据：docs/design/07-trajectory.md

禁用幂缓动（t**p）：导数在 t=0 发散，是 176mm 单帧瞬移的根源。
禁用 smoothstep 做主力：两端速度为 0，会"每段重新起步"。

段间速度用中心差分传递，保证 C1 连续。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 曲线基元
# ---------------------------------------------------------------------------

def hermite(p0: float, v0: float, p1: float, v1: float, T: float, u: float) -> float:
    """三次 Hermite。u ∈ [0,1] 为段内归一化时间，T 为段时长（秒）。

    h(0)=p0, h(1)=p1, h'(0)=v0, h'(1)=v1（v 的单位是「每秒」）。
    """
    u2 = u * u
    u3 = u2 * u
    h00 = 2.0 * u3 - 3.0 * u2 + 1.0
    h10 = u3 - 2.0 * u2 + u
    h01 = -2.0 * u3 + 3.0 * u2
    h11 = u3 - u2
    return h00 * p0 + h10 * T * v0 + h01 * p1 + h11 * T * v1


def hermite_vel(p0: float, v0: float, p1: float, v1: float, T: float, u: float) -> float:
    """Hermite 对全局时间的导数（= 对 u 的导数 / T）。"""
    u2 = u * u
    d00 = 6.0 * u2 - 6.0 * u
    d10 = 3.0 * u2 - 4.0 * u + 1.0
    d01 = -6.0 * u2 + 6.0 * u
    d11 = 3.0 * u2 - 2.0 * u
    return (d00 * p0 + d10 * T * v0 + d01 * p1 + d11 * T * v1) / T


def quintic(u: float) -> float:
    """五次曲线，端点速度与加速度均为 0。只用于摆动相（抬脚）。"""
    u2 = u * u
    return u2 * u * (10.0 - 15.0 * u + 6.0 * u2)


def quintic_vel(u: float) -> float:
    return 30.0 * u * u * (1.0 - u) * (1.0 - u)


def quintic_acc(u: float) -> float:
    return 60.0 * u * (1.0 - u) * (1.0 - 2.0 * u)


# ---------------------------------------------------------------------------
# 轨道
# ---------------------------------------------------------------------------

@dataclass
class Segment:
    t_start: float      # 全局时间
    duration: float
    p0: float
    v0: float
    p1: float
    v1: float

    @property
    def t_end(self) -> float:
        return self.t_start + self.duration


@dataclass
class Track:
    segments: list[Segment] = field(default_factory=list)
    duration: float = 0.0

    def _find(self, t: float) -> tuple[Segment, float]:
        if not self.segments:
            raise ValueError("empty track")
        t = min(max(t, self.segments[0].t_start), self.duration)
        # 段数很少，线性即可
        for seg in self.segments:
            if t <= seg.t_end or seg is self.segments[-1]:
                u = (t - seg.t_start) / seg.duration if seg.duration > 0 else 0.0
                return seg, min(max(u, 0.0), 1.0)
        return self.segments[-1], 1.0

    def value_at(self, t: float) -> float:
        seg, u = self._find(t)
        return hermite(seg.p0, seg.v0, seg.p1, seg.v1, seg.duration, u)

    def velocity_at(self, t: float) -> float:
        seg, u = self._find(t)
        return hermite_vel(seg.p0, seg.v0, seg.p1, seg.v1, seg.duration, u)


def build_track(
    times: list[float],
    values: list[float],
    start_v: float = 0.0,
    end_v: float = 0.0,
) -> Track:
    """由关键帧构建轨道。

    内部节点的速度用中心差分：v_k = (p_{k+1} - p_{k-1}) / (T_k + T_{k+1})
    端点速度可指定（默认 0）。

    要求：times 严格递增，len(times) == len(values) >= 2。
    """
    n = len(values)
    if n != len(times):
        raise ValueError(f"times/{values} 长度不一致：{len(times)} vs {n}")
    if n < 2:
        raise ValueError("至少需要 2 个关键帧")
    if any(times[i + 1] <= times[i] for i in range(n - 1)):
        raise ValueError("times 必须严格递增")

    T = [times[i + 1] - times[i] for i in range(n - 1)]

    v = [0.0] * n
    v[0] = start_v
    v[-1] = end_v
    for k in range(1, n - 1):
        v[k] = (values[k + 1] - values[k - 1]) / (T[k - 1] + T[k])

    segs = [
        Segment(
            t_start=times[i],
            duration=T[i],
            p0=values[i], v0=v[i],
            p1=values[i + 1], v1=v[i + 1],
        )
        for i in range(n - 1)
    ]
    return Track(segments=segs, duration=times[-1] - times[0])


def sample_uniform(track: Track, fps: float = 30.0) -> list[tuple[float, float, float]]:
    """按固定帧率采样，返回 [(t, value, velocity), ...]，用于 CSV 导出与验收。"""
    if fps <= 0:
        raise ValueError("fps 必须 > 0")
    n = int(round(track.duration * fps)) + 1
    out = []
    for i in range(n):
        t = min(i / fps, track.duration)
        out.append((t, track.value_at(t), track.velocity_at(t)))
    return out


def check_c1(track: Track, eps: float = 1e-9) -> list[tuple[float, float, float]]:
    """检查所有内部接缝的 C1 连续性，返回 [(t, v_left, v_right), ...]。"""
    out = []
    for seg in track.segments[:-1]:
        t = seg.t_end
        out.append((t, track.velocity_at(t - eps), track.velocity_at(t + eps)))
    return out


def find_velocity_plateaus(
    track: Track, fps: float = 30.0, min_frames: int = 3, eps: float = 1e-6
) -> list[tuple[float, float]]:
    """找「连续 >= min_frames 帧 |v| < eps 且不在端点」的平台。

    返回 [(start_t, end_t), ...]。空列表 = 没有平台 = 通过。
    """
    samples = sample_uniform(track, fps)
    if len(samples) < min_frames:
        return []
    dur = track.duration
    # 端点附近 2 帧视为正常（静止起步/收势）
    guard = min(2.0 / fps, dur * 0.1)

    bad = []
    run_start = None
    for t, _, v in samples:
        is_flat = abs(v) < eps and guard < t < dur - guard
        if is_flat:
            if run_start is None:
                run_start = t
        else:
            if run_start is not None:
                if t - run_start >= (min_frames - 1) / fps:
                    bad.append((run_start, t))
                run_start = None
    if run_start is not None:
        bad.append((run_start, samples[-1][0]))
    return bad
