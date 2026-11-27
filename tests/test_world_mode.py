"""tests/test_world_mode.py — 世界线模式切换：默认沙箱，正版需显式且要备份。

设计依据：v1.9「接正版三步」——模式开关 / 备份门槛 / 心跳隔离

怎么测：python tests/test_world_mode.py
预期现象：[world_mode] ALL PASS
调参位置：llm/paths.py set_world / assert_writable
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm import paths  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  ' + detail) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    sandbox_path = os.path.join(paths.SANDBOX_DIR, "x.json")
    prod_path = os.path.join(paths.PRODUCTION_WORLD, "mood.json")

    print("[1] 默认是沙箱")
    paths.set_world("sandbox")
    check("MODE=sandbox", paths.MODE == "sandbox", paths.MODE)
    check("BASE/WORLD 都是 sandbox", paths.BASE == paths.WORLD == paths.SANDBOX_DIR)
    check("沙箱内可写", paths.writable(sandbox_path))
    check("正版不可写", not paths.writable(prod_path))
    check("心跳文件是自家的", os.path.basename(paths.heartbeat_path()) == "body_v2_live.json",
          paths.heartbeat_path())

    print("[2] 切正版只读：能读不能写")
    paths.set_world("production")
    check("MODE=production", paths.MODE == "production", paths.MODE)
    check("BASE 指向正版根", paths.BASE == paths.PRODUCTION, paths.BASE)
    check("WORLD 指向 world/", paths.WORLD == os.path.join(paths.PRODUCTION, "world"), paths.WORLD)
    check("正版仍然不可写", not paths.writable(prod_path))
    check("心跳指向正版 world/", paths.heartbeat_path().startswith(paths.PRODUCTION_WORLD))

    print("[3] 没备份时，可写模式必须被拒")
    real_latest = paths.latest_backup
    paths.latest_backup = lambda: None
    try:
        paths.set_world("production_rw")
        check("无备份应拒绝", False, "竟然通过了")
    except PermissionError as e:
        check("无备份应拒绝", True, str(e).splitlines()[0][:40])
    finally:
        paths.latest_backup = real_latest
        paths.set_world("sandbox")

    print("[4] 有备份才允许可写")
    paths.latest_backup = lambda: r"D:\fake\world-20260101-000000.zip"
    try:
        paths.set_world("production_rw")
        check("MODE=production_rw", paths.MODE == "production_rw", paths.MODE)
        check("正版可写", paths.writable(prod_path))
    except PermissionError:
        check("有备份应允许", False)
    finally:
        paths.latest_backup = real_latest
        paths.set_world("sandbox")

    print("[5] 切回沙箱后状态干净")
    check("MODE=sandbox", paths.MODE == "sandbox", paths.MODE)
    check("正版又不可写", not paths.writable(prod_path))
    check("沙箱可写", paths.writable(sandbox_path))
    check("SANDBOX 与 BASE 同步", paths.SANDBOX == paths.BASE)

    print("[6] 未知模式要报错")
    try:
        paths.set_world("moonbase")
        check("拒绝未知模式", False, "竟然通过了")
    except ValueError:
        check("拒绝未知模式", True)

    # 兜底：不管上面怎么折腾，最后一定回到沙箱
    paths.set_world("sandbox")

    print("-" * 52)
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)}) -> {FAILS}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
