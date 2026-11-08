"""Trace：一步一行 JSONL，字段 = 步骤 / 动作 / 工具 / 参数 / 返回摘要 / 决策依据。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .domain.models import StepRecord


class TraceWriter:
    def __init__(self, path: Optional[str]) -> None:
        self.path = Path(path) if path else None
        self._fp = None

    def __enter__(self) -> "TraceWriter":
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fp = self.path.open("w", encoding="utf-8")
        return self

    def write(self, record: StepRecord) -> None:
        if self._fp is None:
            return
        self._fp.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
        self._fp.flush()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fp is not None:
            self._fp.close()
            self._fp = None
