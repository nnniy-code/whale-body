"""core/grow.py — 族自动生长：从库里反复出现的谓词组合，提议/生成新族。

设计依据：用户 2026-11-24 —— 「如果新动作都进 free 族，那和动作库有什么区别」

**核心区别**：
  库条目 = 一次具体执行（一组参数，无语义）
  族     = 一类动作的定义（参数范围 + 判据模板，有语义）

所以新动作不应该永远留在 `free` 族里：
  同一个「谓词组合」反复出现 N 次 → 提议成新族 → 下次直接走新族（快、稳）

**族名从谓词生成**，不需要人工起名：
  hand_near_anchor(head_top) + elbow_angle → `reach_head_top`
  torso_lean + head_nod                    → `lean_forward`
"""

from __future__ import annotations

import hashlib
import json
import os
import re

# 谓词 → 族名的短词映射（生成的族名要能看懂）
WORD = {
    "hand_near_anchor": "reach", "hand_above_head": "raise", "hand_above_shoulder": "lift",
    "hand_below_waist": "lower", "hand_at_point": "point", "foot_at_point": "step",
    "foot_grounded": "stand", "foot_lifted": "step",
    "elbow_angle": "bend", "forearm_upright": "upright", "arm_not_crossed": "side",
    "hand_outside_body": "out", "elbow_outside": "out", "elbow_below_hand": "down",
    "wrist_neutral": "wrist", "no_hyperextension": "safe",
    "head_nod": "nod", "head_tilt": "tilt", "head_yaw": "turn",
    "torso_lean": "lean", "torso_upright": "upright", "gaze_toward": "gaze",
    "no_self_collision": "safe", "no_ground_penetration": "safe",
    "dual_support": "stand", "tail_tip_at_anchor": "tail",
}

# 这些是「底线」，不参与造名（每个族都有）
BASELINE = {"torso_upright", "no_self_collision", "no_ground_penetration",
            "no_hyperextension", "foot_grounded", "inside_workspace"}

# 「弱」谓词：安全/中立项，换个说法就会多一条或少一条，但不改变动作身份。
# 归一化时丢掉——**只丢这些**，别的都不动，避免把不同动作并成一个。
WEAK = {"wrist_neutral", "no_hyperextension", "torso_upright",
        "no_self_collision", "no_ground_penetration", "inside_workspace"}


def normalize_predicates(predicates: list[dict]) -> list[dict]:
    """把谓词组合规范化：去弱谓词、统一数值粒度、排序。

    **只做这些**——不合并名字相近的谓词，不改 side，不动 anchor。
    归一化越激进，越可能把「意思像、动作不像」的两个动作并成一个。
    """
    out = []
    for p in predicates or []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name") or "").strip()
        if not name or name in WEAK:
            continue
        args = {}
        for k, v in (p.get("args") or {}).items():
            if k in ("tol_mm", "tol_deg", "deg", "max_deg", "min_deg") and isinstance(v, (int, float)):
                step = 50 if k == "tol_mm" else 10
                args[k] = int(round(float(v) / step) * step)
            elif isinstance(v, float):
                args[k] = round(v, 1)
            else:
                args[k] = v
        out.append({"name": name, "args": args})
    out.sort(key=lambda x: json.dumps(x, sort_keys=True, ensure_ascii=False))
    return out


def fingerprint(predicates: list[dict]) -> str:
    """谓词组合的指纹（名字 + 关键参数）。**动作身份的唯一依据。**"""
    key = []
    for p in normalize_predicates(predicates):
        args = p.get("args") or {}
        keep = {k: v for k, v in sorted(args.items())
                if k in ("anchor", "side")}          # 只留真正改变动作的参数
        key.append((p["name"], json.dumps(keep, sort_keys=True, ensure_ascii=False)))
    return hashlib.md5(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:12]


_TAIL = "的了呀啊吧嘛呢哦啦"
_STRIP = re.compile(r"[\s，。,.!！?？~～…、·—\-]+")


def norm_label(text: str) -> str:
    """名字归一化：**只用于显示和去重名字**，绝不参与动作合并。"""
    s = _STRIP.sub("", str(text or ""))
    return s.strip(_TAIL)


def merge_labels(proposals: list[dict]) -> list[dict]:
    """同一指纹的多条提议 → 一条，名字取最常见的那个。

    注意：这里**只按 fingerprint 合并**，不看名字像不像。
    """
    groups: dict[str, list[dict]] = {}
    for p in proposals:
        groups.setdefault(p["fingerprint"], []).append(p)
    out = []
    for fp, ps in groups.items():
        base = dict(max(ps, key=lambda x: x.get("count", 0)))
        base["count"] = sum(int(x.get("count", 0)) for x in ps)
        labels = [norm_label(x.get("label")) for x in ps if x.get("label")]
        if labels:
            base["label"] = max(set(labels), key=labels.count)
        # 别把 propose 已经收集到的别名冲掉
        alias_set = set(base.get("aliases") or []) | set(labels)
        base["aliases"] = sorted(a for a in alias_set if a)[:5]
        out.append(base)
    out.sort(key=lambda x: -x.get("count", 0))
    return out


def label_collisions(proposals: list[dict]) -> list[dict]:
    """同名但判据不同 → 报警，**不合并**。

    这正是「语义相近、动作不相近」的情况：用户说的是同一个词，
    但谓词不同（比如左手 vs 右手，或 anchor 不同）。
    """
    by_label: dict[str, list[dict]] = {}
    for p in proposals:
        lab = p.get("label") or p.get("name")
        by_label.setdefault(norm_label(lab) or lab, []).append(p)
    out = []
    for lab, ps in by_label.items():
        if len({p["fingerprint"] for p in ps}) > 1:
            out.append({"label": lab,
                        "fingerprints": [p["fingerprint"] for p in ps],
                        "counts": [p.get("count", 0) for p in ps]})
    return out


