"""tools/probe_leg_range.py — 腿/脚的全向可达范围。

怎么测：python tools/probe_leg_range.py
回答：脚能去哪些方向、多远、膝朝向能不能控。
"""

import json
import sys

import numpy as np

sys.path.insert(0, r"D:\DSproject\body-v2")

from core import ik
from core.bones import BoneMap
from core.fk import Rig


def main():
    R = r"D:\DSproject\body-v2"
    d = json.load(open(R + r"\tests\fixtures\rig_data.json", encoding="utf-8"))
    rig = Rig(d["rest"], d["parent"], d["connected"], d["order"])
    bm = BoneMap.load(R + r"\config\bones_map.json")
    M0 = rig.compute({n: np.eye(4) for n in d["order"]})
    hip = M0["leftUpperLeg"][0:3, 3].copy()
    ankle = M0["leftFoot"][0:3, 3].copy()
    print(f"rest 髋 = {[round(float(x),3) for x in hip]}")
    print(f"rest 踝 = {[round(float(x),3) for x in ankle]}")
    print(f"腿长    = 427.0 mm\n")

    # 方向 × 距离 扫描
    dirs = {
        "前(-Y)": (0.0, -1.0, 0.0),
        "后(+Y)": (0.0, 1.0, 0.0),
        "左(-X)": (-1.0, 0.0, 0.0),
        "右(+X)": (1.0, 0.0, 0.0),
        "上(+Z)": (0.0, 0.0, 1.0),
        "斜前上": (0.0, -0.707, 0.707),
        "斜前下": (0.0, -0.707, -0.707),
    }
    print(f"{'方向':<10} {'100mm':>8} {'200mm':>8} {'300mm':>8} {'400mm':>8}")
    for name, dv in dirs.items():
        row = []
        for dist in (0.10, 0.20, 0.30, 0.40):
            tgt = hip + np.array(dv) * dist
            tgt[2] = max(tgt[2], 0.05)          # 不穿地
            b, why = ik.leg_basis(rig, bm, "left", tgt)
            row.append("OK" if b is not None else "✗")
        print(f"{name:<10} {row[0]:>8} {row[1]:>8} {row[2]:>8} {row[3]:>8}")

    # 膝朝向（pole）能不能控
    print("\n=== 膝朝向（pole）===")
    tgt = hip + np.array([0.0, -0.20, -0.35])   # 前下方
    for pname, pole in (("默认(-Y)", None), ("前(-Y)", (0, -1, 0)),
                        ("外(-X)", (-1, 0, 0)), ("内(+X)", (1, 0, 0))):
        b, why = ik.leg_basis(rig, bm, "left", tgt, pole=pole)
        if b is None:
            print(f"  {pname}: 失败 {why}")
            continue
        cur = {n: np.eye(4) for n in d["order"]}
        cur.update(b)
        M = rig.compute(cur)
        knee = M["leftLowerLeg"][0:3, 3]
        print(f"  {pname:10s} 膝={[round(float(x),3) for x in knee]}")

    # 单脚站：右脚抬到 150mm 高
    print("\n=== 单脚站（右脚抬 150mm）===")
    tgt_r = M0["rightFoot"][0:3, 3] + np.array([0.0, -0.05, 0.15])
    b, why = ik.leg_basis(rig, bm, "right", tgt_r)
    print(f"  右腿抬 150mm: {'OK' if b else why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
