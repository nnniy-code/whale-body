"""llm/client.py — DeepSeek 客户端（openai 兼容）。

设计依据：docs/design/06-llm-schema.md

**只读 .env，绝不打印 key**。
调用是**无状态**的：不发历史、不写 world/、不碰她的记忆文件。
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from llm.paths import ENV_CANDIDATES


def _read_env(path: str) -> dict:
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def find_api_key(env_path: str | None = None, var: str = "DEEPSEEK_API_KEY") -> str | None:
    """按顺序找 key：显式 env_path > 候选 .env > 环境变量。"""
    paths = [env_path] if env_path else ENV_CANDIDATES
    for p in paths:
        if not p:
            continue
        v = _read_env(p).get(var)
        if v:
            return v
    return os.environ.get(var) or None


class DeepSeek:
    """`llm_fn(system, user) -> str` 的实现。"""

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com",
                 model: str = "deepseek-chat", temperature: float = 1.15,
                 timeout_s: float = 30.0, max_tokens: int = 800):
        if not api_key or len(api_key) < 10:
            raise ValueError("API key 无效")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = float(temperature)
        self.timeout_s = float(timeout_s)
        self.max_tokens = int(max_tokens)
        self.calls = 0
        self.last_error = None

    def __call__(self, system: str, user: str) -> str:
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
            method="POST",
        )
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                data = json.loads(r.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:200]
            self.last_error = f"HTTP {e.code}: {detail}"
            raise RuntimeError(self.last_error) from e
        except Exception as e:
            self.last_error = str(e)
            raise


def redact(text: str) -> str:
    """把疑似 key 替换掉（日志/打印前用）。"""
    return re.sub(r"sk-[A-Za-z0-9]{16,}", "sk-***", text or "")
