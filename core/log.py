"""core/log.py — 事件日志（按 docs/LOGGING.md 的 schema）。

设计原则：
  - append-only，不改写
  - 每条自带完整语义（交接时只看日志也能读懂）
  - 写日志失败**不抛异常**（日志不能把主流程搞挂）
  - seq 单调递增，用于发现丢条

两种用法：
    log = EventLog(ROOT)                      # 默认写 logs/
    log.emit("boot", bones=209, fps=30)
    log.gate(verdict, family="wave", trace_id=...)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

FILES = {
    "events": "events.jsonl",
    "library": "library.jsonl",
    "health": "health.jsonl",
    "errors": "errors.jsonl",
    "review": "review.jsonl",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


class EventLog:
    def __init__(self, root: str | None = None, echo: bool = False,
                 flush_every: int = 1):
        if root is None:
            root = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
            )
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.echo = echo
        self.flush_every = max(1, int(flush_every))
        self.seq = 0
        self._buf: dict[str, list[str]] = {k: [] for k in FILES}
        self._fh: dict[str, object] = {}
        self._errors = 0

    # ---- 底层 ----
    def _open(self, kind: str):
        if kind not in self._fh:
            path = os.path.join(self.root, FILES[kind])
            self._fh[kind] = open(path, "a", encoding="utf-8", newline="\n")
        return self._fh[kind]

    def _flush(self, kind: str):
        buf = self._buf[kind]
        if not buf:
            return
        try:
            self._open(kind).write("\n".join(buf) + "\n")
            self._open(kind).flush()
        except Exception:
            self._errors += 1
        finally:
            self._buf[kind] = []

    def close(self):
        for k in list(self._fh):
            self._flush(k)
            try:
                self._fh[k].close()
            except Exception:
                pass
        self._fh.clear()

    def emit(self, kind: str, file: str = "events", **fields):
        """写一条事件。kind 是 type 字段。"""
        self.seq += 1
        ev = {"v": 1, "ts": now_iso(), "seq": self.seq, "type": kind}
        for k, v in fields.items():
            if k not in ev:
                ev[k] = v
        line = json.dumps(ev, ensure_ascii=False, separators=(",", ":"))
        self._buf[file].append(line)
        if self.echo:
            print(line, file=sys.stderr)
        if len(self._buf[file]) >= self.flush_every:
            self._flush(file)
        return ev

    # ---- 便捷方法 ----
    def boot(self, **kw):
        return self.emit("boot", **kw)

    def golden(self, ok: bool, max_err_mm: float, cases: int, asserts: dict | None = None):
        # key 是 "pass"，不能直接写成关键字参数
        return self.emit("golden", **{
            "pass": bool(ok),
            "max_err_mm": round(float(max_err_mm), 6),
            "cases": cases,
            "asserts": asserts or {},
        })

    def intent(self, trace_id: str, **kw):
        return self.emit("intent", trace_id=trace_id, **kw)

    def retrieve(self, trace_id: str, hit: bool, ms: float, entry_id=None,
                 score=None, status=None, reason=None):
        return self.emit("retrieve", trace_id=trace_id, hit=hit, ms=round(ms, 3),
                         entry_id=entry_id, score=None if score is None else round(score, 4),
                         status=status, reason=reason)

    def gate(self, trace_id: str, verdict, family: str | None = None):
        d = verdict.as_dict()
        return self.emit("gate", trace_id=trace_id, family=family, **d)

    def play(self, trace_id: str, entry_id, source: str, latency_ms: float | None = None,
             blend_ms: int = 0, **kw):
        return self.emit("play", trace_id=trace_id, entry_id=entry_id, source=source,
                         blend_ms=blend_ms,
                         start_latency_ms=None if latency_ms is None else round(latency_ms, 1),
                         **kw)

    def lib_put(self, entry: dict):
        return self.emit("lib_put", file="library", **entry)

    def lib_status(self, entry_id: str, frm: str, to: str, **kw):
        return self.emit("lib_status", file="library", **{
            "entry_id": entry_id,
            "from": frm,
            "to": to,
            **kw,
        })

    def emotion(self, axes: dict, delta: dict | None = None, label: str | None = None):
        return self.emit("emotion", axes={k: round(v, 2) for k, v in axes.items()},
                         delta=delta, label=label)

    def error(self, code: str, detail: str, fatal: bool = False):
        self._errors += 1
        return self.emit("error", file="errors", code=code, detail=detail, fatal=fatal)

    def stall(self, ms: float, during: str, action: str):
        return self.emit("stall", ms=round(ms, 1), during=during, action=action)

    @property
    def error_count(self) -> int:
        return self._errors
