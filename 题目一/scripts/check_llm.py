"""LLM 配置诊断：只打印元信息与一次调用的状态，不打印密钥本身。

用法：python scripts/check_llm.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_env, get_settings  # noqa: E402


def describe_key(key: str) -> str:
    if not key:
        return "（空）"
    flags = []
    if key != key.strip():
        flags.append("含首尾空白")
    if any(ch in key for ch in ('"', "'")):
        flags.append("含引号")
    if any(ord(ch) > 127 for ch in key):
        flags.append("含非 ASCII 字符")
    if " " in key.strip():
        flags.append("中间有空格")
    return "长度=%d 前缀=%s %s" % (len(key), key[:3], "；".join(flags) if flags else "无异常字符")


def main() -> int:
    env_path = ROOT / ".env"
    print("工作目录      :", os.getcwd())
    print(".env 路径     :", env_path, "存在" if env_path.exists() else "不存在")
    loaded = load_env(str(env_path))
    print(".env 解析到    :", sorted(loaded.keys()) if loaded else "（空）")

    settings = get_settings()
    print("LLM_BASE_URL  :", settings["base_url"])
    print("LLM_MODEL     :", settings["model"])
    print("LLM_API_KEY   :", describe_key(settings["api_key"]))

    if not settings["api_key"]:
        print("\n结论：没有读到 key。检查 .env 是否在仓库根目录、变量名是否为 LLM_API_KEY。")
        return 1

    url = settings["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": settings["model"],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 4,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + settings["api_key"]},
        method="POST",
    )
    print("\nPOST", url)
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            print("HTTP", resp.status)
            print("响应片段:", body[:200].replace("\n", " "))
            print("\n结论：鉴权通过，模型可调用。")
            return 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        print("HTTP", exc.code)
        print("响应片段:", body[:400].replace("\n", " "))
        if exc.code == 401:
            print("\n结论：密钥被拒。常见原因：key 与 base_url 不是同一家；key 过期/被删；key 前后带引号或空格。")
        elif exc.code == 404:
            print("\n结论：端点路径不对。确认 base_url 写成 https://xxx/v1 这种形式（不要带 /chat/completions）。")
        return 1
    except OSError as exc:
        print("网络层错误:", exc)
        print("\n结论：连不上该域名，检查网络/代理或 base_url 拼写。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
