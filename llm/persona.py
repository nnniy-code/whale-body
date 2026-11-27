"""llm/persona.py — 大肥鱼的人格接入。

设计依据：sandbox/persona.md（她的人格卡原文）、HANDOFF §16.3

两种模式（用户 2026-11-24 定）：

  ① 身体没连   → **一次调用**，完全走她原来的人格，输出自然语言
  ② 身体连着   → **两次调用**：
        第一次：她还是她（自然语言，不挤压表达）
        第二次：拿她刚说的话，单独问「这句对应什么身体动作」
     第二次不带人格设定，纯功能，不会影响她的说话

**为什么不能合并成一次**：格式约束和表达自由度是竞争的。同时要求
「必须输出 JSON 且 act 不能空」会让她说话变短、不敢跑题、心情表达被压平。
"""

from __future__ import annotations

import json
import os
import re
import time

from llm import paths

# 身体连接判据：世界线 body.json 在 N 秒内被更新过
BODY_TIMEOUT_S = 15.0


# ---------------------------------------------------------------------------
# 资源
# ---------------------------------------------------------------------------

def _read(path: str, limit: int | None = None) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        t = f.read()
    return t[:limit] if limit else t


def load_persona(sandbox: str | None = None) -> str:
    """人格文本 = `persona.md` + `persona.json` 里的**输出格式硬要求**。

    v1.9：`persona.md` 是给人读的人格卡，不含「只发说出口的话、不写括号神态」
    这条格式约束——那条在 `persona.json` 的 `speech.medium` 里。
    以前只读 `.md`，于是她真的会写「（抬起手）」这种旁白。
    """
    d = sandbox or paths.SANDBOX
    text = _read(os.path.join(d, "persona.md"))
    return text + _speech_rules(d)


def _speech_rules(d: str) -> str:
    """从 `persona.json` 抽出**说话方式**的硬约束（md 里没有的那些）。"""
    p = os.path.join(d, "persona.json")
    if not os.path.exists(p):
        return ""
    try:
        data = json.load(open(p, encoding="utf-8"))
    except Exception:
        return ""
    sp = data.get("speech") or {}
    bits = []
    med = (sp.get("medium") or "").strip()
    if med:
        bits.append(med)
    # 实测：只写「不写括号」时，模型会改用 {action: wave} / 【抬手】。
    # 所以这里把「任何符号形式」都封掉。
    bits.append("不管用什么符号都不写动作和神态——圆括号、方括号、花括号、"
                "书名号、星号都不行。只说你会说出口的那句话。")
    bits.append("【你这副身体能做的动作】里列出来的，你都做得到，直接做、别推辞；"
                "只有**不在**那个清单里的（翻跟头、跑步、跳舞）才说做不到。")
    cp = sp.get("catchphrases") or []
    if cp:
        bits.append("口头禅（偶尔用，别每句都用）：" + "、".join(str(x) for x in cp[:6]))
    if not bits:
        return ""
    return "\n\n## 输出格式（硬要求）\n" + "\n\n".join(bits) + "\n"


_STAGE = re.compile(r"[（(【\[{][^（）()【】\[\]{}]{0,60}[）)】\]}]")


def strip_stage_directions(text: str) -> str:
    """去掉舞台描写：`（抬手）`、`{action: wave}`、`【歪头】`。

    为什么不能只靠 prompt：同一个 prompt 上一轮守规矩、下一轮就写括号，
    这是**概率**不是规则。所以在输出侧做一次确定性清理。

    解析器仍然看**原文**（括号里的动作对它是有用信号），
    只有给人看 / 给 TTS 的 `say` 被清过。
    """
    if not text:
        return text
    out = _STAGE.sub("", text)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    if not out:
        # 整句都是舞台描写（实测发生过：她只回了「（抬手，冲屏幕这边晃了晃）」）。
        # 这时候不能返回空字符串——面板上会像「她没说话」。
        # 退一步：只去掉括号字符，保留文字。
        out = re.sub(r"[（）()【】\[\]{}]", "", text).strip()
    return out


