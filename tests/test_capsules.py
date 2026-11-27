"""tests/test_capsules.py — 胶囊碰撞验收。

怎么测：python tests/test_capsules.py
预期现象：[capsules] ALL PASS（rest 无违规、手插头能检出）
调参位置：config/capsules.json、config/collision.json 的 chain_skip / allowed_mm
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.collision import (  # noqa: E402
    baseline_gaps, capsule_gaps, chain_distance, check_capsules, load_table,
    region_of, seg_seg_distance,
)
from core.fk import Rig  # noqa: E402

MAIN = ['hips','leftUpperLeg','leftLowerLeg','leftFoot','rightUpperLeg','rightLowerLeg','rightFoot','spine','chest','upperChest','leftShoulder','leftUpperArm','leftLowerArm','leftHand','neck','head','mixamorig:HeadTop_End','rightShoulder','rightUpperArm','rightLowerArm','rightHand','WhaleTail_01','WhaleTail_02','WhaleTail_03','WhaleTail_04']

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    caps = json.load(open(os.path.join(ROOT, "config", "capsules.json"), encoding="utf-8"))["bones"]
    bm = json.load(open(os.path.join(ROOT, "config", "bones_map.json"), encoding="utf-8"))["bones"]
    lengths = {n: d["len_mm"] / 1000.0 for n, d in bm.items()}
    table = load_table()

    zero = {n: np.eye(4) for n in rigd["order"]}
    M0 = rig.compute(zero)

    print("[1] 几何工具")
    check("平行线距离", abs(seg_seg_distance([0,0,0],[1,0,0],[0,1,0],[1,1,0]) - 1.0) < 1e-9)
    check("相交为 0", abs(seg_seg_distance([0,0,0],[1,0,0],[0.5,-1,0],[0.5,1,0])) < 1e-9)
    check("端点距离", abs(seg_seg_distance([0,0,0],[0,0,0],[0,0,2],[0,0,3]) - 2.0) < 1e-9)

    print("[2] 骨链距离")
    check("自身 0", chain_distance(rig, "head", "head") == 0)
    check("父子 1", chain_distance(rig, "head", "neck") == 1)
    check("隔代 2", chain_distance(rig, "head", "upperChest") == 2)
    check("手到头 ≥4", chain_distance(rig, "leftHand", "head") >= 4,
          str(chain_distance(rig, "leftHand", "head")))
    check("跨肢体较大", chain_distance(rig, "leftHand", "leftFoot") > 5,
          str(chain_distance(rig, "leftHand", "leftFoot")))

    print("[3] region 分类")
    for bone, want in (("Hair_00_02", "hair"), ("Skirt_03_02", "skirt"),
                       ("WhaleTail_02", "tail"), ("head", "head"),
                       ("leftHand", "arm"), ("leftFoot", "leg"), ("chest", "body")):
        check(f"{bone} -> {want}", region_of(bone) == want, region_of(bone))

    print("[4] rest 姿态：baseline 自洽")
    base = baseline_gaps(M0, caps, rig, lengths, chain_skip=3, only=MAIN)
    rest_v = check_capsules(M0, caps, rig, table, baseline=base, lengths=lengths, chain_skip=3, only=MAIN)
    check("rest 零违规", len(rest_v) == 0, str(rest_v[:2]))
    check("baseline 有内容", len(base) > 0, str(len(base)))

    print("[5] 造「手插进头」→ 必须检出")
    M_bad = {k: v.copy() for k, v in M0.items()}
    head_pos = M0["head"][0:3, 3].copy()
    M_bad["leftHand"] = M_bad["leftHand"].copy()
    M_bad["leftHand"][0:3, 3] = head_pos
    bad = check_capsules(M_bad, caps, rig, table, baseline=base, lengths=lengths, chain_skip=3, only=MAIN)
    check("检出违规", len(bad) > 0, str(len(bad)))
    if bad:
        hit = [v for v in bad if "head" in v["regions"] and "arm" in v["regions"]]
        check("命中 arm×head", len(hit) > 0, str(bad[0]))

    print("[6] 造「手贴胸」（合法接触）→ 不该报")
    M_ok = {k: v.copy() for k, v in M0.items()}
    chest = M0["chest"][0:3, 3].copy()
    M_ok["leftHand"] = M_ok["leftHand"].copy()
    M_ok["leftHand"][0:3, 3] = chest + np.array([0.0, -0.09, 0.0])
    ok_v = check_capsules(M_ok, caps, rig, table, baseline=base, lengths=lengths, chain_skip=3, only=MAIN)
    arm_body = [v for v in ok_v if v["regions"] == ["arm", "body"]]
    check("arm×body 不报或轻报", all(v["over_mm"] < 30 for v in arm_body),
          str(arm_body[:2]))

    print("[7] 成对数量（不该爆炸）")
    gaps = capsule_gaps(M0, caps, rig, lengths, chain_skip=3, only=MAIN)
    check("主骨跨 region 对 < 500", len(gaps) < 500, str(len(gaps)))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

