"""任务状态与幂等：按输入指纹查已完成结果，避免重复保存。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional

from .types import TaskInput


def scenario_digest(scenario: Dict[str, Any]) -> str:
    """场景数据的版本指纹：数据一改，历史结果即失效。"""
    raw = json.dumps(
        scenario, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def fingerprint(task: TaskInput, provider: str = "mock", scenario_hash: str = "") -> str:
    """同一输入 + 同一决策来源 + 同一数据版本，才视为同一个任务。"""
    payload = {
        "topic": (task.topic or "").strip(),
        "budget": task.budget,
        "deadline": task.deadline,
        "provider": provider,
        "scenario": scenario_hash,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


class StateStore:
    """极简持久化：一个 JSON 文件保存已完成结果。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def get(self, fp: str) -> Optional[Dict[str, Any]]:
        return self._load().get(fp)

    def put(self, fp: str, record: Dict[str, Any]) -> None:
        data = self._load()
        data[fp] = record
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def delete(self, fp: str) -> None:
        data = self._load()
        if fp in data:
            data.pop(fp, None)
            self.path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
