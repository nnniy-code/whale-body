"""core/library.py — 参数库与意图检索。

设计依据：docs/design/04-library.md

三条核心：
  - intent_key 是结构化三元组（谓词集 / 锚点集 / 动词 / 数值），不用文本 embedding
  - 距离 <0.25 视为命中；<0.05 视为同一动作的又一解（就地更新）
  - 状态机 provisional / verified / rejected / failed / stale

params 不含情绪：情绪在播放时叠加（第十份设计）。
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Iterable

STATUSES = ("provisional", "verified", "rejected", "failed", "stale")
PLAYABLE = ("provisional", "verified")
SEARCHABLE = ("provisional", "verified")

# 距离权重（04-library §2）
W_PRED = 0.50
W_ANCHOR = 0.20
W_VERB = 0.15
W_NUM = 0.15

HIT_THRESHOLD = 0.25
DEDUP_THRESHOLD = 0.05
FALLBACK_THRESHOLD = 0.45


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 1.0


def intent_key(predicates: list[dict], verb: str | None = None) -> dict:
    return {
        "predicates": sorted({p["name"] for p in predicates}),
        "anchors": sorted({str(v) for p in predicates
                           for v in p.get("args", {}).values()
                           if isinstance(v, str)}),
        # 左右是**硬区分**：只手/只脚的动作，side 不同就是不同动作。
        # 以前 side 只混在 anchors 里占 0.2 权重，左右手距离 0.134 < 0.25 → 被当成同一个。
        "side": sorted({str(p["args"]["side"]) for p in predicates
                        if isinstance(p.get("args", {}).get("side"), str)}),
        "verb": verb or "",
        "numeric": {p["name"]: float(p["args"]["tol_mm"])
                    for p in predicates
                    if "tol_mm" in p.get("args", {})},
    }


def key_distance(a: dict, b: dict) -> float:
    # 族不同 = 不同动作，直接判最大距离。
    # 以前 verb 只占 0.15，导致「抬左腿」和「抬右腿」距离 0.15 < 阈值 0.25，
    # 换个腿会命中上一条（实测：她说右腿，库里回的是左腿那条）。
    va, vb = a.get("verb", ""), b.get("verb", "")
    if va and vb and va != vb:
        return 1.0
    # 左右不同 = 不同动作（左腿/右腿、左手/右手）
    sa, sb = a.get("side") or [], b.get("side") or []
    if sa and sb and sa != sb:
        return 1.0
    d = W_PRED * (1.0 - jaccard(a.get("predicates", []), b.get("predicates", [])))
    d += W_ANCHOR * (1.0 - jaccard(a.get("anchors", []), b.get("anchors", [])))
    d += W_VERB * (0.0 if va == vb else 1.0)
    na, nb = a.get("numeric", {}), b.get("numeric", {})
    common = set(na) & set(nb)
    if na and nb and common:
        diffs = [min(1.0, abs(na[k] - nb[k]) / max(1.0, abs(na[k]) + abs(nb[k])))
                 for k in common]
        d += W_NUM * (sum(diffs) / len(diffs))
    elif not na and not nb:
        pass                      # 两边都没数值 → 该项不贡献（同 key 距离必须为 0）
    else:
        d += W_NUM * 0.5          # 一边有一边没有 → 信息不足，中性
    return min(1.0, d)


@dataclass
class Entry:
    id: str
    status: str
    family: str
    intent_key: dict
    params: dict
    dur_s: float = 1.0
    metrics: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    predicates: list = field(default_factory=list)   # 求解时用的谓词（用于「族自动生长」）
    parent: str | None = None
    created_at: str = ""
    history: list = field(default_factory=list)
    violations: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "id": self.id, "status": self.status, "family": self.family,
            "intent_key": self.intent_key, "params": self.params, "dur_s": self.dur_s,
            "metrics": self.metrics, "evidence": self.evidence, "parent": self.parent,
            "created_at": self.created_at, "history": self.history,
            "violations": self.violations,
        }


class Library:
    def __init__(self, log=None):
        self.entries: dict[str, Entry] = {}
        self.log = log
        self._next = 1

    # ---- 检索 ----
    def searchable(self) -> list[Entry]:
        return [e for e in self.entries.values() if e.status in SEARCHABLE]

    def best(self, key: dict, threshold: float = HIT_THRESHOLD,
             count_hit: bool = True) -> tuple[Entry | None, float]:
        best, best_d = None, 1e9
        for e in self.searchable():
            d = key_distance(key, e.intent_key)
            if d < best_d:
                best, best_d = e, d
        if best is not None and best_d <= threshold:
            if count_hit:
                # 命中计数：这是「这个动作被反复请求」的证据，族生长要用
                best.metrics["hits"] = int(best.metrics.get("hits", 0)) + 1
            return best, best_d
        return None, best_d if best is not None else 1.0

    def query(self, key: dict, k: int = 5) -> list[tuple[Entry, float]]:
        out = [(e, key_distance(key, e.intent_key)) for e in self.searchable()]
        out.sort(key=lambda t: (t[1], t[0].id))
        return out[:k]

    # ---- 写入 ----
    def put(self, family: str, key: dict, params: dict, dur_s: float = 1.0,
            status: str = "provisional", parent: str | None = None,
            metrics: dict | None = None, evidence: dict | None = None,
            trace_id: str | None = None,
            predicates: list | None = None) -> Entry:
        if status not in STATUSES:
            raise ValueError(f"非法状态 {status}")
        # 去重：距离 <0.05 视为同一动作
        cand, d = self.best(key, threshold=DEDUP_THRESHOLD)
        if cand is not None:
            cand.history.append({"params": cand.params, "metrics": cand.metrics,
                                 "replaced_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
            cand.history = cand.history[-5:]
            cand.params = params
            cand.metrics = metrics or cand.metrics
            cand.status = status
            if self.log:
                self.log.lib_put({**cand.as_dict(), "replaced": True,
                                  "trace_id": trace_id})
            return cand

        eid = f"lib-{self._next:04d}"
        self._next += 1
        e = Entry(id=eid, status=status, family=family, intent_key=key,
                  params=params, dur_s=dur_s, parent=parent,
                  metrics={**(metrics or {}), "hits": int((metrics or {}).get("hits", 0)) + 1},
                  evidence=evidence or {},
                  predicates=list(predicates or []),
                  created_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        self.entries[eid] = e
        if self.log:
            self.log.lib_put({**e.as_dict(), "trace_id": trace_id})
        return e

    def set_status(self, entry_id: str, status: str, violations: list | None = None,
                   trace_id: str | None = None) -> Entry:
        if status not in STATUSES:
            raise ValueError(f"非法状态 {status}")
        e = self.entries[entry_id]
        frm = e.status
        e.status = status
        if violations is not None:
            e.violations = violations
        if self.log:
            self.log.lib_status(entry_id, frm, status,
                                violations=violations or [], trace_id=trace_id)
        return e

    # ---- 统计 / 持久化 ----
    def counts(self) -> dict:
        out = {s: 0 for s in STATUSES}
        for e in self.entries.values():
            out[e.status] = out.get(e.status, 0) + 1
        return out

    def dump(self) -> dict:
        return {eid: e.as_dict() for eid, e in sorted(self.entries.items())}

    def load(self, data: dict) -> None:
        self.entries = {}
        for eid, d in data.items():
            self.entries[eid] = Entry(**{k: d[k] for k in d if k in Entry.__dataclass_fields__})
        self._next = max([int(eid.split("-")[1]) for eid in self.entries], default=0) + 1

    # ---- 持久化 ----
    def save(self, path: str) -> None:
        """写盘（原子替换，避免写一半坏掉）。"""
        import os
        import tempfile
        payload = {"version": 1, "next": self._next, "entries": self.dump()}
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def save_to(self, path: str) -> str:
        self.save(path)
        return path

    @classmethod
    def open(cls, path: str, log=None) -> "Library":
        """从文件读；文件不存在就返回空库（不报错）。"""
        import os
        lib = cls(log=log)
        if os.path.exists(path):
            try:
                data = json.load(open(path, encoding="utf-8"))
                lib.load(data.get("entries", {}))
                lib._next = int(data.get("next", lib._next))
            except Exception:
                pass
        return lib

    @staticmethod
    def replay(lines: Iterable[str]) -> dict:
        """从 library.jsonl 重放出库快照（用于自检）。"""
        entries: dict[str, dict] = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            t = ev.get("type")
            if t == "lib_put":
                d = {k: v for k, v in ev.items()
                     if k in Entry.__dataclass_fields__ and k != "replaced"}
                d.pop("trace_id", None)
                entries[d["id"]] = d
            elif t == "lib_status":
                eid = ev.get("entry_id")
                if eid in entries:
                    entries[eid]["status"] = ev["to"]
                    if ev.get("violations"):
                        entries[eid]["violations"] = ev["violations"]
        return entries




