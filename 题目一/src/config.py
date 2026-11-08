"""配置加载：只读 .env 与环境变量，绝不把密钥写进仓库或 trace。"""
from __future__ import annotations

import os
from typing import Dict, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_env(path: Optional[str] = None) -> Dict[str, str]:
    """极简 .env 解析：KEY=VALUE，忽略空行与 # 注释。"""
    env_path = path or os.path.join(ROOT, ".env")
    loaded: Dict[str, str] = {}
    if not os.path.exists(env_path):
        return loaded
    with open(env_path, "r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            loaded[key] = value
            os.environ.setdefault(key, value)
    return loaded


def get_settings() -> Dict[str, str]:
    load_env()
    return {
        "api_key": os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or "",
        "base_url": os.environ.get("LLM_BASE_URL") or "https://api.openai.com/v1",
        "model": os.environ.get("LLM_MODEL") or "gpt-4o-mini",
    }
