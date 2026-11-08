"""Trace 落盘：只写步骤/参数/结果摘要/决策依据，不写模型内部思维过程，不写密钥。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class Tracer:
    def __init__(self, path: "Path | None",
                 allowed_roots: "list[Path] | None" = None) -> None:
        self.path: "Path | None" = None
        self.records: "List[Dict[str, Any]]" = []
        if path is None:
            return

        target = Path(path)
        if allowed_roots:
            resolved = target.resolve()
            if not any(_within(resolved, Path(r).resolve()) for r in allowed_roots):
                roots = "、".join(str(Path(r)) for r in allowed_roots)
                raise ValueError(f"trace 路径必须位于 {roots} 之内，收到：{resolved}")
            target = resolved
        if target.exists() and target.is_dir():
            raise ValueError(f"trace 路径是一个目录：{target}")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("", encoding="utf-8")
        self.path = target

    def record(self, item: "Dict[str, Any]") -> None:
        self.records.append(item)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    def close(self) -> "Path | None":
        return self.path
