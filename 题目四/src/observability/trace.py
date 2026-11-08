"""Trace 写入器。

边界：trace 是**运行时产物**，由循环在每一步落盘，模型不参与记录，
因此不存在“事后手写 trace”的空间。写入前统一脱敏。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_SECRET_MARKS = ("api_key", "apikey", "token", "secret", "authorization", "password")


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if any(mark in str(k).lower() for mark in _SECRET_MARKS):
                out[k] = "***"
            else:
                out[k] = _sanitize(v)
        return out
    if isinstance(obj, list):
        return [_sanitize(x) for x in obj]
    return obj


class TraceWriter:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else None
        self.events: List[Dict[str, Any]] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def emit(self, event: Dict[str, Any]) -> Dict[str, Any]:
        ev = _sanitize(dict(event))
        ev.setdefault(
            "ts", datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        )
        self.events.append(ev)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        return ev
