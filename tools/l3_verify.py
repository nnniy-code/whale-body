"""tools/l3_verify.py — L3 夜间转正：把库里 provisional 的条目复检成 verified / rejected。

怎么测：python tools/l3_verify.py [--dry-run] [--limit N]
预期现象：库文件里条目状态从 provisional 变成 verified / rejected
调参位置：本文件的 VERIFY_PREDICATES、config/collision.json

**纯脚本，不调 LLM、不用显卡。**
  - 全部 34 条谓词复检（不只是当初解出来用的那几条）
  - 胶囊碰撞全量检查（21 主骨 + 尾巴）
  - 通过 → verified；不通过 → rejected + 原因

设计依据：HANDOFF §7 P8（闸门 = 谓词 + 胶囊，BVH 不作主判据）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core import pose_gen  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, check_capsules, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.predicates import evaluate_series  # noqa: E402
from core.solver import MAIN21, make_frame  # noqa: E402

ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]

# L3 复检用的谓词：库里条目必须全部满足这些「底线」
# 比 L2 当时用的谓词更严 —— 因为 L2 只保证「解出来了」，L3 要保证「存下来是对的」
BASELINE_PREDICATES = [
    {"name": "torso_upright", "args": {"max_deg": 45}},
    {"name": "no_ground_penetration", "args": {"tol_mm": 5}},
    # 反关节：库里的姿势不能把肘/膝折反
    {"name": "no_hyperextension", "args": {"side": "right", "min_deg": 20}},
    {"name": "no_hyperextension", "args": {"side": "left", "min_deg": 20}},
]

# 参数越界也算 rejected：库里存的 P 必须在族的 P_range 内
def range_violations(P: dict, family: dict) -> list[str]:
    rng = family.get("P_range") or {}
    bad = []
    for k, v in P.items():
        if k in rng:
            lo, hi = rng[k]
            if not (lo - 1e-6 <= float(v) <= hi + 1e-6):
                bad.append(f"{k}={v:.1f} 不在 [{lo},{hi}]")
    return bad


def load_library(path: str) -> dict:
    if not os.path.exists(path):
        return {"entries": {}}
    return json.load(open(path, encoding="utf-8"))


def save_library(path: str, lib: dict) -> None:
    tmp = path + ".tmp"
    json.dump(lib, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只报告，不改库")
    ap.add_argument("--limit", type=int, default=0, help="最多处理多少条")
    ap.add_argument("--lib", default=os.path.join(ROOT, "config", "library.json"))
    args = ap.parse_args()

    if not os.path.exists(args.lib):
        print(f"库不存在：{args.lib}")
        print("（库文件由 Runtime 在运行时写出；先跑一次 tools/test_template_runtime.py 生成）")
        return 1

    lib = load_library(args.lib)
    entries = lib.get("entries", {})
    todo = [e for e in entries.values() if e.get("status") == "provisional"]
    if args.limit:
        todo = todo[: args.limit]
    print(f"库中共 {len(entries)} 条，待复检 {len(todo)} 条")

    # ---- 准备 ----
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    rf = rig.subset(ONLY)
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    anch = json.load(open(os.path.join(ROOT, "config", "anchors.json"), encoding="utf-8"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    caps = json.load(open(os.path.join(ROOT, "config", "capsules.json"), encoding="utf-8"))["bones"]
    tab = load_table()
    bmj = json.load(open(os.path.join(ROOT, "config", "bones_map.json"), encoding="utf-8"))["bones"]
    lengths = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base_gaps = baseline_gaps(rf.compute(zero), caps, rf, lengths, chain_skip=3, only=ONLY)

    n_ok = n_bad = n_err = 0
    t0 = time.perf_counter()
    for e in todo:
        eid = e.get("id", "?")
        fame = e.get("family")
        fam = fams.get(fame)
        if fam is None:
            e["status"] = "rejected"
            e["verify"] = {"reason": f"族 {fame!r} 不存在", "at": time.strftime("%Y-%m-%dT%H:%M")}
            n_bad += 1
            continue
        try:
            P = dict(fam["P_default"])
            P.update(e["params"].get("P", {}))
            side = (e.get("metrics") or {}).get("side")
            rng_bad = range_violations(P, fam)
            if rng_bad:
                e["status"] = "rejected"
                e["verify"] = {"reason": "参数越界：" + "; ".join(rng_bad[:3]),
                               "at": time.strftime("%Y-%m-%dT%H:%M")}
                n_bad += 1
                continue
            M = rf.compute(pose_gen.build(P, rf, bm, active_side=side))
        except Exception as ex:
            e["status"] = "rejected"
            e["verify"] = {"reason": f"重建失败：{ex}", "at": time.strftime("%Y-%m-%dT%H:%M")}
            n_err += 1
            continue

        frame = make_frame(M, anch)
        # 1) 谓词复检（当初用的 + 底线）
        preds = list(e.get("predicates") or []) + BASELINE_PREDICATES
        worst_name, worst_err = "?", 0.0
        pred_ok = True
        try:
            for p in preds:
                err, _worst, _detail = evaluate_series(
                    p["name"], [frame], **(p.get("args") or {}))
                if err > worst_err:
                    worst_name, worst_err = p["name"], err
                if err >= 1.0:
                    pred_ok = False
        except Exception as ex:
            e["status"] = "rejected"
            e["verify"] = {"reason": f"谓词求值失败：{ex}", "at": time.strftime("%Y-%m-%dT%H:%M")}
            n_err += 1
            continue

        # 2) 胶囊复检
        caps_v = check_capsules(M, caps, rf, tab, baseline=base_gaps,
                                lengths=lengths, chain_skip=3, only=ONLY)

        if pred_ok and not caps_v:
            e["status"] = "verified"
            e["verify"] = {"at": time.strftime("%Y-%m-%dT%H:%M"),
                           "predicates_ok": True, "capsules_ok": True}
            n_ok += 1
        else:
            reason = []
            if not pred_ok:
                reason.append(f"谓词 {worst_name} err={worst_err:.2f}")
            for c in caps_v[:2]:
                reason.append(f"胶囊 {c['bone']}×{c['other']} 超 {c['over_mm']:.0f}mm")
            e["status"] = "rejected"
            e["verify"] = {"at": time.strftime("%Y-%m-%dT%H:%M"),
                           "reason": "; ".join(reason)}
            n_bad += 1

    ms = (time.perf_counter() - t0) * 1000
    print(f"\nverified {n_ok}  rejected {n_bad}  重建失败 {n_err}   {ms:.0f}ms")

    if args.dry_run:
        print("（--dry-run，未写盘）")
    else:
        save_library(args.lib, lib)
        print(f"已写回 {args.lib}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



