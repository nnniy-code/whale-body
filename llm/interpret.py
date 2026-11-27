"""llm/interpret.py — 事件/对话 → {say, act}。

设计依据：docs/design/06-llm-schema.md

**可插拔**：不绑定任何 LLM 服务。调用者注入 `llm_fn(system, user) -> str`。
这样：
  - 测试用 stub（不需要 API key）
  - 生产接 DeepSeek / OpenAI / 本地模型，都是同一个接口
"""

from __future__ import annotations

import json
import re

from core.predicates import PREDICATES

# 谓词参数 schema（给 prompt 用，也用于校验）
PREDICATE_ARGS = {
    "hand_near_anchor": {"side": "left|right", "anchor": "<锚点名>", "tol_mm": "number"},
    "hand_above_head": {"side": "left|right", "margin_mm": "number"},
    "elbow_below_hand": {"side": "left|right", "margin_mm": "number"},
    "forearm_upright": {"side": "left|right", "max_deg": "number"},
    "arm_not_crossed": {"side": "left|right", "tol_mm": "number"},
    "elbow_outside": {"side": "left|right", "tol_mm": "number"},
    "hand_above_shoulder": {"side": "left|right", "margin_mm": "number"},
    "elbow_angle": {"side": "left|right", "min_deg": "number", "max_deg": "number"},
    "no_hyperextension": {"side": "left|right", "min_deg": "number"},
    "wrist_neutral": {"side": "left|right", "max_deg": "number"},
    "hand_below_waist": {"side": "left|right", "margin_mm": "number"},
    "hand_at_point": {"side": "left|right", "target_m": "[x,y,z]", "tol_mm": "number"},
    "foot_at_point": {"side": "left|right", "target_m": "[x,y,z]", "tol_mm": "number"},
    "tail_tip_at_anchor": {"anchor": "<锚点名>", "tol_mm": "number"},
    "head_nod": {"deg": "number", "tol_deg": "number"},
    "head_tilt": {"deg": "number", "tol_deg": "number"},
    "head_yaw": {"deg": "number", "tol_deg": "number"},
    "torso_lean": {"deg": "number", "tol_deg": "number"},
    "gaze_toward": {"anchor": "<锚点名>", "cone_deg": "number"},
    "foot_grounded": {"side": "left|right", "tol_mm": "number"},
    "foot_lifted": {"side": "left|right", "min_mm": "number"},
    "dual_support": {"min_sides": "number"},
    "no_ground_penetration": {"side": "left|right", "tol_mm": "number"},
    "no_self_collision": {"tol_mm": "number"},
    "inside_workspace": {"box_m": "number"},
    "torso_upright": {"max_deg": "number"},
    "no_big_step": {"budget_mm": "number"},
    "no_micro_jitter": {"min_mm": "number"},
    "max_deg_per_frame": {"budget": "number"},
    "foot_slip": {"tol_mm": "number"},
    "jerk_rms": {"budget": "number"},
    "duty_match": {"target_duty": "number", "tol": "number"},
}

REQUIRED_ACT_KEYS = ("intent", "predicates")


class LLMError(Exception):
    pass


def build_system_prompt(capabilities: dict) -> str:
    """生成给 LLM 的系统提示。只给这 5 样，不要塞整个 HANDOFF。"""
    fams = ", ".join(capabilities.get("families", []))
    anchors = ", ".join(capabilities.get("anchors", []))
    preds = "\n".join(
        f"- {name}({', '.join(f'{k}:{v}' for k, v in PREDICATE_ARGS.get(name, {}).items())})"
        for name in capabilities.get("predicates", [])
    )
    return f"""你是一个虚拟角色的动作编译器。用户说话后，你要输出一个 JSON：
{{"say": "要说的话", "act": {{"intent": "族名", "predicates": [...], "dur_s": 秒}}}}

**硬规则**
1. `intent` 必须是下面这些族之一：{fams}
2. `predicates` 至少一条，名字必须来自下面的表
3. **不要给肩/肘/腕的角度**——位置用锚点（anchor），手在哪用 hand_near_anchor
4. 可以给头/躯干的角度（head_tilt / head_nod / head_yaw / torso_lean）
5. 只用下面的锚点名：{anchors}
6. 说明做不到时，输出 {{"say":"...", "act": null}}，不要编造

**可用的判据**
{preds}

**可用锚点**：{anchors}
**唯一允许的数值**：tol_mm（毫米）、deg（度）、dur_s（秒）
"""


def build_user_message(text: str, context: dict | None = None) -> str:
    ctx = ""
    if context:
        mood = context.get("mood")
        if mood:
            ctx = f"\n当前情绪：val={mood.get('val')} aro={mood.get('aro')} close={mood.get('close')} guard={mood.get('guard')}"
    return f"用户说：{text}{ctx}\n\n只输出 JSON。"


def extract_json(text: str) -> dict:
    """从 LLM 输出里抽出 JSON（容忍 ```json 包裹和前后废话）。"""
    if not text:
        raise LLMError("空输出")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            raise LLMError("找不到 JSON")
        text = text[i:j + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON 解析失败：{e}") from e


def validate_act(obj: dict, capabilities: dict) -> tuple[bool, str]:
    act = obj.get("act")
    if act is None:
        return True, ""                        # 允许「做不到」
    for k in REQUIRED_ACT_KEYS:
        if k not in act:
            return False, f"act 缺 {k}"
    if act["intent"] not in capabilities.get("families", []):
        return False, f"未知族 {act['intent']}"
    preds = act.get("predicates") or []
    if not preds:
        return False, "predicates 为空"
    allowed = set(capabilities.get("predicates", []))
    for p in preds:
        if p.get("name") not in allowed:
            return False, f"未知谓词 {p.get('name')}"
        args = p.get("args") or {}
        schema = PREDICATE_ARGS.get(p["name"], {})
        for k in args:
            if k not in schema:
                return False, f"{p['name']} 不接受参数 {k}"
    return True, ""


def interpret(text: str, llm_fn, capabilities: dict,
              context: dict | None = None, retries: int = 2) -> dict:
    """调用 LLM 并校验。

    llm_fn(system, user) -> str
    返回 {"ok": bool, "obj": dict|None, "say": str, "error": str|None, "attempts": int}
    """
    system = build_system_prompt(capabilities)
    user = build_user_message(text, context)
    last_err = ""
    for attempt in range(retries + 1):
        try:
            raw = llm_fn(system, user)
        except Exception as e:                  # LLM 调用本身失败
            last_err = f"调用失败：{e}"
            continue
        try:
            obj = extract_json(raw)
        except LLMError as e:
            last_err = str(e)
            continue
        ok, reason = validate_act(obj, capabilities)
        if ok:
            return {"ok": True, "obj": obj, "say": obj.get("say", ""),
                    "error": None, "attempts": attempt + 1}
        last_err = reason
    # 降级：只播 say（如果能抽出来）
    return {"ok": False, "obj": None, "say": "", "error": last_err,
            "attempts": retries + 1}
