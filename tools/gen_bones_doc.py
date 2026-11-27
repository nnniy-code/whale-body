"""tools/gen_bones_doc.py — 由 config/bones_map.json 生成 docs/BONES.md。

怎么测：python tools/gen_bones_doc.py
预期现象：写入 docs/BONES.md，打印骨骼数与链统计
调参位置：config/bones_map.json 由 Blender 端的检查脚本生成
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "config", "bones_map.json")
DST = os.path.join(ROOT, "docs", "BONES.md")

MAIN21 = [
    "hips", "spine", "chest", "upperChest", "neck", "head",
    "mixamorig:HeadTop_End",
    "leftShoulder", "leftUpperArm", "leftLowerArm", "leftHand",
    "rightShoulder", "rightUpperArm", "rightLowerArm", "rightHand",
    "leftUpperLeg", "leftLowerLeg", "leftFoot",
    "rightUpperLeg", "rightLowerLeg", "rightFoot",
]

CHAIN_DESC = {
    "main": ("主链（躯干 + 四肢）", "✅ 进 L2 求解"),
    "finger": ("手指", "❌ 静态跟随"),
    "toe": ("脚趾", "❌ 静态跟随"),
    "hair": ("头发", "❌ 二次运动"),
    "bangs": ("刘海", "❌ 二次运动"),
    "skirt": ("裙摆", "❌ 二次运动"),
    "ribbon": ("腰饰", "❌ 二次运动"),
    "tail": ("鲸鱼尾巴", "❌ 二次运动"),
}


def main() -> int:
    with open(SRC, "r", encoding="utf-8") as f:
        d = json.load(f)
    bones = d["bones"]
    chains = d["chains"]
    anoms = d.get("anomalies", {})

    lines: list[str] = []
    add = lines.append

    add("# 骨骼映射表")
    add("")
    add("> 自动生成：`python tools/gen_bones_doc.py`")
    add("> 数据源：`config/bones_map.json`（Blender 端扫描 209 根骨）")
    add("")
    add("## 0. 摘要")
    add("")
    add("| 项 | 值 |")
    add("|---|---|")
    add(f"| 骨数 | {d['n_bones']} |")
    roots = [n for n, v in bones.items() if v["parent"] is None]
    add(f"| 根骨 | {' / '.join(roots)}（唯一） |")
    add(f"| 最大链深 | {d['max_depth']} |")
    pref = Counter(v["has_mixamo_prefix"] for v in bones.values())
    add(f"| 命名前缀 | {pref.get(False,0)} 无前缀 + {pref.get(True,0)} 带 `mixamorig:` —— **混用** |")
    add(f"| 零长度骨 | {len(anoms.get('zero_length', []))} |")
    add(f"| 左右不对称 | {len(anoms.get('asymmetry', []))} |")
    add(f"| 权重 >0.5 的顶点为零 | {len(anoms.get('no_vg', []))} 根（含 13 根完全无 VG 组） |")
    add("")

    add("## 1. 链分类")
    add("")
    add("| 链 | 数量 | 用途 | 进求解？ |")
    add("|---|---|---|---|")
    for c, n in sorted(chains.items(), key=lambda kv: -kv[1]):
        desc, use = CHAIN_DESC.get(c, (c, "?"))
        add(f"| `{c}` | {n} | {desc} | {use} |")
    add("")
    add(f"合计：{sum(chains.values())} = 209")
    add("")

    add("## 2. 局部轴 → 世界（**最容易踩的坑**）")
    add("")
    add("骨的 `matrix_basis` 旋转是在**骨骼局部坐标系**里定义的。这具骨架的局部轴与世界轴**不一致**：")
    add("")
    add("| 骨 | 局部 X 指向 | 局部 Y 指向 | 局部 Z 指向 |")
    add("|---|---|---|---|")
    for n in ("hips", "spine", "head", "leftUpperArm", "leftFoot", "WhaleTail_01"):
        if n in bones:
            a = bones[n]["axes"]
            add(f"| `{n}` | {a['x']} | {a['y']} | {a['z']} |")
    add("")
    add("**由此推出的操作映射**（**仅适用于躯干骨** `hips/spine/chest/upperChest/neck/head`）：")
    add("")
    add("| 想要 | 世界轴 | 作用到躯干骨局部 |")
    add("|---|---|---|")
    add("| 前倾 / 后仰 | 绕世界 **X** | 局部 Z **负**转 / 正转 |")
    add("| 侧倾 | 绕世界 **Y** | 局部 X **负**转 |")
    add("| 转头 / 扭腰 | 绕世界 **Z** | 局部 Y |")
    add("")
    add("> ⚠️ **四肢骨不能套上表**。实测 `leftUpperArm` 是 `x→-Y, y→-Z, z→+X`（和躯干不同），")
    add("> `leftFoot` 更是倾斜的（局部 Z = `[0.65,-0.49,0.57]`）。")
    add("> **一律用通用公式**：`R_local = R_rest.T @ R_world @ R_rest`（rest 正交，inv = T）。")
    add("> 参考实现：`tests/test_proprio.py::world_rot()`。**所有「语义角度 → basis」都要过这一步。**")
    add("")

    add("## 3. 主链（21 根，进 L2 求解）")
    add("")
    add("| 骨 | 父 | conn | 长度 mm | VG 顶点 | 局部轴 X/Y/Z |")
    add("|---|---|---|---|---|---|")
    for n in MAIN21:
        v = bones.get(n)
        if not v:
            add(f"| `{n}` | — | — | — | — | **缺失** |")
            continue
        a = v["axes"]
        add(f"| `{n}` | {v['parent'] or '—'} | {'✅' if v['connected'] else '❌'} | "
            f"{v['len_mm']} | {v['vg_verts']} | {a['x']}/{a['y']}/{a['z']} |")
    add("")

    add("## 4. 异常与注意")
    add("")
    if anoms.get("no_vg"):
        names = anoms["no_vg"]
        add(f"### 4.1 权重 >0.5 的顶点为零：{len(names)} 根")
        add("")
        add("其中 13 根完全没有 VG 组（`*_End`、手指 tip），其余是「有组但权重都 <0.5」。")
        add("**影响**：胶囊半径和质心会漏掉这些骨 → 用父骨半径 × 0.5 或跳过。")
        add("")
        add("<details><summary>名单</summary>")
        add("")
        add("`" + "`, `".join(names) + "`")
        add("")
        add("</details>")
        add("")
    if anoms.get("zero_length"):
        add(f"### 4.2 零长度骨：{len(anoms['zero_length'])} 根 —— 见名单")
        add("")
    if anoms.get("asymmetry"):
        add(f"### 4.3 左右不对称：{len(anoms['asymmetry'])} 对")
        add("")
        for a in anoms["asymmetry"][:20]:
            add(f"- {a}")
        add("")
    else:
        add("### 4.3 左右对称")
        add("")
        add("**全部对称**（rest 头位置镜像误差 <5mm，0 对异常）。可以放心用 `.L/.R` 对称生成。")
        add("")

    add("## 5. 语义锚点 → 骨骼")
    add("")
    add("| 锚点 | 骨骼 | 备注 |")
    add("|---|---|---|")
    rows = [
        ("mouth", "head", "虚拟点，需标定（09-anchors）"),
        ("chin", "head", "虚拟点"),
        ("head_top", "mixamorig:HeadTop_End", "有骨，直接用"),
        ("chest", "chest", "有骨"),
        ("belly", "spine", "有骨"),
        ("hand_center", "leftHand / rightHand", "有骨"),
        ("foot_sole", "leftFoot / rightFoot", "虚拟点（脚底 40 采样点）"),
        ("tail_tip", "WhaleTail_04", "有骨"),
    ]
    for a, b, note in rows:
        add(f"| `{a}` | `{b}` | {note} |")
    add("")
    add("**没有眼睛/脸/嘴骨**——mouth 只能做空间锚点，不能做表情（见 HANDOFF §8.6）。")
    add("")

    add("## 6. 后续代码必须遵守")
    add("")
    add("1. **不许硬编码骨名**：`connected`、`parent`、`axes` 全从 `bones_map.json` 读。")
    add("2. **不许硬编码 unconnected 名单**（实测 57 根，不是初稿的 8 根）。")
    add("3. **语义角度先转局部**，再写 basis。")
    add("4. **43 根无权重骨**：胶囊用父骨 ×0.5，质心跳过。")
    add("5. **前缀混用**（166 无前缀 + 43 `mixamorig:`）：匹配骨名时要做归一化。")
    add("")

    os.makedirs(os.path.dirname(DST), exist_ok=True)
    with open(DST, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"WROTE {DST} ({os.path.getsize(DST)} bytes)")
    print(f"chains={chains} roots={roots} no_vg={len(anoms.get('no_vg', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
