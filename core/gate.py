"""core/gate.py — 求值与判决。

设计依据：docs/design/03-gate.md

两个入口，不能混用：
  evaluate()  给 CMA-ES 的连续标量（有梯度）
  verdict()   给调度器的严格判决（布尔 + 定位）

「verdict 与 pass/total 必须一致」是上一代缺陷 #7 的复发检测。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.predicates import Frame, evaluate_series, spec


@dataclass
class Violation:
    predicate: str
    err: float
    time_sem: str
    worst_frame: int | None
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"predicate": self.predicate, "err": round(self.err, 6),
                "time_sem": self.time_sem, "worst_frame": self.worst_frame,
                "detail": self.detail}


@dataclass
class Verdict:
    passed: bool
    pass_count: int
    total: int
    total_err: float
    violations: list[Violation] = field(default_factory=list)

    @property
    def verdict_str(self) -> str:
        return "PASS" if self.passed else "FAIL"

    def as_dict(self) -> dict:
        return {"verdict": self.verdict_str, "pass": self.pass_count,
                "total": self.total, "total_err": round(self.total_err, 6),
                "violations": [v.as_dict() for v in self.violations]}


def inject_thresholds(name: str, args: dict, thresholds: dict | None) -> dict:
    """把族阈值档注入谓词参数（谓词本身不写死阈值）。"""
    if thresholds:
        if name == "foot_grounded" and "tol_mm" not in args:
            args["tol_mm"] = thresholds.get("foot_grounded_tol_mm", 35.0)
        if name == "duty_match" and "target_duty" not in args:
            args["target_duty"] = thresholds.get("duty", 0.5)
        if name == "max_deg_per_frame" and "budget" not in args:
            args["budget"] = thresholds.get("max_deg_per_frame", 12.0)
    return args


def _resolve(predicates: list[dict]) -> list[tuple[str, dict, float]]:
    out = []
    for p in predicates:
        name = p["name"]
        args = dict(p.get("args", {}))
        w = float(p.get("weight", spec(name).weight))
        out.append((name, args, w))
    return out


def verdict(
    frames: list[Frame],
    predicates: list[dict],
    thresholds: dict | None = None,
    log=None,
    trace_id: str | None = None,
    family: str | None = None,
) -> Verdict:
    if not frames:
        raise ValueError("frames 为空")
    violations: list[Violation] = []
    total_err = 0.0
    total_w = 0.0

    for name, args, w in _resolve(predicates):
        args = inject_thresholds(name, args, thresholds)
        err, worst, detail = evaluate_series(name, frames, **args)
        s = spec(name)
        total_err += w * err
        total_w += w
        if err > 0:
            violations.append(Violation(name, float(err), s.sem, worst, detail))

    total = len(predicates)
    passed = len(violations) == 0
    v = Verdict(
        passed=passed,
        pass_count=total - len(violations),
        total=total,
        total_err=total_err / total_w if total_w > 0 else 0.0,
        violations=violations,
    )
    if log is not None:
        log.gate(trace_id or "", v, family=family)
    return v


def evaluate(
    frames: list[Frame],
    predicates: list[dict],
    thresholds: dict | None = None,
) -> float:
    """只返回加权和，不构造 Violation——求解循环要快。"""
    total = 0.0
    total_w = 0.0
    for name, args, w in _resolve(predicates):
        args = inject_thresholds(name, args, thresholds)
        err, _worst, _detail = evaluate_series(name, frames, **args)
        total += w * err
        total_w += w
    return total / total_w if total_w > 0 else 0.0


def max_step_mm(frames: list[Frame]) -> float:
    """相邻帧末端最大位移（mm/帧）。日志字段名沿用旧指标 max_deg_per_frame 的位置意义。"""
    import numpy as np
    worst = 0.0
    for a, b in zip(frames, frames[1:]):
        for bone in ("head", "leftHand", "rightHand", "leftFoot", "rightFoot"):
            if bone in a.M and bone in b.M:
                worst = max(worst, float(np.linalg.norm(a.p(bone) - b.p(bone))) * 1000.0)
    return worst
