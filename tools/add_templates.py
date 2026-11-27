"""tools/add_templates.py — 把验证过的谓词组固化进族的 predicates_template。

怎么测：python tools/add_templates.py
产物：config/seed_families.json 里每个族多一个 predicates_template
用途：解决「谓词膨胀」——LLM 只说动作名，系统用模板里的谓词，不用它自己写。
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 每个族的默认谓词模板（都经过 tools/solve_target 系列验证过或据实测设定）
TEMPLATES = {
    "wave": [
        {"name": "hand_near_anchor", "args": {"side": "right", "anchor": "head_top",
                                              "dist_mm": 200, "tol_mm": 40}},
        {"name": "elbow_angle", "args": {"side": "right", "min_deg": 85, "max_deg": 125}},
        {"name": "forearm_upright", "args": {"side": "right", "max_deg": 35}},
        {"name": "arm_not_crossed", "args": {"side": "right"}},
        {"name": "hand_outside_body", "args": {"side": "right", "min_mm": 110}},
    ],
    "pat_head": [
        {"name": "hand_near_anchor", "args": {"side": "right", "anchor": "head_top",
                                              "dist_mm": 80, "tol_mm": 40}},
        {"name": "elbow_angle", "args": {"side": "right", "min_deg": 70, "max_deg": 120}},
        {"name": "arm_not_crossed", "args": {"side": "right"}},
    ],
    "point": [
        {"name": "hand_near_anchor", "args": {"side": "right", "anchor": "head_top",
                                              "dist_mm": 420, "tol_mm": 60}},
        {"name": "elbow_angle", "args": {"side": "right", "min_deg": 140, "max_deg": 178}},
        {"name": "hand_outside_body", "args": {"side": "right", "min_mm": 200}},
    ],
    "stretch": [
        {"name": "hand_above_head", "args": {"side": "right"}},
        {"name": "elbow_angle", "args": {"side": "right", "min_deg": 120, "max_deg": 175}},
        {"name": "torso_lean", "args": {"deg": -5, "tol_deg": 8}},
    ],
    "talk": [
        {"name": "hand_above_shoulder", "args": {"side": "right", "margin_mm": -80}},
        {"name": "elbow_angle", "args": {"side": "right", "min_deg": 60, "max_deg": 110}},
        {"name": "arm_not_crossed", "args": {"side": "right"}},
    ],
    "idle": [
        {"name": "hand_below_waist", "args": {"side": "right", "margin_mm": -20}},
        {"name": "torso_upright", "args": {"max_deg": 10}},
    ],
    "walk": [
        {"name": "foot_grounded", "args": {"side": "left", "tol_mm": 40}},
        {"name": "torso_upright", "args": {"max_deg": 12}},
    ],
    "run": [
        {"name": "torso_lean", "args": {"deg": 10, "tol_deg": 10}},
        {"name": "foot_lifted", "args": {"side": "right", "min_mm": 40}},
    ],
    "sit": [
        {"name": "foot_grounded", "args": {"side": "left", "tol_mm": 60}},
        {"name": "torso_lean", "args": {"deg": -5, "tol_deg": 10}},
    ],
    "kneel": [
        {"name": "foot_grounded", "args": {"side": "left", "tol_mm": 80}},
        {"name": "torso_lean", "args": {"deg": 12, "tol_deg": 12}},
    ],
    "rest": [
        {"name": "hand_below_waist", "args": {"side": "right", "margin_mm": -30}},
        {"name": "torso_upright", "args": {"max_deg": 15}},
    ],
    "spin": [
        {"name": "torso_upright", "args": {"max_deg": 15}},
    ],
}


def main() -> int:
    p = os.path.join(ROOT, "config", "seed_families.json")
    d = json.load(open(p, encoding="utf-8"))
    fams = d["families"]
    added = 0
    for name, tpl in TEMPLATES.items():
        if name not in fams:
            print(f"  跳过 {name}（族不存在）")
            continue
        fams[name]["predicates_template"] = tpl
        added += 1
    json.dump(d, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"写入 {added} 个族的 predicates_template")
    for name in TEMPLATES:
        if name in fams:
            print(f"  {name:10s} {len(TEMPLATES[name])} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

