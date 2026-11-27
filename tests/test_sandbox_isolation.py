"""tests/test_sandbox_isolation.py — 联调只碰测试沙箱，绝不碰正式项目。

设计依据：用户 2026-11-25「LLM 用测试版的复制，不要用正式那套」、llm/paths.py

背景：`D:\\DSproject\\dsh-qq` 是**完整项目**（Live2D / 语音 / 贴纸 / 世界线都在跑）。
      body-v2 只读 `sandbox/` 里的人格 / 记忆 / 情绪 / 世界线副本。

v1.8 的漏点：`body_connected()` 默认去读 `dsh-qq/world/body.json` 的 mtime，
             等于拿正式项目的状态判断测试里身体在不在线。

怎么测：python tests/test_sandbox_isolation.py
预期现象：[sandbox_isolation] ALL PASS
调参位置：llm/paths.py
"""

from __future__ import annotations

import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm import paths  # noqa: E402
from llm.persona import body_connected, load_persona  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    print("[1] 沙箱路径收敛在一处")
    check("SANDBOX 在项目内", paths.SANDBOX.startswith(ROOT + os.sep), paths.SANDBOX)
    check("SANDBOX 就是 sandbox/", os.path.abspath(paths.SANDBOX) ==
          os.path.join(ROOT, "sandbox"), paths.SANDBOX)
    check("WORLD 指向沙箱（不是 dsh-qq）", paths.WORLD == paths.SANDBOX, paths.WORLD)
    check("WORLD 不在正式项目里", not paths.is_production(paths.WORLD))

    print("[2] 沙箱文件齐全")
    for f in ("persona.md", "memory.md", "mood.json", "body.json"):
        p = os.path.join(paths.SANDBOX, f)
        check(f, os.path.exists(p), p)

    print("[3] 默认读沙箱（不传参数）")
    p = os.path.join(paths.SANDBOX, "persona.md")
    want = open(p, encoding="utf-8").read()
    got = load_persona()
    check("load_persona() == sandbox/persona.md", got[:200] == want[:200])
    check("load_persona() 非空", len(got) > 100, f"{len(got)} 字")

    print("[4] body_connected 默认看沙箱的 body.json")
    bp = os.path.join(paths.SANDBOX, "body.json")
    st = os.stat(bp)
    try:
        os.utime(bp, None)                       # 刚更新 → 应判在线
        check("刚更新 → 在线", body_connected() is True)
        old = time.time() - 3600
        os.utime(bp, (old, old))                 # 一小时前 → 应判离线
        check("一小时前 → 离线", body_connected() is False)
    finally:
        os.utime(bp, (st.st_atime, st.st_mtime))  # 还原，别弄脏沙箱

    print("[5] 源码里不许出现正式项目路径（llm/paths.py 除外）")
    hits = []
    for f in glob.glob(os.path.join(ROOT, "llm", "*.py")) + \
             glob.glob(os.path.join(ROOT, "core", "*.py")):
        rel = os.path.relpath(f, ROOT).replace("\\", "/")
        if rel == "llm/paths.py":
            continue
        txt = open(f, encoding="utf-8", errors="ignore").read()
        if "dsh-qq" in txt:
            hits.append(rel)
    check("没有硬编码引用", not hits, str(hits))

    print("[6] API key 候选顺序：项目内 → 沙箱 → 正式（只读兜底）")
    c = paths.ENV_CANDIDATES
    check("第一个在项目内", os.path.dirname(c[0]) == ROOT, c[0])
    check("最后一个是正式项目（只读）", paths.is_production(c[-1]), c[-1])
    check("顺序不颠倒", paths.is_production(c[-1]) and not paths.is_production(c[0]))

    print("[7] 写保护")
    ok = paths.assert_writable(os.path.join(paths.SANDBOX, "x.json"))
    check("沙箱内可写", ok.endswith("x.json"))
    for bad, why in ((os.path.join(paths.PRODUCTION_WORLD, "body.json"), "正式项目"),
                     (os.path.join(ROOT, "core", "x.py"), "沙箱外")):
        try:
            paths.assert_writable(bad)
            check(f"拒绝写 {why}", False, "没抛")
        except PermissionError:
            check(f"拒绝写 {why}", True)

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
