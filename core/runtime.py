"""core/runtime.py — 端到端接线：一句话 → 查库/求解 → 入库 → 姿态。

设计依据：HANDOFF §4.5、§16.3；docs/design/05-dispatcher.md

职责：
  - handle_intent：L1 查库 → 未命中走 L2 → 成功入库 → 返回播放决策
  - capabilities：生成「她现在能做什么」的清单（塞进 LLM prompt）
  - capability_gap：做不到时，告诉 LLM **缺的是哪一维**

不碰 bpy：姿态以 {bone: 4x4} 返回，写骨由调用方用 binder 做。
"""

from __future__ import annotations

import time

import numpy as np

from core import gate as G
from core import pose_gen
from core.library import Library, key_distance, intent_key
from core.predicates import PREDICATES, Frame
from core.solver import MAIN21, fast_rig, make_frame, solve, vec_to_P


HAND_PREDICATES_DEFAULT_RIGHT = {
    "hand_above_head", "hand_below_waist", "hand_near_anchor", "hand_at_point",
}


def _infer_side(predicates: list[dict]) -> str | None:
    """推断「只动哪一侧」。

    顺序：显式 side > 手部谓词的默认值（right）> None（两侧动）。
    为什么要这一步：谓词 `hand_above_head` 的默认 side 是 right，
    但 `pose_gen` 默认两侧都驱动——不推断的话「挥手」会变成「投降」。
    """
    for p in predicates or []:
        args = p.get("args") or {}
        s = args.get("side")
        if s in ("left", "right"):
            return s
    for p in predicates or []:
        if p.get("name") in HAND_PREDICATES_DEFAULT_RIGHT:
            return "right"
    return None


