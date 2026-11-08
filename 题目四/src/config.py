"""配置与数据加载。工具只读 data/，不写。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str) -> dict:
    path = ROOT / "data" / name
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class Ctx:
    """一次运行所需的只读上下文。"""

    materials: dict
    rules: dict
    platforms: dict

    def platform_cfg(self, name: str) -> dict:
        name = (name or "").strip()
        if name in self.platforms:
            return self.platforms[name]
        for key, cfg in self.platforms.items():
            if cfg.get("display_name") == name:
                return cfg
        return self.platforms.get("generic", {})


def load_ctx() -> Ctx:
    return Ctx(
        materials=_load("materials.json"),
        rules=_load("brand_rules.json"),
        platforms=_load("platforms.json"),
    )