def _read_memory_json(path: str, top: int = 12) -> str:
    """正版的记忆主体：`world/memory.json` 的 `items`（带 strength/hits）。

    以前只读 `memory.md`（371 字节、3 行），等于把她的记忆砍到只剩摘要。
    """
    if not os.path.exists(path):
        return ""
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return ""
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        return ""
    def key(it):
        return (float(it.get("strength", 0) or 0), int(it.get("hits", 0) or 0))
    picked = sorted(items, key=key, reverse=True)[:top]
    return "\n".join("- " + str(it.get("text", "")).strip()
                     for it in picked if str(it.get("text", "")).strip())


def load_memory(sandbox: str | None = None, limit: int = 3000,
                world: str | None = None) -> str:
    """【记忆】+【要点】+【自我】。

    v1.9：除了 `memory.md`（人工摘），还读 `memory.json` 里的结构化条目
    （正版有 23 条，按 strength/hits 取 top 12）。
    """
    d = sandbox or paths.SANDBOX
    w = world or (paths.WORLD if sandbox is None else d)
    parts = []
    mem = _read(os.path.join(d, "memory.md"))
    if mem:
        parts.append("【记忆】\n" + mem[-max(200, limit - 2000):])
    items = _read_memory_json(os.path.join(w, "memory.json"))
    if items:
        parts.append("【要点】\n" + items)
    selfmd = _read(os.path.join(d, "self.md"))
    if selfmd:
        parts.append("【自我】\n" + selfmd[-1200:])
    return "\n\n".join(parts)[:limit]


