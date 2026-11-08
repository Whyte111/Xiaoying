"""配置加载：只从 .env / 环境变量读取，源码中不含任何密钥。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read_env_file(path: Path) -> "dict[str, str]":
    out: "dict[str, str]" = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


@dataclass
class Config:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    max_steps: int = 6
    top_k: int = 4
    timeout: float = 60.0
    data_dir: Path = field(default_factory=lambda: ROOT / "data")
    runs_dir: Path = field(default_factory=lambda: ROOT / "runs")

    @classmethod
    def load(cls, root: "Path | None" = None) -> "Config":
        root = Path(root) if root else ROOT
        env = _read_env_file(root / ".env")

        def pick(key: str, default: str) -> str:
            return os.environ.get(key) or env.get(key) or default

        return cls(
            api_key=pick("LLM_API_KEY", ""),
            base_url=pick("LLM_BASE_URL", "https://api.deepseek.com/v1"),
            model=pick("LLM_MODEL", "deepseek-chat"),
            max_steps=int(pick("MAX_STEPS", "6")),
            top_k=int(pick("TOP_K", "4")),
            timeout=float(pick("TIMEOUT", "60")),
            data_dir=root / "data",
            runs_dir=root / "runs",
        )

    def redact(self, text: str) -> str:
        """任何写日志/异常的地方都要过一次，避免密钥落盘。"""
        if self.api_key and self.api_key in text:
            return text.replace(self.api_key, "***")
        return text
