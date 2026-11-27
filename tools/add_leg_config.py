"""tools/add_leg_config.py — 给下半身动作的族加 leg 配置（hip_drop / lift）。

怎么测：python tools/add_leg_config.py
"""

from __future__ import annotations

import json
import os

ROOT = r"D:\DSproject\body-v2"
PATH = os.path.join(ROOT, "config", "seed_families.json")

# 族 → (hip_drop 米, lift 字典)
LEG = {
    # 坐姿：髋降比较多，脚留在原地（腿收起来）
    "sit":          (0.26, None),
    # 跪：更低
    "kneel":        (0.34, None),
    # 休息：介于坐和蹲之间
    "rest":         (0.22, None),
    # 趴下：最低
    "lie_down":     (0.38, None),
    # 蹲（新加）
    "crouch":       (0.20, None),
    # 单脚站 / 抬腿
    "lift_leg_left":  (0.02, {"left": 0.22}),
    "lift_leg_right": (0.02, {"right": 0.22}),
    # 踮脚
    "tiptoe":       (0.0, {"left": 0.04, "right": 0.04}),
}


def main() -> int:
    d = json.load(open(PATH, encoding="utf-8"))
    fams = d["families"]
    changed = []
    for name, (drop, lift) in LEG.items():
        f = fams.get(name)
        if f is None:
            # 新族：先建一个最小结构（照抄 idle 的模板和范围）
            if name in ("crouch", "tiptoe"):
                idle = fams["idle"]
                f = {
                    "label": {"crouch": "蹲下", "tiptoe": "踮脚"}[name],
                    "thresholds": {},
                    "predicates_hint": ["torso_upright", "knee_angle"],
                    "predicates_template": [
                        {"name": "torso_upright", "args": {"max_deg": 30}},
                    ],
                    "P_range": dict(idle["P_range"]),
                    "P_default": dict(idle["P_default"]),
                    "G": dict(idle["G"]),
                }
                fams[name] = f
                changed.append(f"{name} (新建)")
            else:
                continue
        G = f.setdefault("G", {})
        G["hip_drop"] = float(drop)
        if lift:
            G["lift"] = dict(lift)
        else:
            G.pop("lift", None)
        changed.append(name)

    json.dump(d, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("改了:", ", ".join(changed))
    print("总族数:", len(fams))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
