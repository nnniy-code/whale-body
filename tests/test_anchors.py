"""tests/test_anchors.py — 锚点标定验收。

怎么测：python tests/test_anchors.py
预期现象：[anchors] ALL PASS
调参位置：config/anchors.json（换皮后重跑标定）
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.fk import Rig  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RIG = os.path.join(HERE, "fixtures", "rig_data.json")
ANCH = os.path.join(os.path.dirname(HERE), "config", "anchors.json")

FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    with open(RIG, "r", encoding="utf-8") as f:
        rig_d = json.load(f)
    with open(ANCH, "r", encoding="utf-8") as f:
        a = json.load(f)
    rig = Rig(rig_d["rest"], rig_d["parent"], rig_d["connected"], rig_d["order"])
    zero = {n: np.eye(4) for n in rig_d["order"]}
    M = rig.compute(zero)

    def world(name):
        p = a["points"][name]
        m = M[p["bone"]]
        return (m @ np.array([*p["offset_m"], 1.0]))[0:3]

    print("[1] 结构")
    check("有 points", "points" in a)
    check("有 directions", "directions" in a)
    for need in ("mouth", "chin", "head_top", "hand_center.L", "hand_center.R"):
        check(f"{need} 存在", need in a["points"])
    bad_conf = [k for k, v in a["points"].items()
                if v.get("source") == "calibrated" and v.get("confidence", 0) < 0.6]
    check("标定点 confidence ≥0.6", bad_conf == [], str(bad_conf))

    print("[2] 空间关系（rest 姿态）")
    mouth, chin = world("mouth"), world("chin")
    head = M["head"][0:3, 3]
    top = world("head_top")
    check("mouth 在 chin 上方", mouth[2] > chin[2], f"{mouth[2]:.4f} vs {chin[2]:.4f}")
    check("mouth 在 head 前方（y 更负）", mouth[1] < head[1] - 0.01,
          f"{mouth[1]:.4f} vs {head[1]:.4f}")
    check("head_top 在 head 上方", top[2] > head[2] + 0.10, f"{top[2]:.4f}")

    print("[3] mouth 在 face 材质的 z 区间内（0.820–0.898）")
    check("z ∈ [0.82, 0.90]", 0.82 <= mouth[2] <= 0.90, f"{mouth[2]:.4f}")
    frac = (mouth[2] - 0.820) / (0.898 - 0.820)
    check("z 分位 ∈ [0.15, 0.55]（脸下 1/3）", 0.15 <= frac <= 0.55, f"{frac:.2f}")

    print("[4] 左右手对称")
    lh, rh = world("hand_center.L"), world("hand_center.R")
    check("x 镜像", abs(lh[0] + rh[0]) < 0.005, f"{lh[0]:.4f} vs {rh[0]:.4f}")
    check("z 接近", abs(lh[2] - rh[2]) < 0.005)

    print("[5] 偏移量级合理")
    for k in ("mouth", "chin"):
        off = np.linalg.norm(a["points"][k]["offset_m"])
        check(f"{k} offset <150mm", off < 0.15, f"{off*1000:.1f}mm")

    print("[6] fk_checksum 标记")
    check("有 checksum 字段", "fk_checksum" in a, str(a.get("fk_checksum")))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
