"""llm/paths.py — 世界线路径的**唯一来源**（v1.9）。

三种模式（`MODE`）：

| 模式 | BASE / WORLD | 能不能写 |
|---|---|---|
| `sandbox`（默认） | `body-v2/sandbox` | 能（只在沙箱内） |
| `production` | `dsh-qq` / `dsh-qq/world` | **不能**（只读演习） |
| `production_rw` | 同上 | 能，但**必须先有备份** |

铁律：

1. 默认永远指向沙箱。切正版要显式调用 `set_world()`。
2. 想写正版必须 `set_world("production_rw")`，而它会检查 `backups/world-*.zip` 是否存在；
   没备份直接拒绝。
3. **心跳不写 `body.json`**。正版那边的 pose 消费逻辑看 `body.json` 的 mtime，
   我们一 touch 它，正版会以为身体上线了。所以用自家的 `body_v2_live.json`。

允许碰 `dsh-qq` 的只有三处，都不在常规运行时路径上：

- `tools/make_sandbox.py`：重新生成沙箱时读源
- `ENV_CANDIDATES` 最后一项：只读 API key
- `production` 模式下**只读**人格/记忆/情绪（这正是接正版要的）
"""

from __future__ import annotations

import glob
import os

LLM_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(LLM_DIR)

#: 测试副本（人格 / 记忆 / 自我 / 情绪 / 世界线都在这里，一个目录装全部）
SANDBOX_DIR = os.path.join(PROJECT, "sandbox")

#: 正版（拆两处：人格/记忆在根，情绪/世界线在 world/）
PRODUCTION = r"D:\DSproject\dsh-qq"
PRODUCTION_WORLD = os.path.join(PRODUCTION, "world")

#: 备份目录（接正版前必须有一份）
BACKUP_DIR = os.path.join(PROJECT, "backups")

#: 我们自己的心跳文件（**不是** body.json）
HEARTBEAT_NAME = "body_v2_live.json"

# ---- 当前生效的布局 ----
MODE = "sandbox"
_BASE = SANDBOX_DIR
_WORLD = SANDBOX_DIR

_env_base = os.environ.get("BODY_V2_BASE")
_env_world = os.environ.get("BODY_V2_WORLD")
if _env_base or _env_world:
    _BASE = _env_base or _env_world
    _WORLD = _env_world or _BASE
    MODE = "custom"

#: 兼容旧名字
BASE = _BASE
WORLD = _WORLD
SANDBOX = _BASE


def set_world(mode: str = "sandbox") -> tuple[str, str]:
    """切换世界线。返回 (BASE, WORLD)。

    `production_rw` 必须有备份才允许——没有就抛 PermissionError。
    """
    global MODE, _BASE, _WORLD, BASE, WORLD, SANDBOX
    mode = (mode or "sandbox").strip()
    if mode in ("sandbox", "test", ""):
        _BASE = _WORLD = SANDBOX_DIR
        MODE = "sandbox"
    elif mode in ("production", "prod"):
        _BASE, _WORLD = PRODUCTION, PRODUCTION_WORLD
        MODE = "production"
    elif mode in ("production_rw", "prod_rw"):
        if not latest_backup():
            raise PermissionError(
                "接正版可写之前必须先备份：python tools/backup_world.py")
        _BASE, _WORLD = PRODUCTION, PRODUCTION_WORLD
        MODE = "production_rw"
    else:
        raise ValueError(f"未知世界线模式：{mode}")
    BASE, WORLD, SANDBOX = _BASE, _WORLD, _BASE
    return BASE, WORLD


def heartbeat_path() -> str:
    return os.path.join(WORLD, HEARTBEAT_NAME)


def latest_backup() -> str | None:
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "world-*.zip")))
    return files[-1] if files else None


def writable(path: str) -> bool:
    """这条路径现在能不能写（不抛异常版）。"""
    try:
        assert_writable(path)
        return True
    except PermissionError:
        return False


#: API key 查找顺序：项目内 → 沙箱 → 正式项目（只读凭据兜底）
ENV_CANDIDATES = [
    os.path.join(PROJECT, ".env"),
    os.path.join(SANDBOX_DIR, ".env"),
    os.path.join(PRODUCTION, ".env"),
]


def is_in_sandbox(path: str) -> bool:
    try:
        p = os.path.realpath(os.path.abspath(path))
        s = os.path.realpath(os.path.abspath(SANDBOX_DIR))
    except OSError:
        return False
    return p == s or p.startswith(s + os.sep)


def is_production(path: str) -> bool:
    try:
        p = os.path.realpath(os.path.abspath(path))
        d = os.path.realpath(os.path.abspath(PRODUCTION))
    except OSError:
        return False
    return p == d or p.startswith(d + os.sep)


def assert_writable(path: str) -> str:
    """写之前调用。越界直接抛 PermissionError，返回原路径方便链式用。"""
    if is_production(path):
        if MODE == "production_rw":
            return path
        raise PermissionError(
            f"拒绝写入正版（当前模式 {MODE}）：{path}\n"
            f"要写正版先 set_world('production_rw')，它会要求先备份。")
    if not is_in_sandbox(path):
        raise PermissionError(f"拒绝写入沙箱外：{path}")
    return path