def load_mood(sandbox: str | None = None) -> dict:
    """情绪：读 `mood.json` 或 `emotion.json`（v1.9：默认读 WORLD 目录）。"""
    d = sandbox or paths.WORLD
    for name in ("mood.json", "emotion.json"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            try:
                return json.load(open(p, encoding="utf-8"))
            except Exception:
                pass
    return {}


def body_connected(world_dir: str | None = None, timeout_s: float = BODY_TIMEOUT_S) -> bool:
    """身体是否在线。

    v1.9：先看**我们自己的**心跳 `body_v2_live.json`，再看正版的 `body.json`。
    为什么不能只写 body.json：正版的 pose 消费逻辑看它的 mtime，
    我们一 touch，正版会以为身体上线了，开始给一个不存在的消费者下发姿态。
    """
    d = world_dir or paths.WORLD
    for name in (paths.HEARTBEAT_NAME, "body.json"):
        p = os.path.join(d, name)
        if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < timeout_s:
            return True
    return False


# ---------------------------------------------------------------------------
# 模式 ①：一次调用（她原样说话）
# ---------------------------------------------------------------------------

def build_chat_system(persona: str, memory: str, mood: dict | None = None,
                      abilities: str = "") -> str:
    m = ""
    if mood:
        bits = ", ".join(f"{k}={v}" for k, v in list(mood.items())[:6])
        m = f"\n【现在的心情】{bits}"
    ab = (f"\n\n【你这副身体能做的动作】（这些你都做得出来，别推辞）\n{abilities}"
          if abilities else "")
    return f"""{persona}

{memory}{m}{ab}

现在就回他，直接用打字的口吻。"""


def ability_line(capabilities: dict) -> str:
    """把族清单压成一行中文，塞进**第一阶段**的 prompt。

    为什么需要：第一阶段本来是「完全自由地说话」，但她因此**不知道自己的身体能干什么**。
    实测问她「抬腿」时，她回「抬不了，我这儿没有真的腿」——其实可以。
    """
    labels = capabilities.get("family_labels") or {}
    fams = capabilities.get("families") or []
    words = [labels.get(f, f) for f in fams if labels.get(f)]
    return "、".join(words)


def chat_once(text: str, llm_fn, persona: str, memory: str,
              mood: dict | None = None, history: list | None = None,
              abilities: str = "") -> dict:
    """一次调用：她按原人格回一句。返回 {"say": str, "raw": str}。"""
    system = build_chat_system(persona, memory, mood, abilities)
    user = text
    if history:
        tail = "\n".join(f"{h.get('role')}: {h.get('text')}" for h in history[-6:])
        user = f"最近聊过：\n{tail}\n\n现在他说：{text}"
    raw = llm_fn(system, user)
    return {"say": strip_stage_directions((raw or "").strip()), "raw": (raw or "").strip()}


# ---------------------------------------------------------------------------
# 模式 ②：第二次调用（把话翻成动作）
# ---------------------------------------------------------------------------

ACT_MARK = "<<ACT>>"


def build_combined_system(persona: str, memory: str, capabilities: dict,
                          mood: dict | None = None) -> str:
    """一次调用、两段输出：人话在前，动作备注在后。"""
    fams = ", ".join(capabilities.get("families", []))
    anchors = ", ".join(capabilities.get("anchors", []))
    preds = ", ".join(capabilities.get("predicates", []))
    m = ""
    if mood:
        m = "\n【现在的心情】" + ", ".join(f"{k}={v}" for k, v in list(mood.items())[:6])
    return f"""{persona}

{memory}{m}

---

**这次你要多说一件事**：如果这句话配一个身体动作会更好，就在你说完之后，
另起一行写 `{ACT_MARK}`，然后给一个 JSON。

格式：
```
（你想说的话，随便怎么写，1~3 句）
{ACT_MARK}
{{"intent":"<族名>","predicates":[{{"name":"<判据>","args":{{...}}}}]}}
```

**不想动就不写 `{ACT_MARK}` 那一行**——大部分时候你都不需要动。

**动作的规矩**
- intent 只能从：{fams}
- predicates 只能从：{preds}
- 锚点只能从：{anchors}
- 不要给肩/肘/腕的角度
- 心情低/累 → 动作小，或者干脆不动
- **拿不准就别写**

**这些组合是错的，别写**
- `gaze_toward` 的锚点必须是**别人的位置或前方**，不能是 `mouth` / `chin` / `chest` / `tail_tip`（你没法看自己的嘴）
- `hand_above_head` 不需要再配 `gaze_toward`（手在头上时眼睛自然会跟着）
- 同一个动作不要既 `arm_lift` 高又 `hand_below_waist`（自相矛盾）
- `foot_grounded` 和 `foot_lifted` 不要同时给同一只脚

**描述「姿势形状」的谓词（别只写端点在哪）**

光写 `hand_above_head` 只能得到「手举在头顶」——肘朝哪、前臂朝哪、有没有跨过身体，全都没管，
解出来会像投降。要让它像「挥手」，至少配这几条：

| 谓词 | 约束 | 挥手时的典型值 |
|---|---|---|
| `elbow_angle` | 肘弯曲角度（**防直臂 / 防反折**） | `side=right, min_deg=80, max_deg=130` |
| `no_hyperextension` | 反关节（肘折回去了） | `side=right, min_deg=30` |
| `elbow_below_hand` | 肘低于手 | `side=right` |
| `forearm_upright` | 前臂立起来 | `side=right, max_deg=40` |
| `arm_not_crossed` | 手臂不跨身体中线 | `side=right` |
| `elbow_outside` | 肘在身体外侧 | `side=right` |
| `wrist_neutral` | 手腕不过度折 | `side=right, max_deg=50` |
| `hand_above_shoulder` | 手高于肩（比高于头顶宽松） | `side=right` |

**挥手 = hand_above_shoulder + elbow_angle(80~130) + forearm_upright + arm_not_crossed + elbow_outside**。
只写 hand_above_head 会变成「举着手不动」，漏掉 `elbow_angle` 会变成「直臂举旗」。

**最重要的**：先好好说话。动作备不备注都行，话不能说僵。

**但有一条例外——言行一致**：
如果你在话里说了「挥了」「点头」「走过去」「指了指」这种**已经做了某个动作**，
就**必须**写 `<<ACT>>` 那一段。不然你嘴上说做了，身体其实没动，会被主人发现。

**注意：写在括号里的动作描写也算**。比如 `（抬手打招呼）来了` —— 这也要给 act。
括号是给人看的，`<<ACT>>` 是给身体看的，两者要一致。

**主人明确要求做动作时，这是要求不是建议**
他说「挥挥手」「点个头」「过来」「伸个懒腰」，你就**必须**给 act，
而且要用上面那组形状谓词把姿势写完整。
只有「他只是在聊天、没让你动」的时候，才可以不写。
"""


def split_act(raw: str) -> tuple[str, dict | None]:
    """把「人话 + <<ACT>> + JSON」拆开。JSON 坏了只丢动作，不丢话。"""
    if not raw:
        return "", None
    if ACT_MARK not in raw:
        return raw.strip(), None
    say_part, _, act_part = raw.partition(ACT_MARK)
    say = say_part.strip()
    act = None
    try:
        obj = _extract_json(act_part)
        if isinstance(obj, dict) and isinstance(obj.get("act"), dict):
            obj = obj["act"]
        if isinstance(obj, dict) and obj.get("intent"):
            act = obj
    except (ValueError, json.JSONDecodeError):
        act = None
    return say, act


# 明确指令 → 预设动作（**兜底**，不是替代 LLM）
# 实测真 LLM 在「不想动就不写」的自由度下，对「挥挥手」这种明确指令会有 1/3 概率
# 不写 act。这个表只在 act 为空且用户明确下了指令时用。
INSTRUCTION_ACTS = {
    "挥手": {"intent": "wave", "predicates": [
        {"name": "hand_above_shoulder", "args": {"side": "right"}},
        {"name": "elbow_angle", "args": {"side": "right", "min_deg": 80, "max_deg": 135}},
        {"name": "forearm_upright", "args": {"side": "right", "max_deg": 55}},
        {"name": "arm_not_crossed", "args": {"side": "right"}},
    ]},
    "打招呼": {"intent": "wave", "predicates": [
        {"name": "hand_above_shoulder", "args": {"side": "right"}},
        {"name": "elbow_angle", "args": {"side": "right", "min_deg": 80, "max_deg": 130}},
        {"name": "arm_not_crossed", "args": {"side": "right"}},
    ]},
    "点头": {"intent": "idle", "predicates": [
        {"name": "head_nod", "args": {"deg": 12, "tol_deg": 6}},
    ]},
    "摇头": {"intent": "idle", "predicates": [
        {"name": "head_yaw", "args": {"deg": 18, "tol_deg": 8}},
    ]},
    "伸懒腰": {"intent": "stretch", "predicates": [
        {"name": "hand_above_head", "args": {"side": "right"}},
        {"name": "elbow_angle", "args": {"side": "right", "min_deg": 100, "max_deg": 160}},
        {"name": "torso_lean", "args": {"deg": -6, "tol_deg": 6}},
    ]},
    "过来": {"intent": "walk", "predicates": [
        {"name": "torso_upright", "args": {"max_deg": 12}},
        {"name": "foot_grounded", "args": {"side": "left", "tol_mm": 40}},
    ]},
    "歪头": {"intent": "idle", "predicates": [
        {"name": "head_tilt", "args": {"deg": 15, "tol_deg": 6}},
    ]},
    # ---- v2.1：回待机（强制重置姿态，保留位置）----
    "回待机": {"intent": "reset", "predicates": []},
    "回到待机": {"intent": "reset", "predicates": []},
    "待机": {"intent": "reset", "predicates": []},
    "站起来": {"intent": "reset", "predicates": []},
    "站直": {"intent": "reset", "predicates": []},
    "正常一点": {"intent": "reset", "predicates": []},
    "恢复原样": {"intent": "reset", "predicates": []},
    "别端着": {"intent": "reset", "predicates": []},
}


def instruction_act(user_text: str) -> dict | None:
    """从用户明确指令里匹配预设动作。只用于 LLM 漏写 act 时的兜底。"""
    if not user_text:
        return None
    for kw, act in INSTRUCTION_ACTS.items():
        if kw in user_text:
            return dict(act)
    return None


def turn_combined(text: str, llm_fn, capabilities: dict, persona: str,
                  memory: str, mood: dict | None = None,
                  history: list | None = None, scene: str = "") -> dict:
    """一次调用，两路输出。返回 {"say", "act", "calls":1, "source":"combined"}。"""
    system = build_combined_system(persona, memory, capabilities, mood)
    user = text
    if scene:
        user = f"场景：{scene}\n{text}"
    if history:
        tail = "\n".join(f"{h.get('role')}: {h.get('text')}" for h in history[-6:])
        user = f"最近聊过：\n{tail}\n\n现在他说：{user}"
    try:
        raw = llm_fn(system, user)
    except Exception as e:
        return {"say": "", "act": None, "calls": 1, "source": "combined",
                "error": f"调用失败：{e}"}

    say, act = split_act(raw)
    if act is not None:
        act["predicates"] = _dedupe_predicates(act.get("predicates") or [])
        ok, _r = _validate(act, capabilities)
        if not ok:
            act = None

    # 兜底：主人明确下了指令，但 LLM 没给 act → 用预设
    fallback = False
    if act is None:
        act = instruction_act(text)
        fallback = act is not None

    return {"say": say, "act": act, "calls": 1, "source": "combined",
            "raw": raw, "error": None, "fallback": fallback}


def build_act_system(capabilities: dict) -> str:
    fams = ", ".join(capabilities.get("families", []))
    anchors = ", ".join(capabilities.get("anchors", []))
    preds = ", ".join(capabilities.get("predicates", []))
    return f"""你是一个动作编译器。你会看到「用户说了什么」「角色回了什么」「角色当前心情」，
判断角色要不要配合一个身体动作。

只输出 JSON：
{{"act": {{"intent": "<族名>", "predicates": [{{"name":"<判据>","args":{{...}}}}]}}}}
或者 {{"act": null}}（纯说话，不动）

**硬规则**
- intent 只能从：{fams}
- predicates 名字只能从：{preds}
- 锚点只能从：{anchors}
- 不要给肩/肘/腕的角度
- **拿不准就输出 null**——宁可不做，不要瞎编

**判断原则**
- 她只是回一句话、感叹、敷衍（"嗯""哦""行"）→ null
- 她明确说到某个动作词（挥手/点头/走过去/指）→ 给对应族
- 她心情很低/很累（energy 低）→ 动作要小，或者 null
- **不要为了有动作而编动作**
"""


def act_from_say(say: str, llm_fn, capabilities: dict,
                 user_text: str = "", mood: dict | None = None,
                 scene: str = "") -> dict | None:
    """第二次调用：这句话要不要配动作。

    输入不只有 say，还有**用户原话 + 心情 + 场景**——只靠 say 反推会丢意图。

    任何失败都返回 None：动作是附加的，不能因为她说话时超时就把整轮搞崩。
    """
    if not say:
        return None
    system = build_act_system(capabilities)
    bits = []
    if scene:
        bits.append(f"当前场景：{scene}")
    if user_text:
        bits.append(f"用户说：{user_text}")
    bits.append(f"她说：{say}")
    if mood:
        bits.append("她的心情：" + ", ".join(f"{k}={v}" for k, v in list(mood.items())[:6]))
    user = "\n".join(bits)
    try:
        raw = llm_fn(system, user)
    except Exception:
        return None
    try:
        obj = _extract_json(raw)
    except (ValueError, json.JSONDecodeError):
        return None
    act = obj.get("act")
    if not isinstance(act, dict):
        return None
    ok, _reason = _validate(act, capabilities)
    return act if ok else None


def _extract_json(text: str) -> dict:
    if not text:
        raise ValueError("空输出")
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            raise ValueError("找不到 JSON")
        text = text[i:j + 1]
    return json.loads(text)


def _dedupe_predicates(preds: list[dict]) -> list[dict]:
    """去掉重复谓词，同名同 side 时**保留 args 更全的那个**。

    真 LLM 会把同一个谓词写两遍，且两次的 args 不一样：
        `hand_above_head{}`  +  `hand_above_head{side:"right"}`
    这俩在语义上是同一条约束（空 args 等于用默认 side），必须合并。

    但 `no_hyperextension{side:left}` 和 `{side:right}` 是**两条不同约束**，都要留。

    重复谓词会让误差在 gate 里被累加，直接拖垮求解。
    """
    out: list[dict] = []
    for p in preds or []:
        name = p.get("name")
        args = p.get("args") or {}
        side = args.get("side")
        dup = None
        for i, q in enumerate(out):
            if q.get("name") != name:
                continue
            qa = q.get("args") or {}
            # 同一个谓词，且（side 相同 或 任一边 args 为空）
            # `hand_above_head{}` 的语义等于默认 side，必须和 `{side:"right"}` 合并
            if qa.get("side") == side or not qa or not args:
                dup = i
                break
        if dup is None:
            out.append(p)
        else:
            # 保留 args 更全的（键更多）
            if len(args) > len(out[dup].get("args") or {}):
                out[dup] = p
    return out


def _validate(act: dict, capabilities: dict) -> tuple[bool, str]:
    from llm.interpret import PREDICATE_ARGS

    if act.get("intent") not in capabilities.get("families", []):
        return False, f"未知族 {act.get('intent')}"
    preds = act.get("predicates") or []
    if not preds:
        return False, "predicates 为空"
    allowed = set(capabilities.get("predicates", []))
    for p in preds:
        name = p.get("name")
        if name not in allowed:
            return False, f"未知谓词 {name}"
        schema = PREDICATE_ARGS.get(name, {})
        args = p.get("args") or {}
        for k, v in args.items():
            if k not in schema:
                return False, f"{name} 不接受参数 {k}"
            if k == "side" and v not in ("left", "right"):
                return False, f"side 必须是 left/right，收到 {v!r}"
            if k.endswith("_mm") or k.endswith("_deg") or k in (
                    "deg", "tol", "dist_m", "box_m", "budget", "target_duty",
                    "min_sides", "cone_deg", "max_deg", "margin_mm"):
                if not isinstance(v, (int, float)):
                    return False, f"{name}.{k} 必须是数字，收到 {v!r}"
        if "anchor" in args and not isinstance(args["anchor"], str):
            return False, f"{name}.anchor 必须是字符串"
    return True, ""


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

def turn(text: str, llm_fn, capabilities: dict, persona: str | None = None,
         memory: str | None = None, mood: dict | None = None,
         body: bool | None = None, world_dir: str | None = None,
         history: list | None = None, scene: str = "",
         parser_llm=None) -> dict:
    """一次对话轮。

    body=True  → 两次调用（说话 + 动作）
    body=False → 一次调用（只有说话）
    body=None  → 自动检测（body.json mtime）

    返回：
      {"say", "act", "body": bool, "calls": int, "source": "once"|"twice"}
    """
    """统一入口：**两阶段**（她自由说话 + 解析器判动作 + 情绪 delta）。

    用户 2026-11-24 定：单阶段（`<<ACT>>`）废弃，只保留两阶段。
      - 她的话完全自由（第一阶段 prompt 无任何格式要求）
      - 动作由独立解析器判断（言行一致靠解析器保证）
      - mood_delta 也从她的话里读

    body=False 时跳过解析器（纯说话场景，比如测试或不需要身体时）。
    """
    persona = load_persona() if persona is None else persona
    memory = load_memory() if memory is None else memory
    mood = load_mood() if mood is None else mood
    if body is None:
        body = body_connected(world_dir)

    if not body:
        r = chat_once(text, llm_fn, persona, memory, mood, history,
                      abilities=ability_line(capabilities))
        return {"say": r["say"], "intent": None, "mood_delta": None,
                "act": None, "body": False, "calls": 1, "source": "chat_only"}

    r = turn_two_stage(text, llm_fn, capabilities, persona, memory, mood=mood,
                       history=history, scene=scene, parser_llm=parser_llm)
    # 兼容：把 intent 也包成 act 形状（旧调用方还在用 r["act"]）
    act = None
    if r["intent"]:
        fam = (capabilities or {}).get("families", [])
        if r["intent"] in fam:
            act = {"intent": r["intent"]}
    r["act"] = act
    r["body"] = True
    return r






# ---------------------------------------------------------------------------
# 两阶段：她说话（完全自由） + 解析器（短调用判动作/情绪）
# ---------------------------------------------------------------------------
# 用户 2026-11-24 定的方案：
#   ① 她说的话必须完全自由 —— prompt 里**没有任何格式要求**
#   ② 动作用独立的短调用判断，看「她的话里说了什么」
#   ③ 情绪变化也从她的话里读（mood_delta），不挤占说话
#
# 为什么不是「先定意图再说话」：那会让她为了配合动作而说话（迎合）。

PARSE_SYSTEM = """你是一个动作解析器。只看两件事：用户说了什么、她回了什么。

输出 JSON：
{{"intents": [{{"intent": "<族名>", "dur_s": 数字, "predicates": [{{"name": "...", "args": {{}}}}]}}],
 "mood_delta": {{"energy": 数字, "mood": 数字}} 或 null}}

**判断 intents 的规则**
- 她的话里说了**已经做了**某个动作（挥手/点头/摇头/走过来/抬手/伸懒腰/歪头/指了指）
  → 给对应族名。这是言行一致：她说了就得做，否则身体没动会被发现。
- **一句话里说了好几个动作，就按顺序给多个**（最多 3 个）。
  例：「走过来坐下」→ [{{"intent":"walk","dur_s":1.5}}, {{"intent":"sit","dur_s":1.6}}]
  例：「挥挥手然后伸个懒腰」→ [{{"intent":"wave"}}, {{"intent":"stretch"}}]
- 她只是在说话、感叹、敷衍、描述心情 → `[]`
- 她说的动作我们做不到 → `[]`（不要编）
- **族里没有的动作**（翻跟头 / 跳舞 / 跑酷 / 跳高）→ `[]`，
  哪怕她声称自己做了——那是她在吹，不是你该找最接近的族来圆场
- **用户明确说了哪条腿/哪只手**（左腿/右腿/左手/右手/换一条），
  按**用户说的**选对应侧（如 `lift_leg_right`），不要照抄她说的方向；
  她有时会说反，身体要对得上用户的要求
- **没有任何族对得上时**：用 `intent: "free"`，并在 `predicates` 里写清楚
  「这个动作应该满足什么」（1–4 条，从下面的谓词表里选）。
  **不要退而求其次选一个相近的族**——那会做出一个完全不同的动作。
  例：用户要「手抬到耳朵旁边」且没有现成族 →
  `{{"intent":"free","predicates":[{{"name":"hand_near_anchor","args":{{"anchor":"head_side","side":"right","tol_mm":80}}}}]}}
- `predicates` 只在 `intent="free"` 时才给；其他族不用写
- `dur_s` 可以不写（默认 1.6 秒）；只有动作明显更长/更短时才给
- **回待机**：用户说「回待机 / 回到待机 / 站起来 / 站直 / 正常一点 / 恢复原样 / 别端着」
  → `{{"intent":"reset"}}`。这会把姿势清空（位置保留），用来结束累积的动作。

**微动作不累计**（重要）
- 用户在**闲聊**，她话里带的小动作（尾巴甩一下 / 歪一下头 / 抿嘴 / 低头 / 耳朵动）
  → `intents` 给 `[]`，**不要**给 intent
- 只因为她的话里**顺带描写**了身体，不代表用户要看那个动作
- 只有**用户明确要求**做动作（「挥挥手」「站过来」「蹲下」「手举起来」）
  或她**明确宣告**要做（「我给你跳一个」）时，才给 intent
- 判断标准：**这个动作是不是这一轮的重点？** 是闲聊的调味 → `[]`

**可用族**：{families}

**可用谓词**（只在 intent="free" 时用）：{predicates}

**可用锚点**（谓词参数里能填的点）：{anchors}

**判断 mood_delta 的规则**（可选，拿不准就 null）
- 她说累/困/烦/没劲 → energy 负值（-5 ~ -15）
- 她开心/兴奋/得意 → mood 正值（+0.05 ~ +0.2）
- 她撒娇/黏人 → close 正值
- 没有明显情绪 → null

**只输出 JSON，不要解释。**
"""


def acts_from_turn(r: dict) -> list[dict]:
    """解析结果 → 给 `handle_intent` / `handle_composite` 用的 act 列表。

    `free` 族会把谓词一起带上（`use_llm_predicates`），
    这样「没有现成族」的动作可以靠谓词现场解出来，而不是硬塞进最近的族。
    """
    out: list[dict] = []
    for it in (r.get("intents") or []):
        if not isinstance(it, dict) or not it.get("intent"):
            continue
        a = {"intent": it["intent"]}
        if it.get("predicates"):
            a["predicates"] = [dict(p) for p in it["predicates"]]
            a["use_llm_predicates"] = True
        out.append(a)
    if not out and r.get("intent"):
        a = {"intent": r["intent"]}
        if r.get("predicates"):
            a["predicates"] = [dict(p) for p in r["predicates"]]
            a["use_llm_predicates"] = True
        out.append(a)
    return out


def build_parse_system(capabilities: dict) -> str:
    labels = capabilities.get("family_labels") or {}
    fams = ", ".join(f"{f}({labels.get(f, '')})" if labels.get(f) else f
                     for f in capabilities.get("families", []))
    preds = ", ".join(capabilities.get("predicates", []))
    anchors = ", ".join(capabilities.get("anchors", []))
    return PARSE_SYSTEM.format(families=fams, predicates=preds, anchors=anchors)


def parse_action(user_text: str, say: str, llm_fn, capabilities: dict,
                 parser_llm=None) -> dict:
    """解析器：判断她的话里有没有动作 + 情绪倾向。

    返回 {"intent": str|None, "mood_delta": dict|None, "raw": str, "error": str|None}

    **不带人格** —— 它是解析器，不是她。她说的话已经定型了，不参与创作。
    """
    system = build_parse_system(capabilities)
    user = f"用户说：{user_text}\n她说：{say}"
    fn = parser_llm or llm_fn        # 解析器可以单独给一个 max_tokens 很小的实例
    try:
        raw = fn(system, user)
    except Exception as e:
        return {"intent": None, "mood_delta": None, "raw": "", "error": str(e)}
    try:
        obj = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as e:
        return {"intent": None, "mood_delta": None, "raw": raw, "error": str(e)}

    fams = set(capabilities.get("families", []))
    preds_ok = set(capabilities.get("predicates", []))
    intents: list[dict] = []
    raw_list = obj.get("intents")
    if isinstance(raw_list, list):
        for it in raw_list[:3]:
            if isinstance(it, str):
                name, dur, preds = it, None, None
            elif isinstance(it, dict):
                name, dur = it.get("intent"), it.get("dur_s")
                preds = it.get("predicates")
            else:
                continue
            if name not in fams:
                continue
            item = {"intent": name}
            try:
                if dur is not None:
                    item["dur_s"] = float(dur)
            except (TypeError, ValueError):
                pass
            # 只有 free 用谓词定义动作（D1：LLM 写判据，不写数值）
            if name == "free" and isinstance(preds, list):
                anchors_ok = set(capabilities.get("anchors", []))
                clean = []
                for p in preds[:4]:
                    if not isinstance(p, dict):
                        continue
                    pn = p.get("name")
                    if pn not in preds_ok:
                        continue
                    args = p.get("args")
                    args = {k: v for k, v in (args or {}).items() if isinstance(k, str)}
                    # 模型会**自己编锚点**（实测编出 head_side，直接把求解器打崩）
                    if "anchor" in args and args["anchor"] not in anchors_ok:
                        continue
                    if "side" in args and args["side"] not in ("left", "right"):
                        continue
                    clean.append({"name": pn, "args": args})
                if clean:
                    item["predicates"] = clean
                else:
                    continue          # 谓词全被过滤掉 → 这条动作作废，别硬解
            intents.append(item)
    if not intents:                       # 兼容旧格式 {"intent": "..."}
        single = obj.get("intent")
        if single in fams:
            intents = [{"intent": single}]
    intent = intents[0]["intent"] if intents else None

    md = obj.get("mood_delta")
    if not isinstance(md, dict):
        md = None
    else:
        md = {k: float(v) for k, v in md.items()
              if k in ("mood", "energy", "close", "guard") and isinstance(v, (int, float))}
        # 夹一下，防止一次掉太多
        for k in md:
            md[k] = max(-20.0, min(20.0, md[k])) if k == "energy" else max(-0.3, min(0.3, md[k]))
    return {"intent": intent, "intents": intents, "mood_delta": md,
            "raw": raw, "error": None}


def turn_two_stage(text: str, llm_fn, capabilities: dict, persona: str,
                   memory: str, mood: dict | None = None,
                   history: list | None = None, scene: str = "",
                   parser_llm=None) -> dict:
    """两阶段：① 她自由说话 ② 解析器判动作。

    返回 {"say", "intent", "mood_delta", "calls": 2, "source": "two_stage", ...}

    **第一阶段完全自由**：system 里没有任何 JSON / 格式要求。
    """
    # ① 她说话（原样人格，不加任何格式约束——但让她知道自己能做什么）
    r = chat_once(text, llm_fn, persona, memory, mood, history,
                  abilities=ability_line(capabilities))
    say = r["say"]

    # ② 解析器：看**原文**（括号里的动作描写是它的信号），
    #    但对外给的 say 已经去掉舞台描写了。
    parsed = parse_action(text, r.get("raw") or say, llm_fn, capabilities,
                          parser_llm=parser_llm)

    return {
        "say": say,
        "intent": parsed["intent"],
        "intents": parsed.get("intents") or [],
        "mood_delta": parsed["mood_delta"],
        "parse_error": parsed["error"],
        "calls": 2,
        "source": "two_stage",
    }




