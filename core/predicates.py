"""core/predicates.py — 谓词库。

设计依据：docs/design/02-predicates.md

契约：
  - 每条谓词返回 err ∈ [0,1]（0 = 通过），**不是布尔**
  - 每条带时间语义：at_end / at_any / throughout / peak
  - detail 必须能回答「差多少、差在哪」

不碰 bpy。所有输入来自 fk.py 的矩阵与 contact.py 的接触状态。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

SEM = ("at_end", "at_any", "throughout", "peak")
DEFAULT_SCALE_K = 2.0          # scale = tol * 2：超出容差一倍记满分


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def norm_err(excess: float, scale: float) -> float:
    if excess <= 0:
        return 0.0
    if scale <= 0:
        return 1.0
    return min(1.0, excess / scale)


def soft_max(errs, beta: float = 10.0) -> float:
    """log-sum-exp 软最大值。硬 max 会让搜索只看到最差帧。"""
    if not errs:
        return 0.0
    e = [beta * x for x in errs]
    m = max(e)
    return (m + math.log(sum(math.exp(v - m) for v in e))) / beta


def merge(errs: list[float], sem: str) -> tuple[float, int | None]:
    if not errs:
        return 0.0, None
    if sem in ("at_end",):
        return float(errs[-1]), len(errs) - 1
    if sem in ("at_any", "peak"):
        i = min(range(len(errs)), key=lambda k: errs[k])
        return float(errs[i]), i
    if sem == "throughout":
        i = max(range(len(errs)), key=lambda k: errs[k])
        return soft_max(errs), i
    raise ValueError(f"未知时间语义 {sem!r}")


# ---------------------------------------------------------------------------
# 上下文
# ---------------------------------------------------------------------------

@dataclass
class Frame:
    M: dict[str, np.ndarray]        # bone -> 4x4（armature 空间）
    contacts: dict = field(default_factory=dict)   # side -> FootContact
    phase: float = 0.0
    duty: float = 0.5
    anchors: dict = field(default_factory=dict)    # name -> np.array(3)
    meta: dict = field(default_factory=dict)

    def p(self, bone: str) -> np.ndarray:
        return self.M[bone][0:3, 3]

    def anchor(self, name: str) -> np.ndarray:
        if name in self.anchors:
            return np.asarray(self.anchors[name], dtype=np.float64)
        if name in self.M:
            return self.p(name)
        raise KeyError(f"锚点或骨骼缺失：{name}")


@dataclass
class Spec:
    name: str
    sem: str
    weight: float
    fn: object
    doc: str = ""


PREDICATES: dict[str, Spec] = {}


def predicate(name: str, sem: str, weight: float = 1.0):
    if sem not in SEM:
        raise ValueError(f"{name}: 非法时间语义 {sem}")
    def deco(fn):
        PREDICATES[name] = Spec(name=name, sem=sem, weight=weight, fn=fn,
                                doc=(fn.__doc__ or "").strip())
        return fn
    return deco


# ---------------------------------------------------------------------------
# A. 末端位置
# ---------------------------------------------------------------------------

@predicate("hand_near_anchor", "at_any", 1.5)
def hand_near_anchor(f: Frame, side="right", anchor="mouth",
                     dist_mm=0.0, tol_mm=40.0):
    """手到锚点的**距离**（不要求贴上去）。

    dist_mm=0  → 贴到锚点上（"手到嘴边"）
    dist_mm=50 → 离锚点 50mm（"手在头顶上方 50mm"）

    用「锚点 + 距离」表达目标，**不需要参考姿态**——锚点是身体自己的坐标，
    谁都不会变，所以评价标准是独立的（不会变成自己考自己）。
    """
    d = float(np.linalg.norm(f.p(f"{side}Hand") - f.anchor(anchor)) * 1000.0)
    excess = abs(d - dist_mm) - tol_mm
    return norm_err(excess, tol_mm * DEFAULT_SCALE_K), {
        "d_mm": round(d, 2), "target_mm": dist_mm, "tol_mm": tol_mm,
        "anchor": anchor}


@predicate("hand_outside_body", "at_end", 1.2)
def hand_outside_body(f: Frame, side="right", min_mm=150.0):
    """手必须在身体外侧至少 min_mm。

    `arm_not_crossed` 只管「不跨过中线」（x<0），手停在 x=-88 它也满意。
    挥手时手应该在体侧（参考值 x≈-230），所以需要这条把手下限。
    """
    sh_x = f.p(f"{side}Shoulder")[0]
    hd_x = f.p(f"{side}Hand")[0]
    outside = (sh_x - hd_x) if side == "right" else (hd_x - sh_x)
    excess = min_mm / 1000.0 - outside
    return norm_err(excess * 1000.0, 80.0), {
        "outside_mm": round(outside * 1000, 1), "min_mm": min_mm}


@predicate("elbow_near_anchor", "at_any", 1.2)
def elbow_near_anchor(f: Frame, side="right", anchor="chest",
                      dist_mm=150.0, tol_mm=40.0):
    """肘到锚点的距离。用来约束「肘在哪」——之前求解器总是把肘抬到肩高。"""
    d = float(np.linalg.norm(f.p(f"{side}LowerArm") - f.anchor(anchor)) * 1000.0)
    excess = abs(d - dist_mm) - tol_mm
    return norm_err(excess, tol_mm * DEFAULT_SCALE_K), {
        "d_mm": round(d, 2), "target_mm": dist_mm, "anchor": anchor}


@predicate("foot_near_anchor", "at_any", 1.2)
def foot_near_anchor(f: Frame, side="left", anchor="chest",
                     dist_mm=800.0, tol_mm=60.0):
    """脚到锚点的距离。"""
    d = float(np.linalg.norm(f.p(f"{side}Foot") - f.anchor(anchor)) * 1000.0)
    excess = abs(d - dist_mm) - tol_mm
    return norm_err(excess, tol_mm * DEFAULT_SCALE_K), {
        "d_mm": round(d, 2), "target_mm": dist_mm, "anchor": anchor}


@predicate("hand_above_head", "at_any", 1.0)
def hand_above_head(f: Frame, side="right", margin_mm=0.0):
    top = f.anchor("head_top")[2]
    h = f.p(f"{side}Hand")[2]
    excess = (top - h) + margin_mm / 1000.0
    return norm_err(excess * 1000.0, 100.0), {
        "hand_z_mm": round(h * 1000, 1), "head_top_mm": round(top * 1000, 1)}


@predicate("hand_below_waist", "throughout", 0.8)
def hand_below_waist(f: Frame, side="right", margin_mm=0.0):
    hip = f.p("hips")[2]
    h = f.p(f"{side}Hand")[2]
    excess = (h - hip) + margin_mm / 1000.0
    return norm_err(excess * 1000.0, 100.0), {"hand_z_mm": round(h * 1000, 1)}


@predicate("hand_at_point", "at_end", 1.5)
def hand_at_point(f: Frame, side="right", target_m=(0, 0, 1), tol_mm=30.0):
    t = np.asarray(target_m, dtype=np.float64)
    d = float(np.linalg.norm(f.p(f"{side}Hand") - t) * 1000.0)
    return norm_err(d - tol_mm, tol_mm * DEFAULT_SCALE_K), {"d_mm": round(d, 2)}


@predicate("foot_at_point", "at_end", 1.2)
def foot_at_point(f: Frame, side="left", target_m=(0, 0, 0), tol_mm=25.0):
    t = np.asarray(target_m, dtype=np.float64)
    d = float(np.linalg.norm(f.p(f"{side}Foot") - t) * 1000.0)
    return norm_err(d - tol_mm, tol_mm * DEFAULT_SCALE_K), {"d_mm": round(d, 2)}


@predicate("tail_tip_at_anchor", "at_any", 0.5)
def tail_tip_at_anchor(f: Frame, anchor="hand.R", tol_mm=60.0):
    tip = f.p("WhaleTail_04")
    d = float(np.linalg.norm(tip - f.anchor(anchor)) * 1000.0)
    return norm_err(d - tol_mm, tol_mm * DEFAULT_SCALE_K), {"d_mm": round(d, 2)}


# ---------------------------------------------------------------------------
# B. 朝向
# ---------------------------------------------------------------------------

def _pitch_deg(f: Frame, bone: str) -> float:
    """骨的世界朝向相对竖直的俯仰角（度）。用骨的 Y 轴（局部 +Z 映射）。"""
    m = f.M[bone][0:3, 0:3]
    up = m[:, 1]                       # 局部 Y = 世界 +Z（躯干骨）
    return math.degrees(math.acos(max(-1.0, min(1.0, float(up[2])))))


@predicate("head_nod", "at_end", 0.8)
def head_nod(f: Frame, deg=0.0, tol_deg=6.0):
    got = _pitch_deg(f, "head")
    return norm_err(abs(got - abs(deg)) - tol_deg, tol_deg * DEFAULT_SCALE_K), {
        "deg": round(got, 2), "target": deg}


@predicate("head_tilt", "at_end", 0.8)
def head_tilt(f: Frame, deg=0.0, tol_deg=6.0):
    m = f.M["head"][0:3, 0:3]
    side = m[:, 2]                     # 局部 Z = 世界 -X
    got = math.degrees(math.asin(max(-1.0, min(1.0, float(side[2])))))
    return norm_err(abs(-got - deg) - tol_deg, tol_deg * DEFAULT_SCALE_K), {
        "deg": round(-got, 2), "target": deg}


@predicate("head_yaw", "at_end", 0.8)
def head_yaw(f: Frame, deg=0.0, tol_deg=8.0):
    m = f.M["head"][0:3, 0:3]
    fwd = m[:, 0]                      # 局部 X = 世界 -Y（前方）
    got = math.degrees(math.atan2(float(fwd[0]), float(-fwd[1])))
    return norm_err(abs(got - deg) - tol_deg, tol_deg * DEFAULT_SCALE_K), {
        "deg": round(got, 2), "target": deg}


# rest 姿态下 chest 的前倾角（实测，见 tools/audit_rig.py 的输出）。
# `lean` 参数是**相对 rest 的增量**，所以谓词要减掉这个基准，否则
# lean=0 时 torso_lean 会读出 6.34°，把「没动」误判成「接近目标 8°」。
REST_CHEST_PITCH_DEG = 6.34


@predicate("torso_lean", "at_end", 1.0)
def torso_lean(f: Frame, deg=0.0, tol_deg=5.0):
    got_abs = _pitch_deg(f, "chest")
    got = got_abs - REST_CHEST_PITCH_DEG       # 相对 rest 的增量
    return norm_err(abs(got - deg) - tol_deg, tol_deg * DEFAULT_SCALE_K), {
        "deg": round(got, 2), "abs_deg": round(got_abs, 2), "target": deg}


@predicate("gaze_toward", "at_any", 1.0)
def gaze_toward(f: Frame, anchor="hand.R", cone_deg=20.0):
    m = f.M["head"][0:3, 0:3]
    fwd = -m[:, 0]                     # 局部 X = -Y = 前方
    to = f.anchor(anchor) - f.p("head")
    n = np.linalg.norm(to)
    if n < 1e-9:
        return 0.0, {"deg": 0.0}
    ang = math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(fwd, to / n))))))
    return norm_err(ang - cone_deg, cone_deg * DEFAULT_SCALE_K), {"deg": round(ang, 2)}


# ---------------------------------------------------------------------------
# B2. 关节关系（描述「姿势形状」，不是「端点在哪」）
# ---------------------------------------------------------------------------

def _dir_between(f: Frame, a: str, b: str) -> np.ndarray:
    d = f.p(b) - f.p(a)
    n = np.linalg.norm(d)
    return d / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])


@predicate("hand_elbow_gap", "at_end", 1.5)
def hand_elbow_gap(f: Frame, side="right", min_mm=80.0):
    """手到肘的竖直间距。防「肘贴着手」（求解器偷懒时肘会缩到手上）。

    参考姿态手肘差 97mm；求解器给的只有 20mm——`elbow_below_hand` 抓不到这个，
    因为它只判「谁高谁低」，不判「差多少」。
    """
    dz = f.p(f"{side}Hand")[2] - f.p(f"{side}LowerArm")[2]
    excess = min_mm / 1000.0 - dz
    return norm_err(excess * 1000.0, 60.0), {
        "gap_mm": round(dz * 1000, 1), "min_mm": min_mm}


@predicate("elbow_below_hand", "at_end", 1.2)
def elbow_below_hand(f: Frame, side="right", margin_mm=0.0):
    """肘必须低于手（挥手的基本形状：手举高，肘在下面）。"""
    elbow_z = f.p(f"{side}LowerArm")[2]
    hand_z = f.p(f"{side}Hand")[2]
    excess = (elbow_z - hand_z) + margin_mm / 1000.0
    return norm_err(excess * 1000.0, 80.0), {
        "elbow_z_mm": round(elbow_z * 1000, 1), "hand_z_mm": round(hand_z * 1000, 1)}


@predicate("forearm_upright", "at_end", 1.2)
def forearm_upright(f: Frame, side="right", max_deg=40.0):
    """前臂方向与竖直的夹角。挥手时前臂要立起来。"""
    d = _dir_between(f, f"{side}LowerArm", f"{side}Hand")
    ang = math.degrees(math.acos(max(-1.0, min(1.0, abs(float(d[2]))))))
    # d[2] 取绝对值：向上或向下都算「竖直」，但挥手时手在上，用有符号更准
    ang_signed = math.degrees(math.acos(max(-1.0, min(1.0, float(d[2])))))
    return norm_err(ang_signed - max_deg, max_deg), {"deg": round(ang_signed, 1)}


@predicate("arm_not_crossed", "at_end", 1.2)
def arm_not_crossed(f: Frame, side="right", tol_mm=20.0):
    """手臂不能跨过身体中线（跨过去就像投降/抱胸）。"""
    sh = f.p(f"{side}Shoulder")[0]
    hd = f.p(f"{side}Hand")[0]
    # 右手应该在肩的负侧（外侧）：hand_x < shoulder_x
    if side == "right":
        excess = hd - sh + tol_mm / 1000.0
    else:
        excess = sh - hd + tol_mm / 1000.0
    return norm_err(excess * 1000.0, 100.0), {
        "shoulder_x_mm": round(sh * 1000, 1), "hand_x_mm": round(hd * 1000, 1)}


@predicate("elbow_outside", "at_end", 0.8)
def elbow_outside(f: Frame, side="right", tol_mm=0.0):
    """肘不能在身体内侧（肘朝躯干里，手就拧着）。"""
    sh = f.p(f"{side}Shoulder")[0]
    el = f.p(f"{side}LowerArm")[0]
    if side == "right":
        excess = el - sh + tol_mm / 1000.0
    else:
        excess = sh - el + tol_mm / 1000.0
    return norm_err(excess * 1000.0, 80.0), {"elbow_x_mm": round(el * 1000, 1)}


@predicate("hand_above_shoulder", "at_end", 1.0)
def hand_above_shoulder(f: Frame, side="right", margin_mm=0.0):
    """手高于肩（比「高于头顶」宽松，适合打招呼/挥手）。"""
    sh = f.p(f"{side}Shoulder")[2]
    hd = f.p(f"{side}Hand")[2]
    excess = (sh - hd) + margin_mm / 1000.0
    return norm_err(excess * 1000.0, 80.0), {"hand_z_mm": round(hd * 1000, 1)}

def _airborne_ok(f: Frame) -> bool:
    return f.meta.get("skip_airborne_check", False)


@predicate("elbow_below_shoulder", "at_end", 1.5)
def elbow_below_shoulder(f: Frame, side="right", margin_mm=0.0):
    """肘必须低于肩——防「肘朝天」。

    挥手时肘在肩下方，前臂从那里往上抬。肘高于肩就变成「举旗」。
    """
    sh = f.p(f"{side}Shoulder")[2]
    el = f.p(f"{side}LowerArm")[2]
    excess = (el - sh) + margin_mm / 1000.0
    return norm_err(excess * 1000.0, 60.0), {
        "elbow_z_mm": round(el * 1000, 1), "shoulder_z_mm": round(sh * 1000, 1)}


@predicate("elbow_behind_hand", "at_end", 1.0)
def elbow_behind_hand(f: Frame, side="right"):
    """肘比手更靠身体中线外侧（挥手时肘不外翻到身前）。"""
    sh = f.p(f"{side}Shoulder")[0]
    el = f.p(f"{side}LowerArm")[0]
    hd = f.p(f"{side}Hand")[0]
    # 右手：肘应该比手更靠外（更负）
    if side == "right":
        excess = (el - hd) - abs(sh) * 0.1
    else:
        excess = (hd - el) - abs(sh) * 0.1
    return norm_err(excess * 1000.0, 60.0), {
        "elbow_x_mm": round(el * 1000, 1), "hand_x_mm": round(hd * 1000, 1)}


@predicate("elbow_angle", "at_end", 1.5)
def elbow_angle(f: Frame, side="right", min_deg=80.0, max_deg=140.0):
    """肩-肘-手的夹角。180° = 直臂，90° = 直角。

    下限防反关节（<30° 说明肘折回去了），上限防直臂（>150° 像举旗）。
    挥手典型 80–140°。
    """
    sh = f.p(f"{side}Shoulder")
    el = f.p(f"{side}LowerArm")
    hd = f.p(f"{side}Hand")
    v1 = sh - el
    v2 = hd - el
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0, {"skipped": "degenerate"}
    c = float(np.dot(v1, v2) / (n1 * n2))
    deg = math.degrees(math.acos(max(-1.0, min(1.0, c))))
    if deg < min_deg:
        excess = min_deg - deg
    elif deg > max_deg:
        excess = deg - max_deg
    else:
        excess = 0.0
    return norm_err(excess, 30.0), {"deg": round(deg, 1),
                                    "range": [min_deg, max_deg]}


@predicate("no_hyperextension", "at_end", 2.0)
def no_hyperextension(f: Frame, side="right", min_deg=25.0):
    """反关节检测：肘夹角小于 min_deg 说明折反了。"""
    sh = f.p(f"{side}Shoulder")
    el = f.p(f"{side}LowerArm")
    hd = f.p(f"{side}Hand")
    v1 = sh - el
    v2 = hd - el
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0, {"skipped": "degenerate"}
    c = float(np.dot(v1, v2) / (n1 * n2))
    deg = math.degrees(math.acos(max(-1.0, min(1.0, c))))
    return norm_err(min_deg - deg, 20.0), {"deg": round(deg, 1)}


@predicate("wrist_neutral", "at_end", 1.0)
def wrist_neutral(f: Frame, side="right", max_deg=50.0):
    """手腕相对前臂的偏折角。超过 max_deg 就像手腕折断了。"""
    la = f.p(f"{side}LowerArm")
    hd = f.p(f"{side}Hand")
    # 手骨再往前一段（用手的局部 Y 轴，长度取骨长）
    hand_m = f.M[f"{side}Hand"]
    tip = hand_m[0:3, 3] + hand_m[0:3, 1] * 0.04
    v1 = hd - la
    v2 = tip - hd
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 0.0, {"skipped": "degenerate"}
    c = float(np.dot(v1, v2) / (n1 * n2))
    deg = math.degrees(math.acos(max(-1.0, min(1.0, c))))
    return norm_err(deg - max_deg, max_deg), {"deg": round(deg, 1)}


# ---------------------------------------------------------------------------
# C. 支撑
# ---------------------------------------------------------------------------

@predicate("foot_grounded", "throughout", 2.0)
def foot_grounded(f: Frame, side="left", tol_mm=35.0):
    if _airborne_ok(f):
        return 0.0, {"skipped": "airborne"}
    c = f.contacts.get(f"{side}Foot")
    if c is None:
        return 0.0, {"skipped": "no_contact_data"}
    return norm_err(c.height_mm - tol_mm, tol_mm * DEFAULT_SCALE_K), {
        "h_mm": round(c.height_mm, 2), "tol_mm": tol_mm}


@predicate("foot_lifted", "at_any", 1.0)
def foot_lifted(f: Frame, side="left", min_mm=40.0):
    c = f.contacts.get(f"{side}Foot")
    if c is None:
        return 0.0, {"skipped": "no_contact_data"}
    return norm_err(min_mm - c.height_mm, min_mm * DEFAULT_SCALE_K), {
        "h_mm": round(c.height_mm, 2), "min_mm": min_mm}


@predicate("dual_support", "throughout", 1.5)
def dual_support(f: Frame, min_sides=2):
    if _airborne_ok(f):
        return 0.0, {"skipped": "airborne"}
    have = sum(1 for c in f.contacts.values() if c.grounded)
    miss = max(0, int(min_sides) - have)
    return norm_err(miss * 1.0, max(1.0, float(min_sides))), {
        "grounded_sides": have, "need": min_sides}


@predicate("no_ground_penetration", "throughout", 2.5)
def no_ground_penetration(f: Frame, side="left", tol_mm=3.0):
    c = f.contacts.get(f"{side}Foot")
    if c is None:
        return 0.0, {"skipped": "no_contact_data"}
    return norm_err(c.penetration_mm - tol_mm, tol_mm * 4.0), {
        "pen_mm": round(c.penetration_mm, 2)}


# ---------------------------------------------------------------------------
# D. 空间
# ---------------------------------------------------------------------------

@predicate("no_self_collision", "throughout", 2.5)
def no_self_collision(f: Frame, tol_mm=0.0):
    """胶囊近似：只用 ctx.meta['capsule_penetration_mm']（由 solver 预先算好）。

    真正的网格求交是 L3 的 BVH。这里只消费一个标量。
    """
    pen = float(f.meta.get("capsule_penetration_mm", 0.0))
    return norm_err(pen - tol_mm, 20.0), {"pen_mm": round(pen, 2)}


@predicate("inside_workspace", "throughout", 1.5)
def inside_workspace(f: Frame, box_m=2.0):
    worst = 0.0
    for b in ("leftHand", "rightHand", "head", "leftFoot", "rightFoot"):
        p = f.p(b)
        over = max(abs(p[0]) - box_m, abs(p[1]) - box_m, 0.0)
        worst = max(worst, over)
    return norm_err(worst * 1000.0, 200.0), {"over_mm": round(worst * 1000, 1)}


@predicate("torso_upright", "throughout", 1.0)
def torso_upright(f: Frame, max_deg=35.0):
    got = _pitch_deg(f, "chest")
    return norm_err(got - max_deg, max_deg * 0.5), {"deg": round(got, 2)}


# ---------------------------------------------------------------------------
# E. 动态（帧间）
# ---------------------------------------------------------------------------

def _max_step(f: Frame, prev: Frame, bones) -> tuple[float, str]:
    worst, who = 0.0, ""
    for b in bones:
        if b in f.M and b in prev.M:
            d = float(np.linalg.norm(f.p(b) - prev.p(b))) * 1000.0
            if d > worst:
                worst, who = d, b
    return worst, who


@predicate("no_big_step", "throughout", 3.0)
def no_big_step(f: Frame, prev=None, budget_mm=50.0):
    if prev is None:
        return 0.0, {"skipped": "first_frame"}
    d, who = _max_step(f, prev, ("leftHand", "rightHand", "head", "leftFoot", "rightFoot"))
    return norm_err(d - budget_mm, budget_mm), {"mm": round(d, 1), "bone": who}


@predicate("no_micro_jitter", "at_end", 0.5)
def no_micro_jitter(f: Frame, prev=None, min_mm=0.5):
    if prev is None:
        return 0.0, {"skipped": "first_frame"}
    d, _ = _max_step(f, prev, ("leftHand", "rightHand", "head"))
    return norm_err(min_mm - d, max(min_mm, 1e-6)), {"mm": round(d, 3)}


# ---------------------------------------------------------------------------
# F. 轨迹（需要整段）
# ---------------------------------------------------------------------------

@predicate("foot_slip", "throughout", 2.5)
def foot_slip(f: Frame, history=None, tol_mm=3.0):
    """支撑相内脚底世界位移。history = 该脚已支撑帧的世界坐标列表。"""
    if not history or len(history) < 2:
        return 0.0, {"skipped": "not_enough_frames"}
    p0 = np.asarray(history[0])
    worst = max(float(np.linalg.norm(np.asarray(h) - p0)) * 1000.0 for h in history)
    return norm_err(worst - tol_mm, tol_mm * DEFAULT_SCALE_K), {"slip_mm": round(worst, 2)}


@predicate("jerk_rms", "throughout", 0.5)
def jerk_rms(f: Frame, value=None, budget=1.0):
    if value is None:
        return 0.0, {"skipped": "no_value"}
    return norm_err(float(value) - budget, budget), {"jerk": round(float(value), 3)}


@predicate("duty_match", "throughout", 1.5)
def duty_match(f: Frame, target_duty=0.5, tol=0.08):
    return norm_err(abs(f.duty - target_duty) - tol, tol * DEFAULT_SCALE_K), {
        "duty": round(f.duty, 3), "target": target_duty}


@predicate("max_deg_per_frame", "throughout", 3.0)
def max_deg_per_frame(f: Frame, value=None, budget=12.0):
    if value is None:
        return 0.0, {"skipped": "no_value"}
    return norm_err(float(value) - budget, budget * 0.5), {
        "deg": round(float(value), 2), "budget": budget}


# ---------------------------------------------------------------------------
# 求值
# ---------------------------------------------------------------------------

def spec(name: str) -> Spec:
    if name not in PREDICATES:
        raise KeyError(f"未知谓词 {name!r}；可用：{sorted(PREDICATES)[:8]}…")
    return PREDICATES[name]


def evaluate_one(name: str, frame: Frame, **args) -> tuple[float, dict]:
    s = spec(name)
    return s.fn(frame, **args)


def evaluate_series(name: str, frames: list[Frame], **args) -> tuple[float, int | None, dict]:
    """对一段帧序列求值，按时间语义合成。"""
    s = spec(name)
    errs, details = [], {}
    for i, fr in enumerate(frames):
        e, d = s.fn(fr, **args)
        errs.append(float(e))
        if e > 0 and not details:
            details = d
            details["_frame"] = i
    err, worst = merge(errs, s.sem)
    return err, worst, details


# ---------------------------------------------------------------------------
# B3. 生理范围（「自然度底线」用；LLM 不知道该写这些）
# ---------------------------------------------------------------------------

def _angle_at(f: Frame, a: str, b: str, c: str) -> float:
    """a-b-c 三点在 b 处的夹角（度）。b 是顶点。"""
    pa, pb, pc = f.p(a), f.p(b), f.p(c)
    v1, v2 = pa - pb, pc - pb
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-9 or n2 < 1e-9:
        return 180.0
    c_ = float(np.dot(v1, v2) / (n1 * n2))
    return float(math.degrees(math.acos(max(-1.0, min(1.0, c_)))))


@predicate("knee_angle", "at_end", 1.5)
def knee_angle(f: Frame, side="left", min_deg=0.0, max_deg=155.0):
    """髋-膝-踝夹角。膝盖不能反折（<0），也不能过度弯（>155）。"""
    deg = _angle_at(f, f"{side}UpperLeg", f"{side}LowerLeg", f"{side}Foot")
    if deg < min_deg:
        excess = min_deg - deg
    elif deg > max_deg:
        excess = deg - max_deg
    else:
        excess = 0.0
    return norm_err(excess, 30.0), {"deg": round(deg, 1),
                                    "range": [min_deg, max_deg]}


@predicate("shoulder_range", "at_end", 1.0)
def shoulder_range(f: Frame, side="right", max_deg=175.0):
    """上臂与躯干（脊柱方向）的夹角。超 175° = 手臂反折向上/向后。"""
    deg = _angle_at(f, f"{side}UpperArm", f"{side}Shoulder", "chest")
    excess = deg - max_deg
    return norm_err(excess, 30.0), {"deg": round(deg, 1), "max_deg": max_deg}


@predicate("balance", "throughout", 1.5)
def balance(f: Frame, tol_mm=220.0):
    """重心（髋部）投影到两脚中点的水平距离。

    简化版平衡：不建支撑多边形，只要求重心在双脚中点附近。
    站立/走路都够用；跳跃/单脚站立会误报，那种场景要单独处理。
    """
    hips = f.p("hips")
    mid = (f.p("leftFoot") + f.p("rightFoot")) * 0.5
    d = float(np.linalg.norm((hips - mid)[0:2]) * 1000.0)
    return norm_err(d - tol_mm, tol_mm * DEFAULT_SCALE_K), {
        "d_mm": round(d, 1), "tol_mm": tol_mm}

@predicate("knee_forward", "at_end", 2.0)
def knee_forward(f: Frame, side="left", min_mm=10.0):
    """膝盖必须在髋-踝连线的**前方**（脸朝 -Y，所以是 y 更负）。

    `knee_angle` 只查夹角范围，**抓不到反折**：小腿绕世界 X 正转 40°（正常）
    和负转 40°（反折）的夹角都落在 0-180 内。实测两者 `knee_angle` err 都是 0。

    这个判据查的是**方向**：膝的 y 必须比髋-踝中点更负（更靠前）。
    """
    hip = f.p(f"{side}UpperLeg")
    knee = f.p(f"{side}LowerLeg")
    ankle = f.p(f"{side}Foot")
    mid_y = (hip[1] + ankle[1]) * 0.5
    forward = (mid_y - knee[1]) * 1000.0     # 正数 = 膝在连线前方
    excess = min_mm - forward
    return norm_err(excess, 60.0), {
        "forward_mm": round(forward, 1), "min_mm": min_mm}


@predicate("elbow_forward", "at_end", 1.5)
def elbow_forward(f: Frame, side="right", min_mm=-40.0):
    """肘相对肩-手连线的前后位置（挥手时肘略在后方，不是硬性要求）。

    默认 min_mm=-40 表示「允许肘在连线后方 40mm 内」，比 knee 宽松——
    手臂的活动度比腿大得多，硬卡方向会解不出来。
    """
    sh = f.p(f"{side}Shoulder")
    el = f.p(f"{side}LowerArm")
    hd = f.p(f"{side}Hand")
    mid_y = (sh[1] + hd[1]) * 0.5
    forward = (mid_y - el[1]) * 1000.0
    excess = min_mm - forward
    return norm_err(excess, 80.0), {
        "forward_mm": round(forward, 1), "min_mm": min_mm}