class Runtime:
    def __init__(self, rig, bm, anchors_cfg, families: dict, library: Library | None = None,
                 log=None, capsules=None, collision_table=None, lengths=None,
                 capsule_baseline=None, capsule_only=None, thresholds=None):
        self.rig = rig
        self.rig_fast = fast_rig(rig)
        self.bm = bm
        self.anchors_cfg = anchors_cfg
        self.families = families
        self.library = library or Library(log=log)
        self.log = log
        self.capsules = capsules
        self.collision_table = collision_table
        self.lengths = lengths
        self.capsule_baseline = capsule_baseline
        self.capsule_only = capsule_only or (MAIN21 + ["WhaleTail_01", "WhaleTail_02",
                                                       "WhaleTail_03", "WhaleTail_04"])
        self.thresholds = thresholds or {}
        self.trace_n = 0
        # 身体状态（D 第②步）：位置/朝向/bob，由 tick() 积分
        from core.body_state import BodyState
        self.body = BodyState()
        self._t = 0.0                # 步态相位时钟（tick 累加），build_full 默认用它
        self.foot_target = None      # 当前脚目标源（族决定）
        self._lock_side = None       # 支撑脚（脚锁的锚）
        self._lock_world = None      # 支撑脚锁定的世界 xy
        self._full_body = True       # 是否用全身装配（上半身 + 腿 IK）
        self._last_P: dict | None = None   # 当前姿态的 P（轨迹规划的起点）
        self._last_full_diag: dict = {}

        # 动作混合状态（200ms 惯性化）
        self._basis: dict | None = None
        self._from_basis: dict | None = None
        self._blend_t0 = 0.0
        self._blend_ms = 0.0
        self._composite_sig: tuple | None = None   # 上一帧的动作组合（判断要不要重新过渡）

    # ---- 能力清单（给 LLM 的 prompt 用）----
    # 注意：gaze_toward 不给 LLM 选。它要求静态姿态里头的朝向精确对准某个锚点，
    # 实测 LLM 会写成 gaze_toward(anchor="mouth") / "head_top"（看自己的嘴/头顶），
    # 物理上不可能，还会把整轮求解拖进 3000 次失败。
    # 视线跟随本来就该由 L0 底噪自动处理，不需要每次动作指定。
    HIDDEN_FROM_LLM = {"gaze_toward"}

    def capabilities(self) -> dict:
        preds = sorted(p for p in PREDICATES if p not in self.HIDDEN_FROM_LLM)
        return {
            "families": sorted(self.families.keys()),
            #: 中文标签，给 LLM 对词用（「抬腿」→ lift_leg）
            "family_labels": {k: (v.get("label") or k) for k, v in self.families.items()},
            "predicates": preds,
            "anchors": sorted(self.anchors_cfg.get("points", {}).keys()),
            "ranges": {name: f.get("P_range", {}) for name, f in self.families.items()},
            "note": "只能从上面选；做不到的目标会被拒绝并告诉你缺什么",
        }

    # ---- 主入口 ----
    def handle_intent(self, act: dict, now: float | None = None,
                      mood: dict | None = None) -> dict:
        now = time.perf_counter() if now is None else now
        self.trace_n += 1
        trace_id = act.get("trace_id") or f"t-{int(now*1000)}-{self.trace_n:03d}"
        family_name = act.get("intent") or act.get("family") or "idle"
        predicates = act.get("predicates") or []
        side = _infer_side(predicates)

        # v2.1：回待机（强制重置姿态，**保留位置**）
        if family_name in ("reset", "stand_up", "reset_pose", "idle_reset"):
            self.reset_pose()
            if self.log:
                self.log.emit("pose_reset", trace_id=trace_id)
            return {"entry": None, "source": "reset", "capability_gap": None,
                    "reset": True,
                    "message": "", "body": self.body.as_dict()}

        # 2.5 谓词来源：**族有模板时模板优先**
        # 用户 2026-11-24 定的：解决「谓词膨胀」——LLM 只决定用哪个族，
        # 细谓词由模板给（模板是验证过的）。LLM 给的 predicates 只在
        # 族没有模板、或 act 显式要求 use_llm_predicates 时生效。
        family = self.families.get(family_name)
        used_template = False
        if family is not None:
            tpl = family.get("predicates_template") or []
            want_llm = bool(act.get("use_llm_predicates"))
            if tpl and (not predicates or not want_llm):
                if predicates:
                    act["_llm_predicates_ignored"] = True
                predicates = [dict(p) for p in tpl]
                used_template = True
                if side is None:
                    side = _infer_side(predicates)
            # 双手动作（双手平举/伸懒腰）不能被侧别推断砍成单手
            if (family.get("two_handed")):
                side = None

        key = act.get("intent_key") or intent_key(predicates, family_name)

        # 1) L1 查库
        t0 = time.perf_counter()
        hit, dist = self.library.best(key)
        lookup_ms = (time.perf_counter() - t0) * 1000
        if self.log:
            self.log.retrieve(trace_id, hit is not None, lookup_ms,
                              entry_id=hit.id if hit else None, score=dist)

        if hit is not None:
            # 记下这次的说法（同一个动作可能被叫成「手叉腰」「叉着腰」）
            lbl = str(act.get("user_text") or "")[:40]
            if lbl and hit.metrics is not None:
                labs = hit.metrics.setdefault("labels", [])
                if lbl not in labs and len(labs) < 6:
                    labs.append(lbl)
            return {
                "trace_id": trace_id, "source": "L1", "entry": hit,
                "lookup_ms": round(lookup_ms, 3),
                "predicates": predicates, "family": hit.family,
                "capability_gap": None,
            }

        # 2) 族不存在 → 能力缺口
        family = self.families.get(family_name)
        if family is None:
            gap = self._gap(trace_id, act, family_name, "family_missing",
                            suggested=f"可用的族：{sorted(self.families)[:6]}…")
            return {"trace_id": trace_id, "source": "gap", "entry": None,
                    "lookup_ms": round(lookup_ms, 3), "capability_gap": gap,
                    "predicates": predicates, "family": family_name}


        # 3) L2 求解
        # 3a) 情绪先改「目标」（不是缩放 P）——累了是「手抬到肩就够」，不是「挥手幅度小」
        bias_rep = {"changed": False}
        if mood:
            from core.emotion import bias_predicates
            predicates, bias_rep = bias_predicates(predicates, mood)

        try:
            res = solve(self.rig, self.bm, family, predicates, self.anchors_cfg,
                        thresholds=self.thresholds, budget=act.get("budget", 3000),
                        log=self.log, trace_id=trace_id, active_side=side,
                        capsules=self.capsules, collision_table=self.collision_table,
                        lengths=self.lengths, capsule_baseline=self.capsule_baseline,
                        capsule_only=self.capsule_only)
        except Exception as e:                 # 坏谓词/坏锚点不该把整个引擎打崩
            gap = self._gap(trace_id, act, family_name, "solve_error",
                            suggested=f"{type(e).__name__}: {e}"[:120])
            return {"trace_id": trace_id, "source": "L2_error", "entry": None,
                    "lookup_ms": round(lookup_ms, 3), "capability_gap": gap,
                    "predicates": predicates, "family": family_name}

        if not res["converged"]:
            gap = self._gap(trace_id, act, family_name, "solve_failed",
                            suggested=str(res["violations"][:2]),
                            detail=res["violations"])
            return {"trace_id": trace_id, "source": "L2_fail", "entry": None,
                    "lookup_ms": round(lookup_ms, 3), "capability_gap": gap,
                    "predicates": predicates, "family": family_name,
                    "solve": res}

        # 4) 入库（**立即验证**，不是夜间批处理）
        # L2 求解 20ms + 验证 2.8ms —— 用户无感，所以不需要 provisional 中间态
        from core.verify import verify_pose
        # pose_for_from_P 现在返回 basis；verify_pose 要世界矩阵，这里显式换算
        M = self.rig_fast.compute(
            self.pose_for_from_P(res["params"]["P"], family_name, side))
        chk = verify_pose(res["params"]["P"], family, predicates, M, self.rig_fast,
                          self.anchors_cfg, self.capsules, self.collision_table,
                          self.lengths, self.capsule_baseline, side=side)
        if not chk["ok"]:
            gap = self._gap(trace_id, act, family_name, "verify_failed",
                            suggested=chk["reason"], detail=[chk["checks"]])
            return {"trace_id": trace_id, "source": "L2_rejected", "entry": None,
                    "lookup_ms": round(lookup_ms, 3), "capability_gap": gap,
                    "predicates": predicates, "family": family_name,
                    "solve": res, "verify": chk}

        # 4b) 抖动：库存「基准」P*，播放时在 P* 附近抖一下（每次略不同）
        from core.jitter import safe_perturb
        from core.verify import range_violations

        def _quick_ok(P):
            if range_violations(P, family):
                return False
            M2 = self.rig_fast.compute(self.pose_for_from_P(
                {k: v for k, v in P.items() if k in self.rig_fast.names or True},
                family_name, side))
            from core.collision import check_capsules as _cc
            return not _cc(M2, self.capsules, self.rig_fast, self.collision_table,
                           baseline=self.capsule_baseline, lengths=self.lengths,
                           chain_skip=3, only=self.capsule_only)

        P_star = dict(res["params"]["P"])
        P_play, jit_rep = safe_perturb(P_star, family, mood, _quick_ok)

        entry = self.library.put(family_name, key, res["params"],
                                 dur_s=act.get("dur_s", 1.0), status="verified",
                                 metrics={"evals": res["evals"], "calc_ms": res["calc_ms"],
                                          "side": side, "jitter": jit_rep.get("attempts", 0),
                                          # 用户原话，用来给「长大后」的族起中文名
                                          "label": str(act.get("user_text") or "")[:40] or None},
                                 evidence={"trace_id": trace_id, "checks": chk["checks"],
                                           "jitter": jit_rep.get("delta", {}),
                                           "emotion_bias": bias_rep},
                                 trace_id=trace_id,
                                 predicates=[dict(p) for p in predicates])
        return {
            "trace_id": trace_id, "source": "L2", "entry": entry,
            "lookup_ms": round(lookup_ms, 3), "predicates": predicates,
            "family": family_name, "solve": res, "verify": chk,
            "play_P": P_play, "jitter": jit_rep, "emotion_bias": bias_rep,
            "capability_gap": None,
        }

    # ---- 多动作叠加 ----
    def handle_composite(self, acts: list[dict], now: float | None = None,
                         mood: dict | None = None,
                         blend_ms: float | None = None) -> dict:
        """多个动作同时：第一个当 base（**全身**，含腿），后面按 groups 叠加。

        `acts` 每项：
            {"intent": "walk"}                                    → base
            {"intent": "wave", "groups": ["arm.right"], "weight": 1.0}  → 叠加层

        流程：
            每个 act 单独走 handle_intent（查库/求解/验证）
            → base 走 `build_full`（全身装配，腿跟着走）
            → 叠加层只取上半身 basis
            → overlay.merge_layers 合并（按 weight 定优先级）
            → 组合变了才 transition_to（200ms），没变只 set_pose 更新目标

        步态相位用 `self.t`（tick 累加）；调用方每帧 `tick(dt)` 才会动。

        返回 {"basis", "entries", "report", "sources", "gaps", "full_body"}
        """
        if not acts:
            return {"basis": None, "entries": [], "report": {},
                    "sources": [], "gaps": []}

        from core.blend import DEFAULT_BLEND_MS
        from core.overlay import merge_layers

        results, gaps = [], []
        for act in acts:
            r = self.handle_intent(act, now=now, mood=mood)
            results.append(r)
            if r.get("capability_gap"):
                gaps.append(r["capability_gap"])

        # 建各自的 basis（v1.9：pose_for_from_P 直接返回 basis）
        # 叠加层永远只改上半身；**base 走全身装配**（含腿 IK），见下面 base_idx 之后。
        bases = []
        for i, (act, r) in enumerate(zip(acts, results)):
            if not r.get("entry"):
                bases.append(None)
                continue
            side = (r["entry"].metrics or {}).get("side")
            P = r.get("play_P") or r["entry"].params.get("P", {})
            bases.append(self.pose_for_from_P(P, act["intent"], side))

        # 第一个能解出来的当 base
        base_basis, base_idx = None, None
        for i, b in enumerate(bases):
            if b is not None:
                base_basis, base_idx = b, i
                break
        if base_basis is None:
            return {"basis": None, "entries": [r.get("entry") for r in results],
                    "report": {}, "sources": [r.get("source") for r in results],
                    "gaps": gaps}

        # base 换成**全身装配**（腿跟着走），叠加层不变
        full_body = False
        if self._full_body:
            act_b, r_b = acts[base_idx], results[base_idx]
            side_b = (r_b["entry"].metrics or {}).get("side")
            P_b = r_b.get("play_P") or r_b["entry"].params.get("P", {})
            base_basis, diag_b = self.build_full(P_b, act_b["intent"],
                                                 self._t, active_side=side_b)
            self._last_full_diag = diag_b
            full_body = True
            # 族自己声明的转向速度（如 spin）也要生效。
            # 以前只有 set_motion(family=) 会读 G.turn_rate，复合路径里永远不转。
            fam_G = (self.families.get(r_b["entry"].family) or {}).get("G") or {}
            self.body.turn_rate = float(fam_G.get("turn_rate", 0.0))

        # 其余按 groups 叠加
        layers = []
        for i, (act, b) in enumerate(zip(acts, bases)):
            if b is None or i == base_idx:
                continue
            groups = act.get("groups") or ["arm.right"]
            layers.append((b, groups, float(act.get("weight", 1.0))))

        import os as _os
        if _os.environ.get("DSH_COMPOSITE_DEBUG"):
            for i, b in enumerate(bases):
                if b is not None:
                    print(f"[composite] base=idx{base_idx} layer{i}={acts[i].get('intent')} "
                          f"hand_z={float(b['rightHand'][2,3]):.3f}")
        merged, report = merge_layers(base_basis, layers)
        import os as _os
        if _os.environ.get("DSH_COMPOSITE_DEBUG"):
            print(f"[composite] merged hand_z={float(merged['rightHand'][2,3]):.3f} layers={len(layers)}")

        # 过渡：**只有动作组合变了才重新起 200ms**。
        # 同一组每帧调用只是更新目标（set_pose），否则过渡计时每帧清零 → 硬切。
        sig = tuple((a.get("intent"), tuple(a.get("groups") or ("arm.right",)))
                    for a in acts)
        switched = sig != self._composite_sig
        self._composite_sig = sig
        if switched:
            self.transition_to(merged, now=now,
                               duration_ms=blend_ms if blend_ms is not None else DEFAULT_BLEND_MS)
        else:
            self.set_pose(merged, now=now)

        return {
            "basis": merged,
            "entries": [r.get("entry") for r in results],
            "report": report,
            "sources": [r.get("source") for r in results],
            "gaps": gaps,
            "layers": len(layers),
            "switched": switched,
            "full_body": full_body,
            "conflicts": report.get("conflicts", []),
        }

    # ---- 能力缺口 ----
    def _gap(self, trace_id: str, act: dict, family: str, kind: str,
             suggested: str = "", detail=None) -> dict:
        gap = {
            "kind": kind,
            "wanted": act.get("say") or act.get("intent"),
            "family": family,
            "predicates": [p.get("name") for p in act.get("predicates", [])],
            "suggested": suggested,
            "detail": detail or [],
        }
        if self.log:
            self.log.emit("capability_gap", trace_id=trace_id, **gap)
        return gap

    # ---- 姿态 ----
    def _copy_basis(self, basis: dict) -> dict:
        return {k: np.array(v, dtype=float, copy=True) for k, v in basis.items()}

    def set_pose(self, basis: dict, now: float | None = None) -> None:
        """**更新目标**，不重新开始过渡（连续相位用）。

        v1.9：`transition_to` 是「动作切换」语义。每帧调它会把过渡计时清零，
        结果是「延迟一帧的硬切」（实测切换时单帧跳 433.9mm）。
        连续变化（走路每帧、波形动画）必须用这个。
        """
        import time as _t
        now = _t.perf_counter() if now is None else now
        self._basis = self._copy_basis(basis)
        if self._from_basis is None:
            self._blend_t0 = now
            self._blend_ms = 0.0

    def transition_to(self, basis: dict, now: float | None = None,
                      duration_ms: float | None = None) -> None:
        """从**当前显示的姿态**过渡到目标姿态（默认 200ms 惯性化）。

        没有当前姿态时直接落在目标上（第一帧不该淡入）。
        「起点」取的是过渡中的实际姿态，不是上一帧的目标——否则切换时会跳。
        """
        import time as _t
        if duration_ms is None:
            from core.blend import DEFAULT_BLEND_MS
            duration_ms = DEFAULT_BLEND_MS
        now = _t.perf_counter() if now is None else now
        if self._basis is None:
            self.set_pose(basis, now)
            return
        cur = self.current_basis(now)
        self._from_basis = self._copy_basis(cur if cur is not None else self._basis)
        self._basis = self._copy_basis(basis)
        self._blend_t0 = now
        self._blend_ms = float(duration_ms)

    def current_basis(self, now: float | None = None) -> dict | None:
        """取当前时刻的姿态（过渡中会自动插值）。"""
        import time as _t
        if self._basis is None:
            return None
        if self._from_basis is None or self._blend_ms <= 0:
            return self._basis
        from core.blend import blend_basis, progress
        now = _t.perf_counter() if now is None else now
        a = progress((now - self._blend_t0) * 1000.0, self._blend_ms)
        if a >= 1.0:
            self._from_basis = None
            return self._basis
        return blend_basis(self._from_basis, self._basis, a)

    def current_pose(self, now: float | None = None) -> dict | None:
        b = self.current_basis(now)
        return self.rig_fast.compute(b) if b is not None else None

    # ---- D 第②步：身体推进 + 全身装配 ----
    def tick(self, dt: float) -> None:
        """推进身体状态（位置/朝向/bob）+ 步态时钟。每帧调用。

        v1.9：如果当前是步态族，**身体位置由支撑脚反推**（脚锁）——
        支撑脚在世界里钉死，身体绕它进退/转身，所以既不滑步也不滑脚。
        """
        dt = max(0.0, float(dt))
        self._t += dt
        self.body.step(dt)
        self._apply_foot_lock()

    def _apply_foot_lock(self) -> None:
        """支撑脚当轴：用「锁定的世界位置 − 旋转后的局部偏移」反推身体 xy。"""
        from core.foot_target import GaitTarget
        ft = self.foot_target
        if not isinstance(ft, GaitTarget):
            self._lock_side = self._lock_world = None
            return
        side = ft.stance_side(self._t, prefer=self._lock_side)
        if side is None:
            return
        pos = ft.positions(self.body, self._t).get(side)
        if pos is None:
            return
        a = np.radians(float(self.body.yaw))
        c, s = np.cos(a), np.sin(a)
        # 与 assembly.rot_z 同一约定（标准 CCW）
        lx = c * float(pos[0]) - s * float(pos[1])
        ly = s * float(pos[0]) + c * float(pos[1])
        if side != self._lock_side or self._lock_world is None:
            self._lock_world = (self.body.x + lx, self.body.y + ly)
            self._lock_side = side
            return
        self.body.x = self._lock_world[0] - lx
        self.body.y = self._lock_world[1] - ly

    @property
    def t(self) -> float:
        """步态相位时钟（秒）。唯一来源是 tick()。"""
        return self._t

    def set_motion(self, vel: float | None = None, turn_rate: float | None = None,
                   family: str | None = None) -> None:
        """设置前进/转向速度（由动作层调，不直接改位置）。

        v1.9：给 `family` 就自动取该族**唯一正确**的速度
        `v = stride/(duty×period)`，并带上 `G.turn_rate`。
        步态族的 `vel` 不是自由量——填错脚就滑（vel=0.5 时一个支撑期滑 118mm）。
        """
        if family is not None:
            fam = self.families.get(family) or {}
            G = fam.get("G") or {}
            if vel is None:
                v = self.speed_for(family)
                if v != 0.0:
                    vel = v
            if turn_rate is None:
                turn_rate = float(G.get("turn_rate", 0.0))
        if vel is not None:
            self.body.vel = float(vel)
        if turn_rate is not None:
            self.body.turn_rate = float(turn_rate)

    def speed_for(self, family_name: str) -> float:
        """该族步态隐含的身体速度（m/s）；站桩/指定点族返回 0。"""
        from core.gait import body_speed
        fam = self.families.get(family_name) or {}
        return body_speed(fam.get("G") or {})

    def foot_target_for(self, family_name: str, start_feet: dict | None = None):
        """按族名选脚目标源。没有步态的族返回 StaticTarget（站着不动）。"""
        from core.foot_target import GaitTarget, StaticTarget
        fam = self.families.get(family_name) or {}
        G = fam.get("G") or {}
        stride = float(G.get("stride", 0.0))
        if start_feet is None:
            M0 = self.rig_fast.compute({n: np.eye(4) for n in self.rig_fast.names})
            start_feet = {s: [float(M0[f"{s}Foot"][0, 3]), float(M0[f"{s}Foot"][1, 3]), 0.0]
                          for s in ("left", "right")}
        if abs(stride) > 1e-6:
            return GaitTarget(G, start_feet), start_feet
        # 站桩族也可以声明髋要降多少（坐下 / 跪 / 蹲）。
        # v1.9 之前这个值没人读，所以 sit 族看起来和站着一样。
        # `lift` 让某只脚抬起来（单腿站 / 抬腿），值是脚底离地米数。
        return StaticTarget(start_feet,
                            hip_drop=float(G.get("hip_drop", 0.0)),
                            lift=G.get("lift") or None), start_feet

    def build_full(self, P: dict, family_name: str, t: float | None = None,
                   start_feet: dict | None = None,
                   active_side: str | None = None) -> tuple[dict, dict]:
        """全身装配：上半身 + 腿。

        `t` 省略就用 `self.t`（`tick()` 累加的步态时钟），别再让调用方
        顺手传帧号或墙钟——相位会跳。
        """
        t = self._t if t is None else float(t)
        from core.assembly import build_full_body
        ft, feet = self.foot_target_for(family_name, start_feet)
        self.foot_target = ft
        # 速度自洽：步态族的速度由 stride/duty/period 决定，偏了就会滑。
        # 这里兜一道底，并把修正写进 diag（不静默）。
        v = ft.speed()
        vel_sync = None
        if v != 0.0:
            if abs(self.body.vel - v) > 1e-6:
                vel_sync = {"was": round(float(self.body.vel), 4), "now": round(v, 4)}
                self.body.vel = v
        elif abs(self.body.vel) > 1e-9:
            # v1.9：从步态族切到站桩族（走完→坐下）时，速度必须归零。
            # 不清的话她会以 0.32 m/s 一直往前滑（实测 5 秒漂 4 米，直接出画）。
            vel_sync = {"was": round(float(self.body.vel), 4), "now": 0.0}
            self.body.vel = 0.0
        basis, diag = build_full_body(P, ft, self.body, t, self.rig, self.bm,
                                      active_side=active_side)
        if vel_sync:
            diag["vel_sync"] = vel_sync
        return basis, diag

    def basis_for_from_P(self, P: dict, family_name: str, side: str | None = None) -> dict:
        """由 P 算 **basis**（局部矩阵）。族里没给的维度用 P_default 补齐。"""
        fam = self.families.get(family_name) or {}
        full = dict(fam.get("P_default") or {})
        full.update(P or {})
        return pose_gen.build(full, self.rig_fast, self.bm, active_side=side)

    def pose_for_from_P(self, P: dict, family_name: str, side: str | None = None) -> dict:
        """【v1.9】由 P 直接算 **basis**（不经库条目）。

        返回 basis 而不是世界矩阵——因为下游（`apply_basis` / `merge_layers` /
        `build_full_body` / `transition_to`）**全都吃 basis**。
        要世界矩阵请显式 `self.rig_fast.compute(basis)`。把返回值当世界矩阵读
        `[0:3,3]` 只会得到 0（v1.8 的叠加 bug 就是这么来的）。
        """
        return self.basis_for_from_P(P, family_name, side)

    def pose_for(self, entry, side: str | None = None, as_basis: bool = False) -> dict:
        """由库条目算出姿态（不写骨）。

        side：只驱动这一侧手臂。**不传就会两只手一起举**——挥手变投降。
        优先级：显式参数 > entry.metrics.side > None（两侧）。

        as_basis=False（默认）返回**世界矩阵**，兼容 `apply_pose` 和旧调用；
        as_basis=True 返回 basis，给 `apply_basis` / `merge_layers` 用。
        """
        if side is None:
            side = (entry.metrics or {}).get("side")
        P = entry.params.get("P", {})
        basis = pose_gen.build(P, self.rig_fast, self.bm, active_side=side)
        return basis if as_basis else self.rig_fast.compute(basis)

    # ---- v2.0：起点 → 终点的轨迹求解 ----
    def plan_trajectory(self, act: dict, n_frames: int = 20,
                        start_P: dict | None = None) -> dict:
        """对一个动作规划完整轨迹（起点 = 当前姿态，终点 = 求解结果）。

        返回 `solve_path` 的结果，额外带 `entry` / `play_P` / `source`。
        **不改变** `self._last_P`，播完再调 `commit_trajectory()`。
        """
        from core.trajectory_solver import solve_path

        r = self.handle_intent(act, now=time.perf_counter())
        if not r.get("entry"):
            from core.explain import explain_gap
            gaps = [r["capability_gap"]] if r.get("capability_gap") else []
            return {"frames": [], "mode": "no_entry", "entry": None,
                    "gaps": gaps,
                    "message": explain_gap(gaps[0]) if gaps else "这个动作我做不出来。"}

        fam = self.families.get(act.get("intent") or "") or {}
        P_end = dict(r.get("play_P") or r["entry"].params.get("P") or {})
        # 起点优先级：显式传入 > 上次姿态 > **保持的姿态** > 中立站姿（idle）
        # 用户 2026-11-27 定的：默认保持当前姿势，说「回待机」才重置。
        P_start = dict(start_P or self._last_P
                       or self.body.P_hold
                       or ((self.families.get("idle") or {}).get("P_default") or {}))
        side = (r["entry"].metrics or {}).get("side")

        tr = solve_path(
            P_start, P_end, n_frames=n_frames, rig=self.rig_fast, bm=self.bm,
            side=side, anchors_cfg=self.anchors_cfg,
            caps=self.capsules, tab=self.collision_table,
            lengths=self.lengths, base=self.capsule_baseline,
            only=self.capsule_only,
        )
        tr["entry"] = r["entry"]
        tr["play_P"] = P_end
        tr["source"] = r.get("source")
        tr["intent"] = act.get("intent")
        return tr

    def commit_trajectory(self, tr: dict) -> None:
        """播完之后：记住终点 P（**含腿的状态**），下次从这里出发。

        v2.1：默认保持——不说「回待机」就一直保持这个姿势。
        """
        if tr.get("play_P"):
            self._last_P = dict(tr["play_P"])
            hip_drop = 0.0
            if self.foot_target is not None:
                hip_drop = float(self.foot_target.hip_drop())
            self.body.hold_pose(tr["play_P"], hip_drop,
                                getattr(self.body, "foot_source_hold", "static"))

    def reset_pose(self) -> None:
        """回到待机：清空姿态，**保留位置**。"""
        self._last_P = None
        self.body.reset_pose()

    def explain(self, r: dict) -> str:
        """把结果转成给用户看的一句话（失败原因 / 无）。"""
        from core.explain import explain_trajectory
        if "frames" in (r or {}):
            return r.get("message") or explain_trajectory(r)
        from core.explain import explain_gap
        return explain_gap((r or {}).get("capability_gap"))

    def trajectory_basis(self, tr: dict, family_name: str, t: float = 0.0,
                         active_side: str | None = None) -> list[dict]:
        """把轨迹的每帧 P 转成 basis（可逐帧 apply_basis）。"""
        out = []
        for P in tr.get("frames") or []:
            b, _d = self.build_full(P, family_name, t, active_side=active_side)
            out.append(b)
        return out

    def frame_for(self, entry) -> Frame:
        return make_frame(self.pose_for(entry), self.anchors_cfg)













