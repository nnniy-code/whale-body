"""tests/test_grow_auto.py — 族自动生长（v1.9 接上）。

设计依据：用户 2026-11-27「那就把成长功能按你的方法接入」

流程：
  同一个新动作被要 3 次
    → 库条目 hits=3（L1 命中累加）
    → grow.propose 认出「反复出现的谓词组合」
    → 面板提议（用户点「收下」）
    → apply_proposals 生成完整族（label/G/P_default/P_range/thresholds/hint）
    → 写进 sandbox/auto_families.json，下次连接自动并进来

怎么测：python tests/test_grow_auto.py
预期现象：[grow_auto] ALL PASS
调参位置：core/grow.py propose(min_count) / apply_proposals
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import grow  # noqa: E402
from core.bones import BoneMap  # noqa: E402
from core.collision import baseline_gaps, load_table  # noqa: E402
from core.fk import Rig  # noqa: E402
from core.library import Library  # noqa: E402
from core.runtime import Runtime  # noqa: E402
from core.solver import MAIN21  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ONLY = MAIN21 + ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"]
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    rigd = json.load(open(os.path.join(ROOT, "tests", "fixtures", "rig_data.json"), encoding="utf-8"))
    rig = Rig(rigd["rest"], rigd["parent"], rigd["connected"], rigd["order"])
    rf = rig.subset(ONLY)
    bm = BoneMap.load(os.path.join(ROOT, "config", "bones_map.json"))
    anch = json.load(open(os.path.join(ROOT, "config", "anchors.json"), encoding="utf-8"))
    fams = json.load(open(os.path.join(ROOT, "config", "seed_families.json"), encoding="utf-8"))["families"]
    caps = json.load(open(os.path.join(ROOT, "config", "capsules.json"), encoding="utf-8"))["bones"]
    tab = load_table()
    bmj = json.load(open(os.path.join(ROOT, "config", "bones_map.json"), encoding="utf-8"))["bones"]
    L = {n: d["len_mm"] / 1000.0 for n, d in bmj.items()}
    zero = {n: np.eye(4) for n in rigd["order"]}
    base = baseline_gaps(rf.compute(zero), caps, rf, L, chain_skip=3, only=ONLY)

    def mk(f=fams):
        return Runtime(rig, bm, anch, f, Library(), capsules=caps,
                       collision_table=tab, lengths=L,
                       capsule_baseline=base, capsule_only=ONLY)

    ACT = {"intent": "free", "use_llm_predicates": True, "user_text": "手叉腰",
           "predicates": [{"name": "hand_near_anchor",
                           "args": {"anchor": "chin", "side": "right", "tol_mm": 150}}]}

    print("[1] 同一个新动作要 3 次 → 库命中数到 3")
    rt = mk()
    sources = []
    for _ in range(3):
        r = rt.handle_intent(dict(ACT))
        sources.append(r.get("source"))
    check("第一次现场解出（L2）", sources[0] == "L2", str(sources))
    check("之后命中库（L1）", sources[1:] == ["L1", "L1"], str(sources))
    hits = max(int((e.metrics or {}).get("hits", 0)) for e in rt.library.entries.values())
    check("hits=3", hits == 3, str(hits))

    print("[2] propose 认出它，并带上用户原话当标签")
    props = grow.propose(rt.library, min_count=3)
    check("有 1 条提议", len(props) == 1, str(len(props)))
    p = props[0]
    check("count=3", p["count"] == 3, str(p["count"]))
    check("label=手叉腰", p["label"] == "手叉腰", repr(p.get("label")))
    check("模板带着谓词", bool(p["predicates_template"]))
    check("P_range 有范围", all(len(v) == 2 for v in p["P_range"].values()))

    print("[3] 阈值以下不提议")
    check("min_count=4 时没有提议", len(grow.propose(rt.library, min_count=4)) == 0)

    print("[4] 收下 → 生成完整的族（能过校验的那种）")
    fams2, added = grow.apply_proposals(rt.families, props, min_count=3,
                                        template_family=rt.families.get("free"))
    check("新增 1 个族", len(added) == 1, str(added))
    name = added[0]["name"]
    fam = fams2[name]
    for k in ("label", "G", "P_default", "P_range", "thresholds", "predicates_hint"):
        check(f"字段 {k}", k in fam, str(list(fam.keys()))[:80])
    check("label 用用户原话", fam["label"] == "手叉腰", fam["label"])
    check("标了 auto", fam.get("auto") is True)
    check("有指纹", bool(fam.get("fingerprint")))

    print("[5] 新族能直接跑（不用再现场搜）")
    rt2 = mk(fams2)
    r2 = rt2.handle_intent({"intent": name})
    check("有 entry", r2.get("entry") is not None, str(r2.get("source")))
    check("命中的是新族", (r2.get("entry").family if r2.get("entry") else None) == name,
          str(r2.get("entry").family if r2.get("entry") else None))
    b, _ = rt2.build_full((r2["entry"].params or {}).get("P", {}), name, 0.0)
    M = rig.compute(b)
    check("姿势跟 free 解出来的一致（手在 chin 附近 <25cm）",
          abs(float(M["rightHand"][2, 3]) - 0.572) > 0.03,
          f"{float(M['rightHand'][2,3]):.3f}")

    print("[6] 落盘 + 重新加载（merge_auto）")
    tmpdir = tempfile.mkdtemp(dir=os.path.join(ROOT, "sandbox"))
    path = os.path.join(tmpdir, "auto_families.json")
    try:
        f2 = dict(fam)
        f2["name_hint"] = name
        grow.record_family(path, f2)
        check("文件写出来了", os.path.exists(path))
        merged = grow.merge_auto(fams, path)
        check("并进来后多了一个族", len(merged) == len(fams) + 1, f"{len(fams)} → {len(merged)}")
        check("不覆盖手工族", merged["wave"] is fams["wave"] or merged["wave"].get("auto") is None)
        # 再写一次同一个指纹，不应该重复
        grow.record_family(path, f2)
        data = grow.load_auto(path)
        check("重复收下不会长两份", len(data["families"]) == 1, str(len(data["families"])))
        # dismissed 生效
        data["dismissed"].append("fp_test")
        grow.save_auto(path, data)
        check("dismissed 存下来了", "fp_test" in grow.load_auto(path)["dismissed"])
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print("[7] 不同谓词不会被合并")
    rt3 = mk()
    other = dict(ACT)
    other["user_text"] = "手放头顶"
    other["predicates"] = [{"name": "hand_near_anchor",
                            "args": {"anchor": "head_top", "side": "right", "tol_mm": 120}}]
    for _ in range(3):
        rt3.handle_intent(dict(other))
    ps = grow.propose(rt3.library, min_count=3)
    check("只提议这一种", len(ps) == 1, str(len(ps)))
    check("模板是 head_top", ps[0]["predicates_template"][0]["args"].get("anchor") == "head_top")

    print("[8] 归一化：只归名字，不归判据")
    # 8a 换个说法、判据一样 → 同一个指纹（不会被拆成两个动作）
    a = [{"name": "hand_near_anchor", "args": {"anchor": "chin", "side": "right", "tol_mm": 150}}]
    b = [{"name": "hand_near_anchor", "args": {"anchor": "chin", "side": "right", "tol_mm": 120}},
         {"name": "wrist_neutral", "args": {}}]
    check("换个说法 + 弱谓词 → 同指纹", grow.fingerprint(a) == grow.fingerprint(b),
          f"{grow.fingerprint(a)} vs {grow.fingerprint(b)}")
    check("tol 120/150 等价", grow.fingerprint(a) == grow.fingerprint(
        [{"name": "hand_near_anchor", "args": {"anchor": "chin", "side": "right", "tol_mm": 149}}]))

    # 8b 判据不一样（左右手）→ 不同指纹，**绝不能归一**
    c = [{"name": "hand_near_anchor", "args": {"anchor": "chin", "side": "left", "tol_mm": 150}}]
    check("左右手不同指纹", grow.fingerprint(a) != grow.fingerprint(c))
    # 8c 锚点不同 → 不同指纹
    d = [{"name": "hand_near_anchor", "args": {"anchor": "head_top", "side": "right"}}]
    check("锚点不同 → 不同指纹", grow.fingerprint(a) != grow.fingerprint(d))

    # 8d 名字归一：标点/语气词去掉
    check("名字归一 手叉腰。", grow.norm_label("手叉腰。") == "手叉腰")
    check("名字归一 叉着腰啊", grow.norm_label("叉着腰啊") == "叉着腰")

    # 8e 同名不同判据 → 报警，不合并
    p1 = {"fingerprint": grow.fingerprint(a), "label": "手叉腰", "count": 3, "name": "x"}
    p2 = {"fingerprint": grow.fingerprint(c), "label": "手叉腰", "count": 3, "name": "y"}
    coll = grow.label_collisions([p1, p2])
    check("报出同名冲突", len(coll) == 1, str(coll))
    check("冲突里有两个指纹", coll and len(coll[0]["fingerprints"]) == 2)
    merged = grow.merge_labels([p1, p2])
    check("merge_labels 不会把两个指纹并成一个", len(merged) == 2, str(len(merged)))
    # 8f 同一指纹多条 → 合成一条，计数相加
    p3 = dict(p1)
    p3["count"] = 2
    m2 = grow.merge_labels([p1, p3])
    check("同指纹合并成 1 条", len(m2) == 1, str(len(m2)))
    check("计数相加 3+2=5", m2[0]["count"] == 5, str(m2[0]["count"]))

    print("[9] 真链路：两个说法、同一判据 → 合成一条提议（计数相加）")
    rt4 = mk()
    A = {"intent": "free", "use_llm_predicates": True, "user_text": "手叉腰",
         "predicates": [{"name": "hand_near_anchor",
                         "args": {"anchor": "chin", "side": "right", "tol_mm": 150}}]}
    B = {"intent": "free", "use_llm_predicates": True, "user_text": "叉着腰",
         "predicates": [{"name": "hand_near_anchor",
                         "args": {"anchor": "chin", "side": "right", "tol_mm": 120}},
                        {"name": "wrist_neutral", "args": {}}]}
    for act in (A, B, A, B, A, B):
        rt4.handle_intent(dict(act))
    ps4 = grow.merge_labels(grow.propose(rt4.library, min_count=3))
    check("只有一条提议", len(ps4) == 1, str(len(ps4)))
    check("计数 ≥3", ps4 and ps4[0]["count"] >= 3, str(ps4[0]["count"] if ps4 else None))
    check("名字归一后是「手叉腰」", ps4 and ps4[0]["label"] in ("手叉腰",), str(ps4[0]["label"] if ps4 else None))
    check("两个说法都记进 aliases",
          ps4 and {"手叉腰", "叉着腰"} <= set(ps4[0].get("aliases") or []),
          str(ps4[0].get("aliases") if ps4 else None))

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
