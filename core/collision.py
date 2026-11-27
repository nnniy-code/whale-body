"""core/collision.py — 穿模判定与归因（纯逻辑，不碰 bpy）。

设计依据：docs/design/13-collision.md

分工：
  - 本模块：阈值查表、违规判定、**归因到骨**、罚项构造
  - Blender 端（tools/l3_verify.py）：建 BVH、三角形求交、算穿透深度

为什么归因这么重要：直接把「相交三角形对数」当罚项，CMA-ES 只知道
「这组不好」，不知道改哪一维——罚项就变成噪声。
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

DEFAULT_MM = 2.0

REGION_PREFIX = "REGION_"

# 骨对预筛缓存：key = (id(rig), 骨集合, chain_skip) → [(a,b)]
_PAIR_CACHE: dict = {}


def load_table(path: str | None = None) -> dict:
    if path is None:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "collision.json",
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def pair_key(a: str, b: str) -> str:
    """规范化成 'a|b'，按字母序（保证 (arm,head) 和 (head,arm) 同键）。"""
    return "|".join(sorted([a.lower(), b.lower()]))


def lookup(table: dict, region_a: str, region_b: str) -> tuple[float, str]:
    """返回 (allowed_mm, mode)。mode: 'abs' | 'delta'。"""
    key = pair_key(region_a, region_b)
    d = table.get("pairs", {}).get(key)
    if d is None:
        d = table.get("default", {"allowed_mm": DEFAULT_MM})
    return float(d.get("allowed_mm", DEFAULT_MM)), d.get("mode", "abs")


def baseline_depth(table: dict, region_a: str, region_b: str) -> float:
    """baseline 的键也做规范化，'head|hair' 和 'hair|head' 都认。"""
    want = pair_key(region_a, region_b)
    for k, v in (table.get("baseline") or {}).items():
        parts = k.split("|")
        if len(parts) == 2 and pair_key(parts[0], parts[1]) == want:
            return float(v)
    return 0.0


def is_violation(table: dict, region_a: str, region_b: str, depth_mm: float) -> tuple[bool, float]:
    """返回 (是否违规, 超出的 mm 数)。"""
    allowed, mode = lookup(table, region_a, region_b)
    effective = depth_mm
    if mode == "delta":
        effective = depth_mm - baseline_depth(table, region_a, region_b)
    over = effective - allowed
    return over > 0.0, over


def attribute(tri_pairs, bone_of_tri, region_of_bone, table: dict,
              frame: int = 0, move_amount=None):
    """把相交三角形对归因到骨。

    tri_pairs: [(tri_a, tri_b, depth_mm), ...]
    bone_of_tri: 可调用，tri -> 骨名（取该三角形权重最大的顶点组）
    region_of_bone: 可调用，骨名 -> region（arm/body/hair/...）
    move_amount: 可调用，骨名 -> 该帧位移量（用于决定罚哪根）

    返回 [{"frame","bone","other","regions","depth_mm","allowed_mm","over_mm"}]
    """
    out = []
    for tri_a, tri_b, depth in tri_pairs:
        ba = bone_of_tri(tri_a)
        bb = bone_of_tri(tri_b)
        ra = region_of_bone(ba)
        rb = region_of_bone(bb)
        bad, over = is_violation(table, ra, rb, depth)
        if not bad:
            continue
        # 归因给「该帧动得更多」的那根
        if move_amount is None:
            culprit = ba
        else:
            culprit = ba if move_amount(ba) >= move_amount(bb) else bb
        other = bb if culprit == ba else ba
        allowed, _mode = lookup(table, ra, rb)
        out.append({
            "frame": int(frame),
            "bone": culprit,
            "other": other,
            "regions": sorted([ra, rb]),
            "depth_mm": round(float(depth), 3),
            "allowed_mm": allowed,
            "over_mm": round(float(over), 3),
        })
    return out


def summarize(violations: list[dict]) -> dict:
    if not violations:
        return {"count": 0, "worst_mm": 0.0, "worst_bone": None, "max_pen": 0.0}
    worst = max(violations, key=lambda v: v["over_mm"])
    return {
        "count": len(violations),
        "worst_mm": worst["over_mm"],
        "worst_bone": worst["bone"],
        "max_pen": max(v["depth_mm"] for v in violations),
    }


def penalty(violations: list[dict], scale_mm: float = 10.0) -> float:
    """把归因结果变成一个标量罚项（用于回灌重搜）。

    按骨聚合，取每根骨的最大超出量——不要累加（累加会让一根骨的多次
    浅穿插压过另一根的深穿插）。
    """
    if not violations:
        return 0.0
    per_bone: dict[str, float] = {}
    for v in violations:
        per_bone[v["bone"]] = max(per_bone.get(v["bone"], 0.0), v["over_mm"])
    return sum(per_bone.values()) / scale_mm


def guilty_bones(violations: list[dict]) -> list[str]:
    return sorted({v["bone"] for v in violations})


# ---------------------------------------------------------------------------
# 胶囊碰撞（求解期快速档）
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4096)
def region_of(bone: str | None) -> str:
    if not bone:
        return "body"
    x = bone.lower()
    if "hair" in x or "bangs" in x:
        return "hair"
    if "skirt" in x:
        return "skirt"
    if "ribbon" in x:
        return "ribbon"
    if "whale" in x or "tail" in x:
        return "tail"
    if "head" in x or "neck" in x:
        return "head"
    if any(k in x for k in ("hand", "arm", "shoulder", "finger",
                            "thumb", "index", "middle", "ring", "pinky")):
        return "arm"
    if any(k in x for k in ("leg", "foot", "toe")):
        return "leg"
    return "body"


def seg_seg_distance(p1, q1, p2, q2) -> float:
    """两条线段的最近距离（numpy 3 维）。"""
    import numpy as np
    p1 = np.asarray(p1, float)
    q1 = np.asarray(q1, float)
    p2 = np.asarray(p2, float)
    q2 = np.asarray(q2, float)
    d1 = q1 - p1
    d2 = q2 - p2
    r = p1 - p2
    a = float(d1 @ d1)
    e = float(d2 @ d2)
    f = float(d2 @ r)
    if a < 1e-12 and e < 1e-12:
        return float(np.linalg.norm(p1 - p2))
    if a < 1e-12:
        s = 0.0
        t = min(max(f / e, 0.0), 1.0)
    else:
        c = float(d1 @ r)
        if e < 1e-12:
            t = 0.0
            s = min(max(-c / a, 0.0), 1.0)
        else:
            b = float(d1 @ d2)
            den = a * e - b * b
            s = min(max((b * f - c * e) / den, 0.0), 1.0) if den > 1e-12 else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t = 0.0
                s = min(max(-c / a, 0.0), 1.0)
            elif t > 1.0:
                t = 1.0
                s = min(max((b - c) / a, 0.0), 1.0)
    return float(np.linalg.norm((p1 + d1 * s) - (p2 + d2 * t)))


def _depth(rig, n: str) -> int:
    d, p = 0, rig.parent.get(n)
    while p is not None:
        d += 1
        p = rig.parent.get(p)
    return d


def chain_distance(rig, a: str, b: str) -> int:
    """两骨在骨树上的距离（跳数）。"""
    if a == b:
        return 0

    def anc(n):
        out, p = [], rig.parent.get(n)
        while p is not None:
            out.append(p)
            p = rig.parent.get(p)
        return out

    aa, ab = anc(a), anc(b)
    da, db = len(aa), len(ab)
    if a in ab:
        return db - da            # b 是 a 的祖先
    if b in aa:
        return da - db            # a 是 b 的祖先
    common = set(aa) & set(ab)
    if not common:
        return 99
    # 最近公共祖先：在 aa 里下标最小
    lca = min(common, key=lambda x: aa.index(x))
    dl = len(anc(lca))
    return (da - dl) + (db - dl)


def eligible_pairs(capsules: dict, rig, chain_skip: int = 3, only=None) -> list:
    """预筛：跨 region 且链距 > chain_skip 的骨对。按 rig/骨集缓存。"""
    names = tuple(sorted(n for n in capsules if only is None or n in only))
    key = (id(rig), names, int(chain_skip))
    hit = _PAIR_CACHE.get(key)
    if hit is not None:
        return hit
    pairs = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            if region_of(a) == region_of(b):
                continue
            if chain_distance(rig, a, b) <= chain_skip:
                continue
            pairs.append((a, b))
    _PAIR_CACHE[key] = pairs
    return pairs


def capsule_gaps(M, capsules: dict, rig, lengths: dict | None = None,
                 chain_skip: int = 3, only=None):
    """返回 {(bone_a, bone_b): gap_m}，只含跨 region 且链距 > chain_skip 的对。

    gap < 0 表示两胶囊相交。
    lengths: {bone: 骨长（米）}，从 config/bones_map.json 的 len_mm/1000 取。
    """
    lengths = lengths or {}
    out = {}
    for a, b in eligible_pairs(capsules, rig, chain_skip, only):
        if a not in M or b not in M:
            continue
        ma, mb = M[a], M[b]
        pa = ma[0:3, 3]
        qa = pa + ma[0:3, 1] * float(lengths.get(a, 0.0))
        pb = mb[0:3, 3]
        qb = pb + mb[0:3, 1] * float(lengths.get(b, 0.0))
        d = seg_seg_distance(pa, qa, pb, qb)
        out[(a, b)] = d - (capsules[a]["r50"] + capsules[b]["r50"])
    return out


def baseline_gaps(M_rest, capsules: dict, rig, lengths=None, chain_skip: int = 3, only=None) -> dict:
    """静止姿态的 gap 基线。启动时跑一次。"""
    return capsule_gaps(M_rest, capsules, rig, lengths, chain_skip, only)


def check_capsules(M, capsules: dict, rig, table: dict, baseline: dict | None = None,
                   lengths=None, chain_skip: int = 3, only=None) -> list[dict]:
    """胶囊穿模检测。违规 = 当前 gap 比基线还小，且超出该 region 对的 allowed_mm。"""
    gaps = capsule_gaps(M, capsules, rig, lengths, chain_skip, only)
    base = baseline or {}
    out = []
    for (a, b), gap in gaps.items():
        b0 = float(base.get((a, b), max(0.0, gap)))
        if b0 < 0:
            drop = b0 - gap                     # rest 时已贴着 → 只看「更近了多少」
        else:
            drop = max(0.0, -gap)               # rest 时没贴着 → 直接看是否相交
        if drop <= 0:
            continue
        ra, rb = region_of(a), region_of(b)
        allowed, _mode = lookup(table, ra, rb)
        over_mm = drop * 1000.0 - allowed
        if over_mm <= 0:
            continue
        out.append({
            "bone": a, "other": b, "regions": sorted([ra, rb]),
            "gap_mm": round(gap * 1000.0, 3),
            "drop_mm": round(drop * 1000.0, 3),
            "over_mm": round(over_mm, 3),
        })
    return out


