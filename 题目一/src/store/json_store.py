"""草稿存储：单文件 JSON + 原子写；幂等 upsert；失败可注入且可回滚。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..domain.models import Draft, ExistingSlot, SaveOutcome
from ..errors import StoreError


class JsonStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.fail_next = False
        self._data: Dict[str, Any] = self._load()

    # ---------- 持久化 ----------
    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"drafts": {}}
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StoreError("草稿库 %s 无法读取：%s" % (self.path, exc))
        if not raw.strip():
            return {"drafts": {}}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise StoreError(
                "草稿库 %s 不是合法 JSON（第 %d 行第 %d 列）。"
                "请修复，或备份后删除该文件重新开始。" % (self.path, exc.lineno, exc.colno)
            )
        if not isinstance(data, dict) or not isinstance(data.get("drafts", {}), dict):
            raise StoreError("草稿库 %s 结构非法：期望 {'drafts': {...}}" % self.path)
        data.setdefault("drafts", {})
        return data

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(self.path))

    # ---------- 读取 ----------
    def get_draft(self, material_id: str) -> Optional[Draft]:
        raw = self._data["drafts"].get(material_id)
        return Draft.from_dict(raw) if raw else None

    def draft_count(self) -> int:
        return len(self._data["drafts"])

    def all_drafts(self) -> List[Draft]:
        return [Draft.from_dict(v) for v in self._data["drafts"].values()]

    # ---------- 写入 ----------
    def upsert_draft(self, draft: Draft) -> SaveOutcome:
        if self.fail_next:
            self.fail_next = False
            return SaveOutcome(kind="failed", draft=None, error="injected save failure")

        existing = self.get_draft(draft.material_id)

        if existing is not None and existing.signature() == draft.signature():
            return SaveOutcome(kind="idempotent", draft=existing)

        if existing is not None:
            draft.revision = existing.revision + 1
            draft.created_at = existing.created_at
            kind = "updated"
        else:
            draft.revision = 1
            kind = "created"

        if not draft.created_at:
            draft.created_at = draft.updated_at
        draft.updated_at = draft.updated_at or draft.created_at

        # 写前快照：落盘失败必须回滚内存，否则后续会读到"没真正保存"的草稿
        previous = self._data["drafts"].get(draft.material_id)
        self._data["drafts"][draft.material_id] = draft.to_dict()
        try:
            self._write()
        except OSError as exc:
            if previous is None:
                self._data["drafts"].pop(draft.material_id, None)
            else:
                self._data["drafts"][draft.material_id] = previous
            return SaveOutcome(kind="failed", draft=None, error="write failed: %s" % exc)
        return SaveOutcome(kind=kind, draft=draft)

    # ---------- 排期只读表 ----------
    @staticmethod
    def load_existing(path: str) -> List[ExistingSlot]:
        try:
            raw = Path(path).read_text(encoding="utf-8")
            return [ExistingSlot.from_dict(item) for item in json.loads(raw)]
        except (OSError, json.JSONDecodeError, TypeError, KeyError) as exc:
            raise StoreError("已有排期 %s 无法读取：%s" % (path, exc))
