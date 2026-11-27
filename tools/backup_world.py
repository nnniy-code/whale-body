"""tools/backup_world.py — 接正版前先备份她的世界线。

备份内容（打包成一个 zip）：

    dsh-qq/persona.md
    dsh-qq/memory.md
    dsh-qq/world/**         （17.8 MB：含 stickers 11.3 / voice 3.3 / .runtime 1.9）

输出到 `body-v2/backups/world-YYYYmmdd-HHMMSS.zip`，并**重新打开验证**条目数。

用法：
    python tools/backup_world.py          # 备份一次
    python tools/backup_world.py --list   # 看看已有备份
"""

import argparse
import json
import os
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from llm import paths  # noqa: E402

SRC = paths.PRODUCTION
WORLD = paths.PRODUCTION_WORLD
OUT = paths.BACKUP_DIR
EXTRAS = ["persona.md", "memory.md"]


def build(dst: str) -> dict:
    n, total = 0, 0
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for name in EXTRAS:
            p = os.path.join(SRC, name)
            if os.path.exists(p):
                z.write(p, arcname=name)
                n += 1
                total += os.path.getsize(p)
        for dirpath, _dirs, files in os.walk(WORLD):
            for f in files:
                p = os.path.join(dirpath, f)
                arc = os.path.relpath(p, SRC)
                try:
                    z.write(p, arcname=arc)
                except OSError:
                    continue
                n += 1
                total += os.path.getsize(p)
    return {"entries": n, "src_bytes": total}


def verify(dst: str) -> dict:
    with zipfile.ZipFile(dst) as z:
        bad = z.testzip()
        names = z.namelist()
    return {"entries": len(names), "bad": bad,
            "has_world": any(n.startswith("world/") for n in names),
            "has_persona": "persona.md" in names}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list:
        if not os.path.isdir(OUT):
            print("还没有备份")
            return 0
        for f in sorted(os.listdir(OUT)):
            p = os.path.join(OUT, f)
            print(f"{f}  {os.path.getsize(p)/1e6:.1f} MB  {time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(p)))}")
        return 0

    if not os.path.isdir(WORLD):
        print(json.dumps({"ok": False, "error": f"找不到 {WORLD}"}, ensure_ascii=False))
        return 2

    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, "world-" + time.strftime("%Y%m%d-%H%M%S") + ".zip")
    info = build(dst)
    info.update(verify(dst))
    info.update({"path": dst, "zip_mb": round(os.path.getsize(dst) / 1e6, 2),
                 "ok": bool(info["entries"] > 0 and info["bad"] is None
                            and info["has_world"] and info["has_persona"])})
    print(json.dumps(info, ensure_ascii=False))
    return 0 if info["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