def make_name(predicates: list[dict]) -> str:
    """从谓词生成可读的族名。"""
    words = []
    for p in predicates or []:
        name = p.get("name")
        if name in BASELINE:
            continue
        w = WORD.get(name)
        if not w:
            continue
        args = p.get("args") or {}
        anchor = args.get("anchor")
        if anchor and w in ("reach", "point"):
            w = f"{w}_{str(anchor).replace('.', '_')}"
        if w not in words:
            words.append(w)
    if not words:
        return "auto_action"
    return "_".join(words[:3])


def propose(library, min_count: int = 3, only_family: str = "free") -> list[dict]:
    """从库里找出「反复出现的谓词组合」，提议新族。

    返回 [{"name", "fingerprint", "count", "predicates_template", "P_range", "entries"}]
    """
    # 「反复出现」的判据是**命中次数**（同一个动作被请求多次），
    # 不是条目数——库会去重，同一个动作只会存一条。
    groups: dict[str, list] = {}
    for e in library.entries.values():
        if e.status != "verified":
            continue
        if only_family and e.family != only_family:
            continue
        preds = getattr(e, "predicates", None) or []
        if not preds:
            continue
        hits = int((e.metrics or {}).get("hits", 0))
        if hits <= 0:
            continue
        groups.setdefault(fingerprint(preds), []).append((e, hits))

    out = []
    for fp, pairs in groups.items():
        total = sum(h for _e, h in pairs)
        if total < min_count:
            continue
        es = [e for e, _h in pairs]        # P_range：从这些条目的参数算 min/max（留 10% 余量）
        keys = set()
        for e in es:
            keys |= set((e.params or {}).get("P", {}).keys())
        P_range = {}
        for k in keys:
            vals = [float((e.params or {}).get("P", {}).get(k, 0.0)) for e in es]
            lo, hi = min(vals), max(vals)
            pad = max(2.0, (hi - lo) * 0.1)
            P_range[k] = [round(lo - pad, 1), round(hi + pad, 1)]
        out.append({
            "name": make_name(es[0].predicates),
            "fingerprint": fp,
            "count": total,
            "predicates_template": [dict(p) for p in es[0].predicates],
            "P_range": P_range,
            # 用户原话（最近一次），面板上显示成「手叉腰」
            "label": next((norm_label((e.metrics or {}).get("label")) for e in es
                           if (e.metrics or {}).get("label")), ""),
            "aliases": sorted({norm_label(l) for e in es
                               for l in ((e.metrics or {}).get("labels") or [])} |
                              {norm_label((e.metrics or {}).get("label")) for e in es
                               if (e.metrics or {}).get("label")})[:5],
            "entries": [e.id for e in es],
        })
    out.sort(key=lambda x: -x["count"])
    return out


def apply_proposals(families: dict, proposals: list[dict],
                    min_count: int = 3,
                    template_family: dict | None = None) -> tuple[dict, list[dict]]:
    """把提议写进族表（不覆盖已有族，同名加后缀）。

    `template_family` 用 `free` 族当底——新族要能直接跑，就得有完整的
    label / G / thresholds / predicates_hint，光有 P_range 会在校验里挂掉。
    """
    fams = dict(families)
    base_fam = template_family or families.get("free") or next(iter(families.values()))
    added = []
    for p in proposals:
        if p["count"] < min_count:
            continue
        name = p["name"]
        base = name
        n = 2
        while name in fams:
            name = f"{base}_{n}"
            n += 1
        preds = [dict(q) for q in p["predicates_template"]]
        fams[name] = {
            "label": (p.get("label") or "").strip() or name,
            "G": dict(base_fam.get("G") or {}),
            "P_default": {k: round((v[0] + v[1]) / 2.0, 1) for k, v in p["P_range"].items()},
            "P_range": {k: list(v) for k, v in p["P_range"].items()},
            "thresholds": dict(base_fam.get("thresholds") or {}),
            "predicates_hint": [q["name"] for q in preds][:3],
            "predicates_template": preds,
            "auto": True,
            "fingerprint": p["fingerprint"],
            "evidence_count": p["count"],
        }
        added.append({"name": name, "count": p["count"], "from": base})
    return fams, added


def merge_auto(families: dict, path: str) -> dict:
    """把自动生长的族并进来（不覆盖手工族）。"""
    out = dict(families)
    data = load_auto(path)
    for k, v in (data.get("families") or {}).items():
        if k not in out:
            out[k] = v
    return out


def load_auto(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {"families": {}, "dismissed": []}
    try:
        d = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {"families": {}, "dismissed": []}
    d.setdefault("families", {})
    d.setdefault("dismissed", [])
    return d


def save_auto(path: str, data: dict) -> str:
    """原子写。自动长大的族存在**沙箱**里，不污染 config/。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return path


def record_family(path: str, family: dict) -> str:
    """把一个新族写进 auto 文件（按 fingerprint 去重）。"""
    data = load_auto(path)
    fp = family.get("fingerprint")
    fams = data["families"]
    if not any((v or {}).get("fingerprint") == fp for v in fams.values()):
        fams[family.get("name_hint") or f"auto_{fp}"] = family
    return save_auto(path, data)

