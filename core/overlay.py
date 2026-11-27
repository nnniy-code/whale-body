"""core/overlay.py — 多动作叠加：把两个动作的骨骼 basis 合并成一个。

设计依据：HANDOFF §16 待定项 #1（「一边走过来一边挥手」）

思路：
  基础动作（走路）负责腿 + 躯干 + 步态
  叠加动作（挥手）只负责手臂
  合并 = 基础的全拿 + 把上层指定的骨组覆盖上去

**不是姿态加权平均**——那会让手臂和腿都变软。是「分块替换」：
  每条命令管哪些骨是**声明式**的（OVERLAY_GROUPS），不靠猜。

风险：两条命令都想要同一根骨时，后写的赢，且会记一条冲突日志。
"""

from __future__ import annotations

import numpy as np

# 可以整体替换的骨组
OVERLAY_GROUPS: dict[str, list[str]] = {
    "arm.right": ["rightUpperArm", "rightLowerArm", "rightHand"],
    "arm.left": ["leftUpperArm", "leftLowerArm", "leftHand"],
    "arms": ["leftUpperArm", "leftLowerArm", "leftHand",
             "rightUpperArm", "rightLowerArm", "rightHand"],
    "leg.right": ["rightUpperLeg", "rightLowerLeg", "rightFoot"],
    "leg.left": ["leftUpperLeg", "leftLowerLeg", "leftFoot"],
    "legs": ["leftUpperLeg", "leftLowerLeg", "leftFoot",
             "rightUpperLeg", "rightLowerLeg", "rightFoot"],
    "head": ["neck", "head", "mixamorig:HeadTop_End"],
    "torso": ["spine", "chest", "upperChest"],
    "tail": ["WhaleTail_01", "WhaleTail_02", "WhaleTail_03", "WhaleTail_04"],
}


def bones_of(groups: list[str]) -> list[str]:
    """把组名展开成骨名（去重、保序）。"""
    out, seen = [], set()
    for g in groups:
        for b in OVERLAY_GROUPS.get(g, [g]):
            if b not in seen:
                seen.add(b)
                out.append(b)
    return out


def merge(base: dict, over: dict, groups: list[str]) -> tuple[dict, list[str]]:
    """把 over 里属于 groups 的骨覆盖到 base 上。

    返回 (合并后的 basis, 实际被覆盖的骨名列表)。
    """
    out = dict(base)
    covered = []
    for b in bones_of(groups):
        if b in over and b in out:
            out[b] = np.array(over[b], dtype=float, copy=True)
            covered.append(b)
    return out, covered


def merge_layers(base: dict, layers: list) -> tuple[dict, dict]:
    """多层叠加，支持优先级权重。

    `layers` 每项可以是：
        (basis, groups)              → weight 默认 1.0
        (basis, groups, weight)      → weight 越大优先级越高

    同一根骨被多层改时：**weight 高的赢**；weight 相同则**后写的赢**。
    这样「重要动作」可以用大 weight 压住「次要动作」，而不用改变调用顺序。

    返回 (合并后的 basis, 报告 dict)：
        report = {
          "covered":   {骨名: [第几层, ...]},   # 每根骨被哪些层改过
          "conflicts": [骨名, ...],             # 被 ≥2 层改过的骨
          "winner":    {骨名: 层号},            # 每根骨最终由哪层决定
          "per_layer": [[骨名, ...], ...],      # 每层实际覆盖了哪些骨
        }
    """
    out = dict(base)
    touched: dict[str, list[int]] = {}
    best: dict[str, tuple[float, int, np.ndarray]] = {}
    per_layer: list[list[str]] = []

    for idx, layer in enumerate(layers):
        if len(layer) == 3:
            basis, groups, weight = layer[0], layer[1], float(layer[2])
        else:
            basis, groups, weight = layer[0], layer[1], 1.0
        mine = []
        for b in bones_of(groups):
            if b not in basis or b not in out:
                continue
            mine.append(b)
            touched.setdefault(b, []).append(idx)
            cur = best.get(b)
            # >= 让「同 weight 时后写的赢」
            if cur is None or weight >= cur[0]:
                best[b] = (weight, idx, np.array(basis[b], dtype=float, copy=True))
        per_layer.append(mine)

    for b, (_w, _i, m) in best.items():
        out[b] = m

    return out, {
        "covered": touched,
        "conflicts": sorted(b for b, v in touched.items() if len(v) > 1),
        "winner": {b: v[1] for b, v in best.items()},
        "per_layer": per_layer,
    }


def _non_identity(m) -> bool:
    """basis 是否真的改动了这根骨（恒等 = 没改）。"""
    return not np.allclose(m, np.eye(4), atol=1e-9)


def conflicts(a: dict, b: dict, groups: list[str]) -> list[str]:
    """两条动作**都真的改了**的骨（用于日志/告警）。

    只看「骨在不在」不够——pose_gen 返回全部骨的 basis，没动的那些是恒等。
    """
    return [x for x in bones_of(groups)
            if x in a and x in b and _non_identity(a[x]) and _non_identity(b[x])]


def describe() -> str:
    return "\n".join(f"  {g:12s} {', '.join(v)}" for g, v in OVERLAY_GROUPS.items())

